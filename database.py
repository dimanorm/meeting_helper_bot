import sqlite3

DB_PATH = 'meetings.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Таблицы
    c.execute('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, tg_id INTEGER, name TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS meetings (id INTEGER PRIMARY KEY, title TEXT, start_dt TEXT, end_dt TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS meeting_participants (meeting_id INTEGER, user_id INTEGER, FOREIGN KEY(meeting_id) REFERENCES meetings(id), FOREIGN KEY(user_id) REFERENCES users(id))''')
    
    # Сидинг базы (заполнение при первом запуске)
    c.execute("SELECT COUNT(*) FROM users")
    if c.fetchone()[0] == 0:
        initial_users = [
            (1, None, 'Иван'), (2, None, 'Анна'), 
            (3, None, 'Петр'), (4, None, 'Семен'), (5, None, 'Андрей')
        ]
        c.executemany("INSERT INTO users (id, tg_id, name) VALUES (?, ?, ?)", initial_users)
    conn.commit()
    conn.close()

def get_user_by_tg(tg_id: int):
    """Поиск имени сотрудника по Telegram ID"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT name FROM users WHERE tg_id = ?", (tg_id,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else None

def register_user(tg_id: int, name: str) -> bool:
    """Привязка Telegram ID к имени сотрудника"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE name = ?", (name,))
    if not c.fetchone():
        conn.close()
        return False
    c.execute("UPDATE users SET tg_id = ? WHERE name = ?", (tg_id, name))
    conn.commit()
    conn.close()
    return True

def get_users_ids_by_names(names: list):
    """Возвращает словарь {имя: id} для найденных сотрудников"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    placeholders = ','.join('?' for _ in names)
    c.execute(f"SELECT id, name FROM users WHERE name IN ({placeholders})", tuple(names))
    results = c.fetchall()
    conn.close()
    return {name: uid for uid, name in results}

def check_collision_and_book(user_ids: list, start_dt: str, end_dt: str):
    """
    Проверяет пересечения. Если свободно — бронирует.
    Формат дат: 'YYYY-MM-DD HH:MM:SS'
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    placeholders = ','.join('?' for _ in user_ids)
    
    # Поиск конфликтов
    query = f'''
        SELECT u.name FROM meetings m
        JOIN meeting_participants mp ON m.id = mp.meeting_id
        JOIN users u ON mp.user_id = u.id
        WHERE mp.user_id IN ({placeholders})
        AND (m.start_dt < ? AND m.end_dt > ?)
    '''
    params = tuple(user_ids) + (end_dt, start_dt)
    c.execute(query, params)
    conflicts = c.fetchall()
    
    if conflicts:
        conn.close()
        return False, [conflict[0] for conflict in conflicts]
        
    # Бронирование, если конфликтов нет
    c.execute("INSERT INTO meetings (title, start_dt, end_dt) VALUES (?, ?, ?)", ("Встреча", start_dt, end_dt))
    meeting_id = c.lastrowid
    
    for uid in user_ids:
        c.execute("INSERT INTO meeting_participants (meeting_id, user_id) VALUES (?, ?)", (meeting_id, uid))
        
    conn.commit()
    conn.close()
    return True, []