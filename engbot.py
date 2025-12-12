import os
import threading
import tempfile
from flask import Flask
from dotenv import load_dotenv
import google.generativeai as genai
import edge_tts
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters

# --- НАСТРОЙКИ ---
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

if not GEMINI_API_KEY or not TELEGRAM_TOKEN:
    raise ValueError("Ключи не найдены! Проверь переменные окружения.")

genai.configure(api_key=GEMINI_API_KEY)

# Глобальная переменная для хранения последнего ответа (чтобы озвучивать длинные тексты)
# Format: {chat_id: "текст ответа"}
TTS_CACHE = {}

SYSTEM_PROMPT = """
Ты — профессиональный репетитор английского языка для уровня B2-C1.
Твоя задача — не просто переводить, а объяснять нюансы.
Пользователь — амбициозный парень, ценит краткость и точность.

1. Если прислан ТЕКСТ:
   - Дай прямой перевод.
   - Приведи 2-3 примера (бизнес, сленг).
   - Объясни идиомы или разницу синонимов.

2. Если прислано АУДИО (голосовое сообщение):
   - Послушай произношение и грамматику.
   - Ответь на вопрос пользователя.
   - Если есть ошибки в речи, мягко исправь их.
"""

model = genai.GenerativeModel(
    model_name="gemini-2.5-flash", # Твоя рабочая модель
    system_instruction=SYSTEM_PROMPT
)

# --- 1. ФЕЙКОВЫЙ ВЕБ-СЕРВЕР ---
app = Flask(__name__)

@app.route('/')
def alive():
    return "I am alive!"

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# --- 2. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def get_pronounce_keyboard():
    # Кнопка теперь не несет в себе текст, она просто сигнал "Озвучь последнее"
    keyboard =[]
    return InlineKeyboardMarkup(keyboard)

async def generate_voice_file(text):
    # Голос: en-US-ChristopherNeural (мужской)
    VOICE = "en-US-ChristopherNeural"
    output_file = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False).name
    communicate = edge_tts.Communicate(text, VOICE)
    await communicate.save(output_file)
    return output_file

# --- 3. ОБРАБОТЧИКИ БОТА ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Yo! Я на связи.\n"
        "🔹 Пиши слова — я переведу.\n"
        "🔹 Жми 🎤 и говори — я послушаю твой акцент и отвечу.\n"
        "🔹 Жми кнопку под ответом, чтобы услышать правильное произношение."
    )

# Обработка ТЕКСТА
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    chat_id = update.effective_chat.id
    
    await context.bot.send_chat_action(chat_id=chat_id, action='typing')

    try:
        response = model.generate_content(user_text)
        bot_reply = response.text
        
        # Сохраняем ответ в память, чтобы потом озвучить
        TTS_CACHE[chat_id] = bot_reply
        
        await update.message.reply_text(
            bot_reply, 
            reply_markup=get_pronounce_keyboard()
        ) 
    except Exception as e:
        await update.message.reply_text(f"Ошибка мозга: {e}")

# Обработка ГОЛОСОВЫХ (ИСПРАВЛЕНО)
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action='typing')
    
    try:
        voice_file = await context.bot.get_file(update.message.voice.file_id)
        
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as temp_audio:
            await voice_file.download_to_drive(custom_path=temp_audio.name)
            temp_audio_path = temp_audio.name

        uploaded_file = genai.upload_file(temp_audio_path, mime_type="audio/ogg")
        
        response = model.generate_content(
            ["Послушай это сообщение. Ответь на него. Если это вопрос на английском — ответь на английском.", uploaded_file]
        )
        
        os.remove(temp_audio_path)
        
        bot_reply = response.text
        
        # 1. Сохраняем ответ в память
        TTS_CACHE[chat_id] = bot_reply

        # 2. Отправляем ответ ТЕПЕРЬ С КНОПКОЙ (вот чего не хватало!)
        await update.message.reply_text(
            f"🗣 **Ответ на войс:**\n\n{bot_reply}",
            reply_markup=get_pronounce_keyboard()
        )

    except Exception as e:
        await update.message.reply_text(f"Не расслышал... Ошибка: {e}")

# Обработка нажатия КНОПКИ (ИСПРАВЛЕНО ПОД НОВУЮ ЛОГИКУ)
async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = update.effective_chat.id
    await query.answer()

    if query.data == "tts_last":
        # Достаем текст из памяти
        text_to_speak = TTS_CACHE.get(chat_id)
        
        if not text_to_speak:
            await context.bot.send_message(chat_id=chat_id, text="⚠ Нечего озвучивать (кэш пуст).")
            return

        await context.bot.send_chat_action(chat_id=chat_id, action='record_audio')
        
        try:
            # Если текст очень длинный (больше 1000 символов), лучше обрезать, чтобы не зависло
            if len(text_to_speak) > 1000:
                text_to_speak = text_to_speak[:1000]
            
            audio_path = await generate_voice_file(text_to_speak)
            
            await context.bot.send_voice(chat_id=chat_id, voice=open(audio_path, 'rb'))
            os.remove(audio_path)
            
        except Exception as e:
            await context.bot.send_message(chat_id=chat_id, text=f"Ошибка озвучки: {e}")

# --- ЗАПУСК ---
if __name__ == '__main__':
    threading.Thread(target=run_web_server).start()
    
    print("Бот запускается... (С ИСПРАВЛЕННОЙ КНОПКОЙ)")
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    application.add_handler(CallbackQueryHandler(button_click))

    application.run_polling()