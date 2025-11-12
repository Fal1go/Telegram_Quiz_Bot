import random
import sqlite3
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler,
    JobQueue
)

# ——— CONSTANTS ———
TOKEN = "8413024991:AAFkOY2gi0SQ7_xRltTNJtSeuEIbNtDCXZg"
DB_PATH = "database.db"
ADMIN_ID = 918967275 # Make sure this is your ID

# ——— Quiz state and JobQueue ———
# Keys are f"user_{user_id}" for personal quizzes, f"chat_{chat_id}" for collective quizzes.
quiz_state = {} 
QUIZ_MODE_PERSONAL = "personal"
QUIZ_MODE_GROUP = "group"

# ——— UTILS ———
def escape_markdown(text):
    """Helper function to escape characters in Markdown V1 mode."""
    # Escape all characters that have meaning in Markdown V1
    if not isinstance(text, str):
        text = str(text)
    return text.replace('_', '\\_').replace('*', '\\*').replace('`', '\\`').replace('[', '\\[').replace(']', '\\]').replace('(', '\\(').replace(')', '\\)').replace('~', '\\~').replace('>', '\\>').replace('#', '\\#').replace('+', '\\+').replace('-', '\\-').replace('=', '\\=').replace('|', '\\|').replace('{', '\\{').replace('}', '\\}').replace('.', '\\.').replace('!', '\\!')


# ——— DATABASE OPERATIONS ——
def get_random_question():
    """Gets one random question from the DB."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, question, answer FROM questions ORDER BY RANDOM() LIMIT 1;")
    q = cur.fetchone()
    conn.close()
    if q:
        return {"id": q[0], "question": q[1], "answer": q[2]}
    return None

def register_user(user_id, username):
    """
    Registers the user or updates the name.
    If the user exists, only the username is updated, score is preserved.
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # Use INSERT OR REPLACE with a subquery to preserve the score if the user already exists
    cur.execute(
        "INSERT OR REPLACE INTO users (user_id, username, score) VALUES (?, ?, (SELECT score FROM users WHERE user_id = ?))",
        (user_id, username, user_id)
    )
    conn.commit()
    conn.close()

def update_score(user_id, delta):
    """Updates the user's score."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE users SET score = score + ? WHERE user_id = ?", (delta, user_id))
    conn.commit()
    conn.close()

def get_leaderboard(limit=5):
    """Returns the list of leaders."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT username, score FROM users ORDER BY score DESC LIMIT ?", (limit,))
    result = cur.fetchall()
    conn.close()
    return result

def get_user_score(user_id):
    """Returns the user's current score."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT score FROM users WHERE user_id = ?", (user_id,))
    result = cur.fetchone()
    conn.close()
    # Returns score (integer) or 0 if not found
    return result[0] if result else 0

def get_all_questions():
    """Returns all questions with their IDs and answers."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, question, answer FROM questions ORDER BY id ASC;")
    result = cur.fetchall()
    conn.close()
    return result

# —— STATE AND JOB MANAGEMENT ———

def get_quiz_key(uid, chat_id, mode):
    """Generates a unique key for the quiz state based on mode."""
    if mode == QUIZ_MODE_PERSONAL:
        return f"user_{uid}"
    elif mode == QUIZ_MODE_GROUP:
        return f"chat_{chat_id}"
    return None

def extract_quiz_key_from_job_name(job_name):
    """Extracts the quiz key from a job's name."""
    # Job names are f"job_type_{quiz_key}" e.g., "job_timeout_user_918967275"
    parts = job_name.split("_", 2)
    return parts[2] if len(parts) == 3 else None

def get_job_names(quiz_key):
    """Returns all job names associated with a quiz key."""
    return [
        f"job_format_{quiz_key}",
        f"job_hint_1_{quiz_key}",
        f"job_timeout_{quiz_key}",
    ]

def cancel_quiz_jobs(quiz_key, context):
    """Cancels all active jobs for the specified quiz key."""
    for job_name in get_job_names(quiz_key):
        current_jobs = context.job_queue.get_jobs_by_name(job_name)
        for job in current_jobs:
            job.schedule_removal()

def find_active_quiz_key(uid, chat_id):
    """
    Finds an active quiz key for the user (personal) or chat (group).
    Prioritizes personal quiz for the user.
    """
    # 1. Check for personal quiz started by this user in this chat
    personal_key = get_quiz_key(uid, chat_id, QUIZ_MODE_PERSONAL)
    if personal_key in quiz_state and quiz_state[personal_key]["chat_id"] == chat_id:
        return personal_key, QUIZ_MODE_PERSONAL

    # 2. Check for group quiz in this chat
    group_key = get_quiz_key(None, chat_id, QUIZ_MODE_GROUP)
    if group_key in quiz_state:
        return group_key, QUIZ_MODE_GROUP
        
    return None, None

# —— HINT/JOB LOGIC ———

async def give_hint_by_random_letter(quiz_key, chat_id, context, is_manual=False):
    """Hint logic: revealing one random hidden letter."""
    state = quiz_state.get(quiz_key)
    if not state:
        return

    answer = state["question"]["answer"]
    revealed = state["revealed"] # Array of symbols/placeholders

    # Reveal one random letter that is still hidden ("_")
    hidden_indexes = [i for i, c in enumerate(revealed) if c == "_"]
    
    if not hidden_indexes:
        # All letters are already revealed, no more hints possible
        return 

    idx = random.choice(hidden_indexes)
    # Reveal the letter
    revealed[idx] = answer[idx]
    state["hints_used"] += 1
    
    hint_type_text = "Ручная подсказка" if is_manual else "Подсказка"
    mode_tag = " [Личный]" if state["mode"] == QUIZ_MODE_PERSONAL else " [Групповой]"

    # Send the message
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"💡 {hint_type_text}{mode_tag}: {' '.join(revealed)}"
    )
            
async def send_answer_format_job_callback(context: ContextTypes.DEFAULT_TYPE):
    """Sends the veiled answer format after 30 seconds."""
    quiz_key = extract_quiz_key_from_job_name(context.job.name)
    chat_id = context.job.chat_id
    state = quiz_state.get(quiz_key)
    if not state: return

    mode_tag = " [Личный]" if state["mode"] == QUIZ_MODE_PERSONAL else " [Групповой]"
    
    # Send the message with the format (e.g., _ _ _ _ _)
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"📝 Формат ответа{mode_tag} (через {len(state['revealed'])} букв): {' '.join(state['revealed'])}"
    )

async def send_first_hint_job_callback(context: ContextTypes.DEFAULT_TYPE):
    """Sends the first hint after 50 seconds."""
    quiz_key = extract_quiz_key_from_job_name(context.job.name)
    chat_id = context.job.chat_id
    
    await give_hint_by_random_letter(quiz_key, chat_id, context, is_manual=False)

async def quiz_timeout_job_callback(context: ContextTypes.DEFAULT_TYPE):
    """Reveals the answer and moves to the next question after 60 seconds."""
    quiz_key = extract_quiz_key_from_job_name(context.job.name)
    chat_id = context.job.chat_id
    
    state = quiz_state.get(quiz_key)
    if not state: return
        
    correct_answer = state["question"]["answer"]
    mode_tag = " [Личный]" if state["mode"] == QUIZ_MODE_PERSONAL else " [Групповой]"
    
    # Enhanced string
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"⌛ Время вышло!{mode_tag} Никто не угадал. Правильный ответ: *{correct_answer}* 😭",
        parse_mode='Markdown'
    )
    
    # Move to the next question immediately after timeout
    await proceed_to_next_question(quiz_key, chat_id, context)

# ——— COMMANDS ———

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a message with all available commands and their descriptions."""
    help_text = (
        "📚 *СПИСОК КОМАНД ВИКТОРИНЫ* 🧐\n\n"
        "--- *Для всех пользователей* ---\n"
        "*/start* — Приветствие и регистрация.\n"
        "*/quiz* — Начать новую серию вопросов. **Сначала предлагает выбрать режим (личный/групповой), затем количество вопросов.**\n"
        "*/setname Имя* — ✏️ Сменить имя, отображаемое в таблице лидеров.\n"
        "*/stop* — Остановить *твою* личную или *начатую тобой* групповую викторину.\n"
        "*/hint* — 💡 Получить ручную подсказку, раскрыв одну случайную букву. Доступно всем в групповом режиме.\n"
        "*/skip* — ➡️ Пропустить текущий вопрос. Только для инициатора викторины.\n"
        "*/top* — 👑 Посмотреть таблицу лидеров.\n"
        "*/help* — Показать это сообщение со списком команд.\n"
        "*/removekeyboard* — ✖️ Убрать постоянную клавиатуру с командами.\n\n"
        "--- *Для администратора* ---\n"
        "*/add Вопрос?;Ответ* — 💾 Добавить новый вопрос в базу данных.\n"
        "*/delete ID* — 🗑 Удалить вопрос из базы данных по его ID.\n"
        "*/showall* — 📋 Показать все вопросы, их ID и ответы."
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

def get_user_display_name(user):
    """Constructs the user's full name, prioritizing First/Last Name."""
    full_name = user.first_name or "Неизвестный пользователь"
    if user.last_name:
        full_name += f" {user.last_name}"
    # Use username only as a last resort if first/last names are not set
    elif not user.first_name and user.username:
        full_name = user.username
    return full_name

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    full_name = get_user_display_name(user)
    register_user(user.id, full_name)
    
    # --- Create the new, styled Reply Keyboard ---
    keyboard = [
        [KeyboardButton("🚀 Начать игру /quiz")], # Beautifully styled button
        [KeyboardButton("/help"), KeyboardButton("/top")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    
    # Enhanced string
    await update.message.reply_text(
        f"👋 Добро пожаловать, {user.first_name}! 🚀\nГотов проверить свои знания? Нажми 'Начать игру' или /help для списка команд!",
        reply_markup=reply_markup # Attach the new keyboard
    )

async def set_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Allows a user to set a custom display name on the leaderboard."""
    uid = update.effective_user.id
    
    if not context.args:
        await update.message.reply_text(
            "Использование: /setname *Ваше новое имя*\n\n"
            "Пример: `/setname Кот Учёный`", 
            parse_mode='Markdown'
        )
        return
    
    new_name = " ".join(context.args).strip()
    
    # Simple check for name length
    if len(new_name) < 2 or len(new_name) > 30:
        await update.message.reply_text("🚫 Имя должно быть от 2 до 30 символов.")
        return

    # Update the name in the database
    register_user(uid, new_name)
    
    await update.message.reply_text(
        f"✅ Твое имя на лидерборде успешно обновлено на: *{new_name}*",
        parse_mode='Markdown'
    )


async def remove_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Removes the custom reply keyboard."""
    await update.message.reply_text(
        "Клавиатура команд удалена. Нажмите /start, чтобы вернуть её.",
        reply_markup=ReplyKeyboardRemove()
    )

async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    
    # Update registration logic here too, in case /quiz is called before /start
    user = update.effective_user
    full_name = get_user_display_name(user)
    register_user(uid, full_name)
    
    # New: Ask for the mode first
    keyboard = [
        [InlineKeyboardButton("👤 Личный режим (Твой прогресс)", callback_data="mode_personal")],
        [InlineKeyboardButton("👥 Групповой режим (Для этого чата)", callback_data="mode_group")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🧐 Выбери режим викторины:", reply_markup=reply_markup)


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    chat_id = query.message.chat_id
    data = query.data

    if data.startswith("mode_"):
        # Step 1: Mode selection
        mode = data.split("_")[1]
        
        # Store selected mode temporarily in user_data or context.user_data
        context.user_data["quiz_mode"] = mode
        
        keyboard = [
            [InlineKeyboardButton("10 Вопросов 📝", callback_data=f"start_{mode}_10")],
            [InlineKeyboardButton("20 Вопросов 📚", callback_data=f"start_{mode}_20")],
            [InlineKeyboardButton("50 Вопросов 🔥", callback_data=f"start_{mode}_50")],
            [InlineKeyboardButton("♾️ Безлимитно", callback_data=f"start_{mode}_-1")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Отлично! Сколько вопросов ты хочешь решить?", reply_markup=reply_markup)
        
    elif data.startswith("start_"):
        # Step 2: Duration selection
        parts = data.split("_") # start_mode_total
        mode = parts[1]
        total = int(parts[2])

        # Define the quiz key based on the selected mode
        quiz_key = get_quiz_key(uid, chat_id, mode)

        # Cancel any previous active quiz for this key
        if quiz_key and quiz_key in quiz_state:
            cancel_quiz_jobs(quiz_key, context)
            del quiz_state[quiz_key] # Clean up state

        # Start the quiz
        await start_quiz(quiz_key, uid, chat_id, context, total, current_asked=1, mode=mode)
        
        question_count_text = 'Безлимитная серия' if total == -1 else f'{total} вопросов'
        mode_text = 'Личный' if mode == QUIZ_MODE_PERSONAL else 'Групповой'
        
        await query.edit_message_text(text=f"✅ Отлично! Начинаем {mode_text} режим ({question_count_text})! Удачи!")


async def start_quiz(quiz_key, starter_uid, chat_id, context, total, current_asked=1, mode=QUIZ_MODE_PERSONAL):
    """Initializes the quiz state and sends the first (or subsequent) question."""
    q = get_random_question()
    if not q:
        await context.bot.send_message(chat_id=chat_id, text="⚠️ В базе данных нет вопросов. Попросите администратора их добавить.")
        return
        
    # Cancel the old jobs for this specific key
    cancel_quiz_jobs(quiz_key, context)
    
    # Initialize or refresh state
    quiz_state[quiz_key] = {
        "mode": mode, # Store the mode
        "chat_id": chat_id, # Store the chat_id for group mode
        "question": q,
        "hints_used": 0,
        "total_questions": total,
        "asked": current_asked,
        "revealed": ["_"] * len(q["answer"]),
        "starter_uid": starter_uid # The only user authorized to stop/skip this specific series
    }
    state = quiz_state[quiz_key]

    # --- Job Scheduling (3 events) ---
    # We use chat_id for sending the message and the unique quiz_key for the job name
    context.job_queue.run_once(
        send_answer_format_job_callback, 
        30, 
        chat_id=chat_id, 
        user_id=starter_uid, 
        name=f"job_format_{quiz_key}"
    )
    
    context.job_queue.run_once(
        send_first_hint_job_callback, 
        50, 
        chat_id=chat_id, 
        user_id=starter_uid, 
        name=f"job_hint_1_{quiz_key}"
    )

    context.job_queue.run_once(
        quiz_timeout_job_callback, 
        60, 
        chat_id=chat_id, 
        user_id=starter_uid, 
        name=f"job_timeout_{quiz_key}"
    )

    # Initial message
    mode_text = 'Личный' if mode == QUIZ_MODE_PERSONAL else 'Групповой'
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🧠 *[{mode_text}]* Вопрос {state['asked']}/{'∞' if total == -1 else total}:\n\n*{q['question']}*",
        parse_mode='Markdown'
    )

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    quiz_key, mode = find_active_quiz_key(uid, chat_id)
    
    state = quiz_state.get(quiz_key)
    
    if not state:
        await update.message.reply_text("Нет активной викторины для остановки в этом чате или для тебя лично.")
        return
        
    # Authorization check: only the starter can stop
    if uid != state["starter_uid"]:
        mode_text = 'Личную' if mode == QUIZ_MODE_PERSONAL else 'Групповую'
        await update.message.reply_text(f"🚫 Ты не можешь остановить эту {mode_text} викторину, так как её начал другой пользователь.")
        return

    # Stop and clean up
    cancel_quiz_jobs(quiz_key, context)
    del quiz_state[quiz_key]
    mode_text = 'Личная' if mode == QUIZ_MODE_PERSONAL else 'Групповая'
    await update.message.reply_text(f"👋 {mode_text} викторина успешно завершена. Ждём тебя снова!")


async def skip_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Skips the current question and proceeds to the next one."""
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    quiz_key, mode = find_active_quiz_key(uid, chat_id)
    
    state = quiz_state.get(quiz_key)

    if not state:
        await update.message.reply_text("Сначала начни викторину с /quiz 🧩")
        return
        
    # Authorization check: only the starter can skip
    if uid != state["starter_uid"]:
        mode_text = 'Личную' if mode == QUIZ_MODE_PERSONAL else 'Групповую'
        await update.message.reply_text(f"🚫 Ты не можешь пропустить вопрос в этой {mode_text} викторине, так как её начал другой пользователь.")
        return

    # Announce skip
    await update.message.reply_text("➡️ Вопрос пропущен.")
    
    # Cancel old timers for the current question
    cancel_quiz_jobs(quiz_key, context) 

    # Go to the next question
    await proceed_to_next_question(quiz_key, chat_id, context)


async def hint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    quiz_key, mode = find_active_quiz_key(uid, chat_id)
    
    state = quiz_state.get(quiz_key)
    
    if not state:
        await update.message.reply_text("Сначала начни викторину с /quiz 🧩")
        return
    
    # Authorization check: In Personal mode, only the starter can hint. In Group mode, anyone can hint.
    if mode == QUIZ_MODE_PERSONAL and uid != state["starter_uid"]:
        await update.message.reply_text("🚫 Ты можешь использовать подсказку только в своей личной викторине.")
        return
    
    if state["hints_used"] >= 2:
         await update.message.reply_text("🚫 Ты уже использовал две подсказки на этот вопрос.")
         return

    # Use manual hint logic: reveal one random letter immediately
    await give_hint_by_random_letter(quiz_key, chat_id, context, is_manual=True)
    
    # Crucial: If a manual hint is used, cancel the upcoming scheduled hint and timeout
    cancel_quiz_jobs(quiz_key, context) 
    
    # Re-schedule the timeout job after a manual hint
    if state["hints_used"] < 2:
        context.job_queue.run_once(
            quiz_timeout_job_callback, 
            30, # New timeout window starts now, 30s is a reasonable wait
            chat_id=chat_id, 
            user_id=uid, 
            name=f"job_timeout_{quiz_key}"
        )


async def answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    
    # 1. Determine which quiz, if any, the message is intended for.
    personal_key = get_quiz_key(uid, chat_id, QUIZ_MODE_PERSONAL)
    group_key = get_quiz_key(None, chat_id, QUIZ_MODE_GROUP) # Group key only depends on chat_id

    state = None
    quiz_key = None
    
    # Check for personal quiz first (takes precedence for the user)
    if personal_key in quiz_state and quiz_state[personal_key]["chat_id"] == chat_id:
        state = quiz_state[personal_key]
        quiz_key = personal_key
        # Only the starter can answer a personal quiz
        if uid != state["starter_uid"]:
            return # Ignore answer from non-starter in personal quiz

    # If no personal quiz, check for a group quiz in this chat
    elif group_key in quiz_state:
        state = quiz_state[group_key]
        quiz_key = group_key
        # Anyone can answer a group quiz

    if not state:
        # If it's a group chat, ignore the message unless it's a command
        if update.message.chat.type in ["group", "supergroup"]:
            return 
        await update.message.reply_text("Начни викторину с /quiz 🧩")
        return
        
    # --- Proceed with answer check ---
    question = state["question"]
    correct = question["answer"].strip().lower()
    hints_used = state["hints_used"]

    # 1. Check the answer
    if text.lower() == correct:
        points = max(3 - hints_used, 1)
        # Update score of the user who provided the correct answer
        update_score(uid, points) 
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🚀 Поздравляем! Это верный ответ. Ты получаешь +{points} очк(а/о)! 🎉",
            parse_mode='Markdown'
        )
    else:
        # Ignore wrong answers
        return

    # 2. Correct answer given: Cancel all pending job timers for this quiz key
    cancel_quiz_jobs(quiz_key, context) 

    # 3. Transition to the next question or completion
    await proceed_to_next_question(quiz_key, chat_id, context)


async def proceed_to_next_question(quiz_key, chat_id, context):
    """Handles the transition to the next question or ends the quiz."""
    state = quiz_state[quiz_key]
    starter_uid = state["starter_uid"] # The UID of the person who started the series

    # Check if this was the last question in a limited series
    is_last_question = (state["total_questions"] != -1 and state["asked"] >= state["total_questions"])
    
    if is_last_question:
        # Show final score of the user who initiated the series
        final_score = get_user_score(starter_uid) 
        del quiz_state[quiz_key]
        mode_text = 'Личный' if state["mode"] == QUIZ_MODE_PERSONAL else 'Групповой'
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🥳 Серия вопросов завершена! *[{mode_text} режим]* Твой текущий счет: *{final_score}* очков.\n\nНажми /quiz, чтобы выбрать новую серию, или /top, чтобы увидеть всех лидеров!",
            parse_mode='Markdown'
        )
        return

    # Initialize new question
    next_q = get_random_question()
    if not next_q:
        del quiz_state[quiz_key]
        await context.bot.send_message(chat_id=chat_id, text="💔 Извините, в базе данных больше нет уникальных вопросов.")
        return

    # Increment the counter
    next_asked_number = state["asked"] + 1
    total = state["total_questions"]
    mode = state["mode"]
    
    # Update state for the new question
    state["question"] = next_q
    state["hints_used"] = 0
    state["asked"] = next_asked_number
    state["revealed"] = ["_"] * len(next_q["answer"])
    
    # Start a new sequence of timers, passing the incremented counter
    await start_quiz(quiz_key, starter_uid, chat_id, context, total, current_asked=next_asked_number, mode=mode)

    
async def add_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        # Enhanced string
        await update.message.reply_text("🚫 Только администратор может использовать эту команду.")
        return

    if not context.args:
        await update.message.reply_text("Использование: /add Вопрос?;Ответ")
        return

    try:
        text = " ".join(context.args)
        question, answer = text.split(";", 1)
    except ValueError:
        await update.message.reply_text("Ошибка! Формат: /add Вопрос?;Ответ")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # Note that the questions table must exist and have 'question' and 'answer' fields
    try:
        cur.execute("INSERT INTO questions (question, answer) VALUES (?, ?)", (question.strip(), answer.strip()))
        conn.commit()
        # Enhanced string
        await update.message.reply_text(
            f"💾 Вопрос успешно добавлен в базу данных:\n\n*Вопрос*: {question.strip()}\n*Ответ*: {answer.strip()}", 
            parse_mode='Markdown'
        )
    except sqlite3.OperationalError as e:
         # Enhanced string
         await update.message.reply_text(f"❌ Ошибка БД: Проблема при добавлении вопроса. Убедитесь, что таблица 'questions' существует. Ошибка: {e}")
    finally:
        conn.close()

async def delete_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        # Enhanced string
        await update.message.reply_text("🚫 Только администратор может использовать эту команду.")
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Использование: /delete ID_вопроса")
        return

    qid = int(context.args[0])
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM questions WHERE id = ?", (qid,))
    conn.commit()
    conn.close()

    # Enhanced string
    await update.message.reply_text(f"🗑 Вопрос с ID {qid} удалён из базы.")

async def show_all_questions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to show all questions, IDs, and answers."""
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        await update.message.reply_text("🚫 Только администратор может использовать эту команду.")
        return

    questions = get_all_questions()

    if not questions:
        await update.message.reply_text("⚠️ В базе данных нет вопросов.")
        return

    # Prepare the message text
    message_parts = ["📋 *ВСЕ ВОПРОСЫ ИЗ БАЗЫ ДАННЫХ* 💾\n\n"]

    for q_id, q_text, a_text in questions:
        # Use a consistent format for readability
        message_parts.append(
            f"*{q_id}. Вопрос (ID {q_id})*:\n"
            f"❓ {q_text}\n"
            f"✅ *Ответ*: {a_text}\n"
            f"-----\n"
        )
        # Simple check to prevent exceeding the Telegram message limit (approx 4096 chars)
        if len("".join(message_parts)) > 3500:
            message_parts.append("\n... (Показана только часть вопросов из-за ограничения длины сообщения Telegram)")
            break
            
    await update.message.reply_text("".join(message_parts), parse_mode='Markdown')

async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    leaders = get_leaderboard()
    
    # Enhanced string with HTML and emojis
    text = "👑 ТОП-5 ЛИДЕРОВ ВИКТОРИНЫ 🚀\n\n"
    for i, (name, score) in enumerate(leaders, start=1):
        emoji = '🥇' if i == 1 else '🥈' if i == 2 else '🥉' if i == 3 else '🏅'
        
        # 1. Заменяем HTML-специальные символы на сущности, 
        # чтобы они не ломали разметку (например, < в имени)
        safe_name = name.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        
        # 2. Используем тег <b> для жирного шрифта в режиме HTML. 
        # Нижнее подчеркивание (_) теперь не является специальным символом.
        text += f"{emoji} {i}. <b>{safe_name or 'Без имени'}</b> — {score} очков\n"
        
    await update.message.reply_text(text, parse_mode='HTML') # <-- Using HTML mode

# ——— RUN ———
def main():
    # JobQueue must be initialized
    app = ApplicationBuilder().token(TOKEN).build()
    
    # JobQueue is now available via app.job_queue
    
    # Adding handlers
    app.add_handler(CommandHandler("help", help_command)) # Added /help command
    app.add_handler(CommandHandler("quiz", quiz))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("setname", set_name)) # NEW: Handler for /setname
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(CommandHandler("top", top))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("hint", hint))
    app.add_handler(CommandHandler("skip", skip_question)) # New handler for /skip
    app.add_handler(CommandHandler("add", add_question))
    app.add_handler(CommandHandler("delete", delete_question))
    app.add_handler(CommandHandler("showall", show_all_questions))
    app.add_handler(CommandHandler("removekeyboard", remove_keyboard)) # New handler to remove the keyboard
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, answer)) # Important: this must be placed after all CommandHandlers
    
    # DB Initialization
    # Create the database if it doesn't exist
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            score INTEGER DEFAULT 0
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT NOT NULL,
            answer TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

    print("Бот запущен. Ожидание обновлений...")
    app.run_polling()

if __name__ == "__main__":
    main()