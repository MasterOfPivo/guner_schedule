import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import re
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory
import io
from PyPDF2 import PdfReader
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import URLSafeTimedSerializer
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from models import db, User, Receipt
from parsers import SCHEDULES_DIR, get_processed_data, get_all_schedules_info
from dotenv import load_dotenv
import uuid

load_dotenv()

app = Flask(__name__)
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///schedules.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

GMAIL_SENDER = os.environ.get('GMAIL_SENDER', 'your-email@gmail.com')
GMAIL_CLIENT_ID = os.environ.get('GMAIL_CLIENT_ID', '')
GMAIL_CLIENT_SECRET = os.environ.get('GMAIL_CLIENT_SECRET', '')
GMAIL_REFRESH_TOKEN = os.environ.get('GMAIL_REFRESH_TOKEN', '')

def get_gmail_service():
    creds = Credentials(
        token=None,
        refresh_token=GMAIL_REFRESH_TOKEN,
        token_uri='https://oauth2.googleapis.com/token',
        client_id=GMAIL_CLIENT_ID,
        client_secret=GMAIL_CLIENT_SECRET,
        scopes=['https://www.googleapis.com/auth/gmail.send']
    )
    return build('gmail', 'v1', credentials=creds)
login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

db.init_app(app)

with app.app_context():
    db.create_all()

@app.route('/')
def home():
    return render_template('home.html')

@app.route('/schedule_hub')
def schedule_hub():
    return render_template('schedule_hub.html')

@app.route('/dorm_services')
def dorm_services():
    parsed_receipt = request.args.get('parsed_receipt')  # Will be handled via session or just re-rendered
    return render_template('dorm_services.html')

def parse_receipt_data(pdf_bytes):
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        text = ""
        for page in reader.pages:
            text += page.extract_text() + "\n"
            
        date_match = re.search(r'\b(\d{2}\.\d{2}\.\d{4})\b', text)
        date = date_match.group(1) if date_match else "Дата не найдена"
        
        amount_val = 0
        multiples = [7800 * i for i in range(1, 10)]
        for m in sorted(multiples, reverse=True):
            if str(m) in text.replace(' ', ''):
                amount_val = m
                break
                
        if amount_val == 0:
            amount_match = re.search(r'(?:Сумма|Итого).*?(?:[:\s])?([\d\s]+(?:[.,]\d+)?)\s*(?:руб|₽|RUB)', text, re.IGNORECASE)
            if not amount_match:
                amount_match = re.search(r'([\d\s]{4,}(?:[.,]\d{2})?)', text) 
            if amount_match:
                amount_str = amount_match.group(1).replace(' ', '').replace(',', '.')
                amount_val = float(amount_str)
                
        months_count = int(amount_val // 7800) if amount_val >= 7800 else 1
        excel_cells = []
        for _ in range(months_count):
            excel_cells.append(f"{date} 7800")
            
        excel_string = "\t".join(excel_cells)
        
        fio = "ФИО не найдено"
        text_flat = text.replace('\n', ' ')
        lines = text.split('\n')
        
        # 1. Сначала ищем по характерным окончаниям отчества (вич/вна/ич/ична) - работает в 99% случаев для РФ
        match_patronymic = re.search(r'\b([А-ЯЁ][а-яёА-ЯЁ]+)\s+([А-ЯЁ][а-яёА-ЯЁ]+)\s+([А-ЯЁ][а-яёА-ЯЁ]+(?:вич|вна|ич|ична))\b', text_flat)
        if match_patronymic:
            fio = match_patronymic.group(0).title()
        else:
            # 2. Если не нашли, ищем по ключевым словам (для иностранных студентов или нестандартных чеков)
            stop_words = r'(?i:Банк|ПАО|АО|ООО|БИК|Счет|ИНН|КПП|Росси|УГУ|ГУ|Отделение|Филиал|Управлени|ОГРН|К/С|Р/С|Корр|Бизнес)'
            
            for i, line in enumerate(lines):
                if re.search(r'(?i:\bПлательщик\b|\bФ\.?И\.?О\.?\b|\bОтправитель\b|\bКлиент\b)', line):
                    after_keyword = re.split(r'(?i:\bПлательщик\b|\bФ\.?И\.?О\.?\b|\bОтправитель\b|\bКлиент\b)', line)[-1]
                    matches = re.findall(r'([А-ЯЁ][а-яёА-ЯЁ]+(?:\s+[А-ЯЁ][а-яёА-ЯЁ]+){1,2})', after_keyword)
                    
                    found_valid = False
                    for m in matches:
                        if not re.search(stop_words, m) and not re.search(r'(?i:ФИО|Плательщик|Отправитель|Клиент)', m):
                            fio = m.title()
                            found_valid = True
                            break
                    if found_valid: break
                    
                    if i + 1 < len(lines):
                        next_line = lines[i+1]
                        matches_next = re.findall(r'([А-ЯЁ][а-яёА-ЯЁ]+(?:\s+[А-ЯЁ][а-яёА-ЯЁ]+){1,2})', next_line)
                        for m in matches_next:
                            if not re.search(stop_words, m) and not re.search(r'(?i:ФИО|Плательщик|Отправитель|Клиент)', m):
                                fio = m.title()
                                found_valid = True
                                break
                    if found_valid: break
        
        return {
            "fio": fio,
            "date": date,
            "amount": amount_val,
            "months": months_count,
            "excel_string": excel_string,
            "error": None
        }
    except Exception as e:
        return {"error": str(e)}

@app.route('/admin_receipts')
@login_required
def admin_receipts():
    if not current_user.is_admin:
        flash('У вас нет прав для этого действия', 'danger')
        return redirect(url_for('dorm_services'))
        
    receipts_data = []
    receipts_db = Receipt.query.order_by(Receipt.created_at.desc()).all()
    
    for r in receipts_db:
        parsed = {"error": "Файл не найден"}
        if os.path.exists(r.filename):
            with open(r.filename, 'rb') as f:
                parsed = parse_receipt_data(f.read())
                
        receipts_data.append({
            "id": r.id,
            "user": r.user.username,
            "period": r.period,
            "created_at": r.created_at.strftime("%d.%m.%Y %H:%M"),
            "parsed": parsed,
            "filename": os.path.basename(r.filename)
        })
        
    return render_template('admin_receipts.html', receipts=receipts_data)

@app.route('/uploads/receipts/<path:filename>')
@login_required
def serve_receipt(filename):
    if not current_user.is_admin:
        flash('У вас нет прав для этого действия', 'danger')
        return redirect(url_for('dorm_services'))
    return send_from_directory(os.path.join(app.root_path, 'uploads', 'receipts'), filename)

@app.route('/submit_receipt', methods=['GET', 'POST'])
@login_required
def submit_receipt():
    if request.method == 'POST':
        period = request.form.get('period')
        receipt = request.files.get('receipt')
        
        if not period or not receipt or receipt.filename == '':
            flash('Пожалуйста, заполните период и выберите файл.', 'danger')
            return redirect(url_for('submit_receipt'))
            
        filename = f"receipt_{current_user.id}_{uuid.uuid4().hex[:8]}.pdf"
        filepath = os.path.join('uploads', 'receipts', filename)
        receipt.save(filepath)
        
        new_receipt = Receipt(user_id=current_user.id, period=period, filename=filepath)
        db.session.add(new_receipt)
        db.session.commit()
        
        flash('Ваш чек за период "' + period + '" успешно отправлен на проверку!', 'success')
        return redirect(url_for('dorm_services'))
        
    return render_template('submit_receipt.html')

@app.route('/schedule')
def schedule():
    return render_schedule_page(schedule_type='classes')

@app.route('/exams')
def exams():
    return render_schedule_page(schedule_type='exams')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
        
    if request.method == 'POST':
        login_input = request.form.get('login', '')
        password = request.form.get('password', '')
        
        user = User.query.filter((User.username == login_input) | (User.email == login_input)).first()
        if user and check_password_hash(user.password_hash, password):
            if not user.is_confirmed:
                flash('Пожалуйста, подтвердите вашу почту перед входом.', 'warning')
                return redirect(url_for('login'))
            login_user(user)
            return redirect(url_for('home'))
        else:
            flash('Неверный логин или пароль.', 'danger')
            
    return render_template('login.html')

def send_confirmation_email(user_email):
    token = serializer.dumps(user_email, salt='email-confirm')
    confirm_url = url_for('confirm_email', token=token, _external=True)
    html = f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Подтверждение почты</title>
    </head>
    <body style="margin:0;padding:0;background:#f8faff;font-family:'Segoe UI',Arial,sans-serif;">
        <table width="100%" cellpadding="0" cellspacing="0" style="background:#f8faff;padding:40px 20px;">
            <tr>
                <td align="center">
                    <table width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;background:#ffffff;border-radius:24px;box-shadow:0 10px 40px rgba(108,92,231,0.12);overflow:hidden;">
                        <!-- Шапка -->
                        <tr>
                            <td align="center" style="background:linear-gradient(135deg,#6c5ce7 0%,#a29bfe 100%);padding:40px 40px 30px;">
                                <div style="font-size:2rem;margin-bottom:8px;">🎓</div>
                                <div style="font-size:1.6rem;font-weight:800;color:#ffffff;letter-spacing:-0.5px;">Гунер.рф</div>
                                <div style="font-size:0.85rem;color:rgba(255,255,255,0.75);margin-top:4px;">Неофициальный портал для учёбы и отдыха</div>
                            </td>
                        </tr>
                        <!-- Тело -->
                        <tr>
                            <td style="padding:40px;">
                                <h1 style="margin:0 0 16px;font-size:1.4rem;color:#2d3436;font-weight:700;">Подтверди свою почту ✉️</h1>
                                <p style="margin:0 0 24px;font-size:0.95rem;color:#636e72;line-height:1.6;">
                                    Привет! Ты только что зарегистрировался на <strong>Гунер.рф</strong>.<br>
                                    Нажми на кнопку ниже, чтобы подтвердить адрес электронной почты и активировать аккаунт.
                                </p>
                                <!-- Кнопка -->
                                <table width="100%" cellpadding="0" cellspacing="0">
                                    <tr>
                                        <td align="center" style="padding:8px 0 32px;">
                                            <a href="{confirm_url}"
                                               style="display:inline-block;padding:16px 40px;background:linear-gradient(135deg,#6c5ce7 0%,#a29bfe 100%);color:#ffffff;text-decoration:none;border-radius:14px;font-weight:700;font-size:1rem;box-shadow:0 8px 20px rgba(108,92,231,0.3);">
                                                ✅ Подтвердить почту
                                            </a>
                                        </td>
                                    </tr>
                                </table>
                                <p style="margin:0 0 8px;font-size:0.8rem;color:#b2bec3;">
                                    Если кнопка не работает, скопируй и вставь эту ссылку в браузер:
                                </p>
                                <p style="margin:0;font-size:0.78rem;word-break:break-all;">
                                    <a href="{confirm_url}" style="color:#6c5ce7;">{confirm_url}</a>
                                </p>
                            </td>
                        </tr>
                        <!-- Подвал -->
                        <tr>
                            <td style="padding:20px 40px;background:#f8faff;border-top:1px solid #e2e8f0;">
                                <p style="margin:0;font-size:0.78rem;color:#b2bec3;text-align:center;">
                                    Ссылка действует <strong>1 час</strong>. Если ты не регистрировался — просто проигнорируй это письмо. 🐾
                                </p>
                            </td>
                        </tr>
                    </table>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """
    mime_msg = MIMEMultipart('alternative')
    mime_msg['Subject'] = "Подтверждение почты - Гунер.рф"
    mime_msg['From'] = GMAIL_SENDER
    mime_msg['To'] = user_email
    mime_msg.attach(MIMEText(html, 'html', 'utf-8'))

    try:
        raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode('utf-8')
        service = get_gmail_service()
        service.users().messages().send(userId='me', body={'raw': raw}).execute()
        print(f"✅ Письмо отправлено на {user_email}")
    except Exception as e:
        import traceback
        print(f"❌ Не удалось отправить письмо: {type(e).__name__}: {e}")
        traceback.print_exc()
        print(f"🔗 ССЫЛКА ДЛЯ АКТИВАЦИИ (используй для теста): {confirm_url}")


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
        
    if request.method == 'POST':
        username = request.form.get('username', '')
        email = request.form.get('email', '')
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        
        if not username or not email or not password:
            flash('Пожалуйста, заполните все поля.', 'danger')
            return redirect(url_for('register'))
            
        if password != confirm_password:
            flash('Пароли не совпадают!', 'danger')
            return redirect(url_for('register'))
            
        existing_user = User.query.filter((User.username == username) | (User.email == email)).first()
        if existing_user:
            flash('Пользователь с таким именем или почтой уже существует.', 'danger')
            return redirect(url_for('register'))
            
        hashed_password = generate_password_hash(password)
        new_user = User(username=username, email=email, password_hash=hashed_password, is_confirmed=False)
        db.session.add(new_user)
        db.session.commit()
        
        send_confirmation_email(new_user.email)
        
        flash('Регистрация успешна! На вашу почту отправлено письмо для подтверждения.', 'success')
        return redirect(url_for('login'))
        
    return render_template('register.html')

@app.route('/confirm/<token>')
def confirm_email(token):
    try:
        email = serializer.loads(token, salt='email-confirm', max_age=3600)
    except:
        flash('Ссылка для подтверждения недействительна или устарела.', 'danger')
        return redirect(url_for('login'))
        
    user = User.query.filter_by(email=email).first()
    if not user:
        flash('Пользователь не найден.', 'danger')
        return redirect(url_for('register'))

    if user.is_confirmed:
        flash('Аккаунт уже подтвержден.', 'success')
    else:
        user.is_confirmed = True
        db.session.commit()
        flash('Ваш аккаунт успешно подтвержден! Теперь вы можете войти.', 'success')
        
    return redirect(url_for('login'))

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))

@app.route('/profile')
@login_required
def profile():
    return render_template('profile.html')

_SCHEDULE_CACHE = {}

def render_schedule_page(schedule_type):
    # Инвалидация кэша: если файлы изменились — сбрасываем
    try:
        current_mtimes = {}
        if os.path.exists(SCHEDULES_DIR):
            for f in os.listdir(SCHEDULES_DIR):
                if (f.endswith('.xlsx') or f.endswith('.pdf')) and not f.startswith('~$'):
                    filepath = os.path.join(SCHEDULES_DIR, f)
                    current_mtimes[f] = os.path.getmtime(filepath)
        
        if _SCHEDULE_CACHE.get('last_mtimes') != current_mtimes:
            _SCHEDULE_CACHE['last_mtimes'] = current_mtimes
            _SCHEDULE_CACHE['info'] = get_all_schedules_info()
    except OSError:
        pass

    if 'info' in _SCHEDULE_CACHE:
        all_files, structured_groups = _SCHEDULE_CACHE['info']
    else:
        all_files, structured_groups = get_all_schedules_info()
        
    if schedule_type == 'classes':
        available_files = [f for f in all_files if f.endswith('.xlsx') and 'экзамен' not in f.lower()]
        page_title = 'Расписание занятий'
        time_col_name = 'Время'
    else:
        available_files = [f for f in all_files if 'экзамен' in f.lower() or f.endswith('.pdf')]
        page_title = 'Расписание экзаменов'
        time_col_name = 'Дата'
    
    if not available_files:
        return f"<h1>Ой-ой! Нет файлов расписания! 😿</h1><p>Положи файлы .xlsx в папку {SCHEDULES_DIR}.</p>", 404

    selected_group = request.args.get('group')
    selected_file = request.args.get('file')
    show_empty = request.args.get('show_empty', request.cookies.get('show_empty', '0')) == '1'

    if selected_group and not selected_file:
        for f in available_files:
            _, g_map = get_processed_data(f)
            if g_map and selected_group in g_map:
                selected_file = f
                break
        if not selected_file:
            selected_file = available_files[0]

    if not selected_group:
        return render_template('index.html', 
                               groups_by_course=structured_groups, 
                               selected_group=None,
                               page_title=page_title,
                               schedule_type=schedule_type,
                               show_empty=show_empty)

    schedule_data, group_mapping = get_processed_data(selected_file)
    
    if schedule_data is None:
        return f"<h1>Ошибка чтения файла {selected_file} 😿</h1>", 500

    if group_mapping is None:
        group_mapping = {}

    # Очищаем group_mapping от полностью пустых колонок
    if group_mapping and selected_group in group_mapping:
        active_cols = []
        for c in group_mapping[selected_group]:
            has_value = False
            for row in schedule_data:
                v = row['cells_info'].get(c, {}).get('value', '')
                if v and v.strip():
                    has_value = True
                    break
            if has_value:
                active_cols.append(c)
        if not active_cols:
            active_cols = [group_mapping[selected_group][0]]
        group_mapping[selected_group] = active_cols

    groups_to_show = [selected_group] if group_mapping and selected_group in group_mapping else []
    
    rows_to_render = []
    
    # Проверяем, есть ли хоть одна пара на всей неделе
    has_any_class_on_week = False
    if selected_group:
        for row in schedule_data:
            for g_name in groups_to_show:
                for c in group_mapping[g_name]:
                    if row['cells_info'][c]['value']:
                        has_any_class_on_week = True
                        break
                if has_any_class_on_week:
                    break
                    
    if not selected_group or has_any_class_on_week:
        for row in schedule_data:
            has_any_class = False
            for g_name in groups_to_show:
                for c in group_mapping[g_name]:
                    if row['cells_info'][c]['value']:
                        has_any_class = True
                        break
                if has_any_class:
                    break
            
            # Убираем год из даты (напр. 12.05.2026 → 12.05)
            day_display = re.sub(r'\.\d{4}', '', row['day']).replace('\n', '<br>')
            
            row_data = {
                'day': day_display,
                'time': row['time'],
                'cells': [],
                'is_empty': not has_any_class
            }
            
            # 2. Формируем ячейки с учетом объединений из Excel
            for g_name in groups_to_show:
                col_indices = group_mapping[g_name]
                vals = []
                is_merged_across = False
                
                first_col = col_indices[0]
                last_col = col_indices[-1]
                
                first_cell_info = row['cells_info'][first_col]
                # 🐾 Если ячейка объединена от первой до последней колонки подгруппы - это общая лекция!
                if first_cell_info['merged_min'] <= first_col and first_cell_info['merged_max'] >= last_col:
                    is_merged_across = True
                    
                for c in col_indices:
                    v = row['cells_info'][c]['value']
                    v_clean = re.sub(r'\s+', ' ', v).strip()
                    vals.append(v_clean)
                    
                if not is_merged_across and len(col_indices) > 1:
                    if vals[0] and any(ind in vals[0].lower() for ind in ['(лек)', 'лекция', 'лек.']):
                        if all(v == "" for v in vals[1:]):
                            is_merged_across = True
                            
                if all(v == "" for v in vals):
                    cell_html = "<div class='window-slot'><i class='fa-solid fa-mug-hot'></i>Отдых</div>" # Окно (пар нет)
                elif is_merged_across:
                    # Выводим общую пару на всю ширину без полосок
                    cell_html = f"<div class='single-class'>{vals[0]}</div>"
                else:
                    # Разделяем на колонки с пунктиром!
                    cols_html = []
                    for i, v in enumerate(vals):
                        border = "border-right: 2px dashed var(--border);" if i < len(vals) - 1 else ""
                        content = v if v != "" else "<div style='display: flex; align-items: center; justify-content: center; height: 100%; color: var(--divider); opacity: 0.4; font-size: 0.8rem;'><i class='fa-solid fa-mug-hot'></i></div>"
                        cols_html.append(f"<div class='subgroup-column' style='{border}'>{content}</div>")
                    
                    cell_html = f"<div class='subgroup-container'>{''.join(cols_html)}</div>"
                    
                row_data['cells'].append(cell_html)
                
            rows_to_render.append(row_data)

    # 3. Рассчитываем rowspan для объединения ячеек "Дня недели"
    for i, row in enumerate(rows_to_render):
        if i > 0 and rows_to_render[i]['day'] == rows_to_render[i-1]['day']:
            rows_to_render[i]['rowspan'] = 0
        else:
            count = 1
            for j in range(i + 1, len(rows_to_render)):
                if rows_to_render[j]['day'] == rows_to_render[i]['day']:
                    count += 1
                else:
                    break
            rows_to_render[i]['rowspan'] = count

    file_tabs = []
    for f in available_files:
        pretty_name = f.replace('.xlsx', '').replace('Расписание ', '').replace('Расписание', '').strip()
        file_tabs.append({'filename': f, 'name': pretty_name})

    return render_template('index.html', 
                                 groups_by_course=structured_groups, 
                                 selected_group=selected_group, 
                                 selected_file=selected_file,
                                 file_tabs=file_tabs,
                                 rows=rows_to_render,
                                 header_groups=groups_to_show,
                                 page_title=page_title,
                                 time_col_name=time_col_name,
                                 schedule_type=schedule_type,
                                 show_empty=show_empty)

if __name__ == '__main__':
    app.run(host='0.0.0.0', debug=True, threaded=True)