import os
import re
from flask import Flask, render_template, request
from models import db
from parsers import SCHEDULES_DIR, get_processed_data, get_all_schedules_info

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///schedules.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

with app.app_context():
    db.create_all()

@app.route('/')
def home():
    return render_template('home.html')

@app.route('/schedule')
def schedule():
    return render_schedule_page(schedule_type='classes')

@app.route('/exams')
def exams():
    return render_schedule_page(schedule_type='exams')

def render_schedule_page(schedule_type):
    # Инвалидация кэша: если файлы изменились — сбрасываем
    try:
        current_mtimes = {}
        if os.path.exists(SCHEDULES_DIR):
            for f in os.listdir(SCHEDULES_DIR):
                if (f.endswith('.xlsx') or f.endswith('.pdf')) and not f.startswith('~$'):
                    filepath = os.path.join(SCHEDULES_DIR, f)
                    current_mtimes[f] = os.path.getmtime(filepath)
        
        if not hasattr(render_schedule_page, '_last_mtimes') or render_schedule_page._last_mtimes != current_mtimes:
            render_schedule_page._last_mtimes = current_mtimes
            render_schedule_page._cached_info = get_all_schedules_info()
    except OSError:
        pass

    if hasattr(render_schedule_page, '_cached_info'):
        all_files, structured_groups = render_schedule_page._cached_info
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