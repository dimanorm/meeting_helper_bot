import sqlite3

DB_PATH = 'meetings.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Добавили поле color_emoji
    c.execute('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, tg_id INTEGER, name TEXT, color_emoji TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS meetings (id INTEGER PRIMARY KEY, title TEXT, start_dt TEXT, end_dt TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS meeting_participants (meeting_id INTEGER, user_id INTEGER, FOREIGN KEY(meeting_id) REFERENCES meetings(id), FOREIGN KEY(user_id) REFERENCES users(id))''')
    
    c.execute("SELECT COUNT(*) FROM users")
    if c.fetchone()[0] == 0:
        # У каждого юзера теперь свой цветной маркер
        initial_users = [
            (1, None, 'Иван', '🔴'), 
            (2, None, 'Анна', '🔵'), 
            (3, None, 'Михаил', '🟢'), 
            (4, None, 'Антон', '🟡'), 
            (5, None, 'Андрей', '🟣')
        ]
        c.executemany("INSERT INTO users (id, tg_id, name, color_emoji) VALUES (?, ?, ?, ?)", initial_users)
    conn.commit()
    conn.close()

# Новая функция для получения расписания
def get_upcoming_meetings():
    """Возвращает список встреч, отсортированных по дате и времени начала."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    query = '''
        SELECT m.id, m.start_dt, m.end_dt, u.name, u.color_emoji
        FROM meetings m
        JOIN meeting_participants mp ON m.id = mp.meeting_id
        JOIN users u ON mp.user_id = u.id
        ORDER BY m.start_dt ASC, m.id ASC
    '''
    c.execute(query)
    rows = c.fetchall()
    conn.close()
    
    # Группируем данные: {meeting_id: {'time': '...', 'participants': [('Иван', '🔴'), ...]}}
    schedule = {}
    for row in rows:
        m_id, start, end, name, emoji = row
        if m_id not in schedule:
            # Преобразуем формат даты для красоты вывода
            # Из '2026-07-09 14:00:00' делаем '09.07 14:00-15:00'
            date_str = start[:10]  # '2026-07-09'
            time_start = start[11:16] # '14:00'
            time_end = end[11:16] # '15:00'
            
            # Если день совпадает, выводим красиво
            formatted_time = f"{date_str[8:10]}.{date_str[5:7]} {time_start}-{time_end}"
            
            schedule[m_id] = {
                'time': formatted_time,
                'participants': []
            }
        schedule[m_id]['participants'].append(f"{emoji} {name}")
        
    return schedule

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


def delete_meeting(meeting_id: int) -> bool:
    """Удаляет встречу и связи с участниками. Возвращает True если удалено."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Проверяем, существует ли она
    c.execute("SELECT id FROM meetings WHERE id = ?", (meeting_id,))
    if not c.fetchone():
        conn.close()
        return False
        
    c.execute("DELETE FROM meeting_participants WHERE meeting_id = ?", (meeting_id,))
    c.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))
    conn.commit()
    conn.close()
    return True


def get_unregistered_users():
    """Возвращает список имен сотрудников, у которых еще нет привязанного tg_id"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT name FROM users WHERE tg_id IS NULL")
    users = [row[0] for row in c.fetchall()]
    conn.close()
    return users

def get_tg_ids_by_names(names: list):
    """Возвращает словарь {имя: tg_id} для участников, которые уже зарегистрированы в боте"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    placeholders = ','.join('?' for _ in names)
    # Выбираем только тех, у кого tg_id не NULL
    c.execute(f"SELECT name, tg_id FROM users WHERE name IN ({placeholders}) AND tg_id IS NOT NULL", tuple(names))
    results = c.fetchall()
    conn.close()
    return {name: tg_id for name, tg_id in results}
