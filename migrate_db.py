"""
Скрипт миграции БД: добавляет колонки status и comment в таблицу receipt,
если их ещё нет.
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), 'instance', 'schedules.db')

def migrate():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(receipt)")
    columns = {row[1] for row in cur.fetchall()}
    print(f"Текущие колонки: {columns}")

    changed = False

    if 'status' not in columns:
        cur.execute("ALTER TABLE receipt ADD COLUMN status VARCHAR(50) DEFAULT 'На проверке'")
        print("✅ Колонка 'status' добавлена")
        changed = True
    else:
        print("ℹ️  Колонка 'status' уже существует")

    if 'comment' not in columns:
        cur.execute("ALTER TABLE receipt ADD COLUMN comment TEXT")
        print("✅ Колонка 'comment' добавлена")
        changed = True
    else:
        print("ℹ️  Колонка 'comment' уже существует")

    if changed:
        conn.commit()
        print("✅ Миграция завершена успешно!")
    else:
        print("ℹ️  Миграция не требуется — все колонки уже на месте.")

    conn.close()

if __name__ == '__main__':
    migrate()
