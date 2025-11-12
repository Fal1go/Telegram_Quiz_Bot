import sqlite3

DB_PATH = "database.db"

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# Create tables
cur.execute("""
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT NOT NULL,
    answer TEXT NOT NULL
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    score INTEGER DEFAULT 0
)
""")

# Sample questions
sample_questions = [
    ("Кем приходился Пётр II Петру I?", "внук"),
    ("Начальник варяжского отряда в Новгороде?", "рюрик"),
    ("Xpaнилищe для пacпopтa (Мaякoвcк.)?", "портфель")
]

cur.executemany("INSERT INTO questions (question, answer) VALUES (?, ?)", sample_questions)

conn.commit()
conn.close()

print("Database initialized successfully.")
