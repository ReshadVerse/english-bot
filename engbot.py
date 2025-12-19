import os
import threading
import tempfile
import asyncio
import traceback # ЧТОБЫ ВИДЕТЬ ОШИБКИ
from flask import Flask
from dotenv import load_dotenv

# Библиотеки AI
import google.generativeai as genai
import edge_tts

# Библиотеки Telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler, 
    MessageHandler, CallbackQueryHandler, filters
)
from telegram.constants import ChatAction

# Наша база данных
from database import Database

# --- 1. НАСТРОЙКИ ---
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# Проверка ключей (теперь не роняет скрипт молча)
if not GEMINI_API_KEY:
    print("❌ ОШИБКА: Нет GEMINI_API_KEY в файле .env")
if not TELEGRAM_TOKEN:
    print("❌ ОШИБКА: Нет TELEGRAM_TOKEN в файле .env")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# Подключаем БД
try:
    db = Database()
except Exception as e:
    print(f"❌ ОШИБКА БАЗЫ ДАННЫХ: {e}")

SYSTEM_PROMPT = """
Ты — Элитный языковой коуч, специализирующийся на повышении уровня владения английским.
Твой Пользователь — амбициозный парень, который ценит эффективность, точность и высокооплачиваемые навыки.
Твой Тон: Прямой, лаконичный, профессиональный.

### ПРАВИЛА ФОРМАТИРОВАНИЯ
- Все **объяснения, переводы и нюансы** пиши строго НА РУССКОМ языке.
- Все **примеры и фразы** пиши НА АНГЛИЙСКОМ.
- Используй **Жирный шрифт** для ключевых слов.
- НИКОГДА не начинай с приветствия. Сразу к делу.

### ИНСТРУКЦИИ

#### СЦЕНАРИЙ 1: Пользователь присылает Слово или Фразу
1. **Перевод: [Слово на английском] — [Перевод на русском] [IPA транскрипция].
2. **Контексты (Примеры на английском):
   - 🏢 **Бизнес: Пример использования в деловой среде.
   - 🗣 **Разговорный: Живой пример из жизни.
   - 🔥 **Сленг: (Если есть) Пример использования в этой среде.
3. **Коллокации: 2-3 словосочетания на английском (с переводом в скобках).
4. **Нюансы: Кратко объясни НА РУССКОМ, в чем оттенки смысла, отличие от синонимов или этимология.
5. **Синонимы: Дай несколько наиболее подходящий синонимов.

#### СЦЕНАРИЙ 2: Пользователь спрашивает написав или отправив аудио "Как сказать...?"
1. 🏆 **Как Носитель: Самый естественный вариант.
2. 👔 **Формально: Офисный стиль.
3. 🚧 **Избегать: Типичные ошибки (калька с русского).
*Дай краткий комментарий на русском, почему именно так.*

#### СЦЕНАРИЙ 3: Пользователь присылает АУДИО на английском. ВАЖНО: используй этот сценарий, если голосовое сообщение английском, иначе используй сценарий 2.
3. **Перевод: Переведи фразу или предложение на русский.

2. **Оценка:**
   - 📉 **Ошибки: (Исправь на английском, объясни ошибку на русском).
   - 📈 **Апгрейд: Предложи, как сказать круче.
3. **Ответ: Ответь на сообщение на английском (поддержи диалог).

### ВАЖНО
Если слово имеет несколько значений (как "Shredded" — "уничтоженный в шредере" и "просушенный качок"), обязательно укажи оба перевода в пункте 1.
"""


# Инициализация модели (с защитой)
try:
    model = genai.GenerativeModel("gemini-2.5-flash-lite", system_instruction=SYSTEM_PROMPT)
except Exception as e:
    print(f"❌ ОШИБКА AI МОДЕЛИ: {e}")

# --- 2. FLASK SERVER ---
app = Flask(__name__)

@app.route('/')
def alive():
    return "I am alive!"

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, use_reloader=False)

# --- 3. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔊 Listen", callback_data="tts")],
        [InlineKeyboardButton("💾 Save to Dictionary", callback_data="save")]
    ])

async def generate_voice_file(text):
    VOICE = "en-US-ChristopherNeural"
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        output_file = f.name
    communicate = edge_tts.Communicate(text, VOICE)
    await communicate.save(output_file)
    return output_file

async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    words_to_review = db.get_words_to_review()
    
    for row in words_to_review:
        word_id, user_id, word, translation, stage = row
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Помню", callback_data=f"rev_ok_{word_id}"),
                InlineKeyboardButton("❌ Забыл", callback_data=f"rev_bad_{word_id}")
            ],
            [InlineKeyboardButton("🗑 Удалить", callback_data=f"stop_{word_id}")]
        ])
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"🔔 **Time to review!**\n\nКак переводится: **{word}**?",
                reply_markup=kb, parse_mode="Markdown"
            )
        except Exception:
            pass 

# --- 4. ХЕНДЛЕРЫ ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Yo! Я готов.\n🔹 Пиши слова — я переведу.\n🔹 Используй /mywords чтобы видеть словарь."
    )

async def show_my_words(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    words = db.get_all_words(chat_id)
    if not words:
        await update.message.reply_text("🤷‍♂️ Твой словарь пуст.")
        return

    message_text = "📚 **Твой словарь:**\n\n"
    for row in words:
        word, translation, stage = row
        safe_word = str(word).replace('*', '').replace('_', '').replace('`', '')
        safe_trans = str(translation).replace('*', '').replace('_', '').replace('`', '')
        level_icon = "🔥" * stage if stage < 4 else "🎓"
        
        line = f"🔹 **{safe_word}** ({level_icon} {stage})\n   _{safe_trans}_\n\n"
        if len(message_text) + len(line) > 4000:
            await update.message.reply_text(message_text, parse_mode="Markdown")
            message_text = ""
        message_text += line

    if message_text:
        await update.message.reply_text(message_text, parse_mode="Markdown")

async def delete_word_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        await update.message.reply_text("Пиши: `/delete слово`", parse_mode="Markdown")
        return
    word = " ".join(context.args)
    if db.delete_word(chat_id, word):
        await update.message.reply_text(f"🗑 Удалено: **{word}**", parse_mode="Markdown")
    else:
        await update.message.reply_text("Не нашел такого слова.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action='typing')
    try:
        response = await model.generate_content_async(user_text)
        context.user_data['last_reply'] = response.text
        context.user_data['last_input'] = user_text 
        await update.message.reply_text(response.text, reply_markup=get_keyboard())
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action='typing')
    try:
        file = await context.bot.get_file(update.message.voice.file_id)
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tf:
            await file.download_to_drive(custom_path=tf.name)
            tpath = tf.name
        g_file = genai.upload_file(tpath, mime_type="audio/ogg")
        await asyncio.sleep(1)
        resp = await model.generate_content_async(["Ответь на это аудио.", g_file])
        if os.path.exists(tpath): os.remove(tpath)
        context.user_data['last_reply'] = resp.text
        context.user_data['last_input'] = None 
        await update.message.reply_text(f"🗣 {resp.text}", reply_markup=get_keyboard())
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = update.effective_chat.id
    await query.answer()
    data = query.data

    if data == "tts":
        text = context.user_data.get('last_reply')
        if text:
            clean = text.replace('*', '').replace('_', '')[:1000]
            await context.bot.send_chat_action(chat_id, action='record_audio')
            try:
                path = await generate_voice_file(clean)
                with open(path, 'rb') as f: await context.bot.send_voice(chat_id, f)
                os.remove(path)
            except Exception as e: await context.bot.send_message(chat_id, f"TTS Error: {e}")

    # --- ПРОСТОЕ СОХРАНЕНИЕ (ТОЛЬКО ПЕРЕВОД) ---
    elif data == "save":
        word = context.user_data.get('last_input')
        
        # Защита от случайных нажатий
        if not word: return 
        
        await context.bot.send_chat_action(chat_id, action='typing')

        # 1. Просим только перевод
        try:
            # Используем ту же модель, просто просим дать ТОЛЬКО перевод
            r = await model.generate_content_async(f"Translate '{word}' to Russian. Return ONLY the translation words. No definitions.")
            trans = r.text.strip() # Чистим от пробелов
        except:
            trans = "..."

        # 2. Сохраняем
        if db.add_word(chat_id, word, trans):
            await context.bot.send_message(chat_id, f"✅ Сохранено: **{word}** — {trans}", parse_mode="Markdown")
        else:
            await context.bot.send_message(chat_id, "⚠ Такое слово уже есть.")
    elif data.startswith("rev_ok_"):
        wid = int(data.split("_")[-1])
        db.update_word_stage(wid, 2)
        await query.edit_message_text("🎉 Супер! Отложил на 3 дня.")
    
    # 3. ИНТЕРВАЛЬНОЕ ПОВТОРЕНИЕ
    elif data.startswith("rev_ok_"):
        wid = int(data.split("_")[-1])
        
        # 1. Получаем инфо о слове перед обновлением
        row = db.get_word_by_id(wid)
        
        # 2. Обновляем статус
        db.update_word_stage(wid, 2) 
        
        if row:
            word, translation = row
            # 3. Показываем перевод
            await query.edit_message_text(
                f"🎉 Красавчик!\n\n✅ **{word}** — {translation}\n\n(Увидимся через 3 дня)",
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text("🎉 Молодец! (Слово удалено, но я засчитал)")
    
    elif data.startswith("rev_bad_"):
        wid = int(data.split("_")[-1])
        
        row = db.get_word_by_id(wid)
        
        db.update_word_stage(wid, 1) # Сброс
        
        if row:
            word, translation = row
            await query.edit_message_text(
                f"🤔 Ничего страшного.\n\n📖 **{word}** — {translation}\n\n(Спрошу завтра)",
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text("🤔 Окей, повторим завтра.")

# --- 5. ЗАПУСК (С ОТЛОВОМ ОШИБОК) ---
if __name__ == '__main__':
    try:
        threading.Thread(target=run_web_server, daemon=True).start()
        
        print("✅ Бот запускается...")
        
        if not GEMINI_API_KEY or not TELEGRAM_TOKEN:
            print("❌ СТОП: Проверь ключи в .env")
            input("Нажми Enter чтобы выйти...")
            exit()

        app_bot = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
        
        # Планировщик
        app_bot.job_queue.run_repeating(check_reminders, interval=60, first=10)

        # Хендлеры
        app_bot.add_handler(CommandHandler("start", start))
        app_bot.add_handler(CommandHandler("mywords", show_my_words))
        app_bot.add_handler(CommandHandler("delete", delete_word_command))
        app_bot.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))
        app_bot.add_handler(MessageHandler(filters.VOICE, handle_voice))
        app_bot.add_handler(CallbackQueryHandler(button_click))

        print("🚀 Бот работает! Нажми Ctrl+C для остановки.")
        app_bot.run_polling()
        
    except Exception as e:
        print("\n❌ КРИТИЧЕСКАЯ ОШИБКА ПРИ ЗАПУСКЕ:")
        print(traceback.format_exc())
        input("\nНажми Enter, чтобы закрыть окно...")