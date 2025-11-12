import sqlite3
import os
# Импортируем список вопросов из соседнего файла
from extra_questions import all_quiz_questions

# --- КОНСТАНТЫ ---
# Путь к файлу базы данных, как указано в bot.py
DB_PATH = "database.db"

def initialize_database():
    """
    Создает таблицы 'users' и 'questions' (если они не существуют)
    и заполняет таблицу 'questions' данными из списка all_quiz_questions.
    
    ПРИМЕЧАНИЕ: Этот скрипт удаляет старый файл базы данных (database.db)
    для обеспечения чистого старта.
    """
    
    # 1. Удаление старого файла DB для чистого старта
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        print(f"Старый файл базы данных '{DB_PATH}' удален.")

    # 2. Подключение и создание таблиц
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Создание таблицы пользователей
    cur.execute("""
        CREATE TABLE users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            score INTEGER DEFAULT 0
        )
    """)

    # Создание таблицы вопросов
    cur.execute("""
        CREATE TABLE questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT NOT NULL,
            answer TEXT NOT NULL
        )
    """)
    
    # 3. Массовая вставка вопросов
    print(f"Добавление {len(all_quiz_questions)} вопросов в таблицу 'questions'...")
    try:
        cur.executemany(
            "INSERT INTO questions (question, answer) VALUES (?, ?)", 
            all_quiz_questions
        )
        conn.commit()
        print("✅ Вопросы успешно добавлены!")
    except Exception as e:
        print(f"❌ Ошибка при добавлении вопросов: {e}")
        conn.rollback()
        
    conn.close()
    print("Инициализация базы данных завершена.")

if __name__ == "__main__":
    initialize_database()