import os
import threading
import tempfile
import asyncio
from flask import Flask
from dotenv import load_dotenv
import google.generativeai as genai
import edge_tts
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from telegram.constants import ChatAction

# --- НАСТРОЙКИ ---
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

if not GEMINI_API_KEY or not TELEGRAM_TOKEN:
    raise ValueError("❌ Ключи не найдены! Проверь переменные окружения.")

genai.configure(api_key=GEMINI_API_KEY)

# Глобальная переменная для хранения последнего ответа
TTS_CACHE = {}

SYSTEM_PROMPT = """
Ты — профессиональный репетитор английского языка для уровня B2-C1.
Твоя задача — не просто переводить, а объяснять нюансы.
Пользователь — амбициозный парень, ценит краткость и точность.

Если пользователь присылает слово или фразу:
1. Дай прямой перевод.
2. Приведи 2-3 примера использования в контексте (бизнес, разговорный, сленг).
3. Если это идиома, объясни её происхождение или аналог.
4. Если есть синонимы, укажи, чем они отличаются по тону.

Если пользователь спрашивает "как сказать...":
1. Дай самый естественный вариант (native speaker way).
2. Дай более формальный вариант.
3. Дай сленговый вариант (если уместно).

Если прислано АУДИО (голосовое сообщение):
   - Послушай произношение и грамматику.
   - Ответь на вопрос пользователя.
   - Сделай перевод в самом конце.
   - Если есть ошибки в речи, мягко исправь их.
"""

model = genai.GenerativeModel(
    model_name="gemini-2.5-flash",
    system_instruction=SYSTEM_PROMPT
)

# --- 1. ФЕЙКОВЫЙ ВЕБ-СЕРВЕР ---
app = Flask(__name__)

@app.route('/')
def alive():
    return "I am alive!"

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    # use_reloader=False предотвращает дублирование процессов
    app.run(host="0.0.0.0", port=port, use_reloader=False)

# --- 2. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def get_pronounce_keyboard():
    # Кнопка для озвучки последнего сохраненного сообщения
    keyboard = [
        [InlineKeyboardButton("🔊 Listen (Pronunciation)", callback_data="tts_last")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def generate_voice_file(text):
    # Голос: en-US-ChristopherNeural (мужской, отличный акцент)
    VOICE = "en-GB-RyanNeural"
    # Создаем файл, закрываем его, чтобы другие процессы могли с ним работать
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp_file:
        output_file = temp_file.name
    
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
        # ВАЖНО: await + generate_content_async (чтобы бот не зависал)
        response = await model.generate_content_async(user_text)
        bot_reply = response.text
        
        # Сохраняем в кэш
        TTS_CACHE[chat_id] = bot_reply
        
        await update.message.reply_text(
            bot_reply, 
            reply_markup=get_pronounce_keyboard()
        ) 
    except Exception as e:
        await update.message.reply_text(f"Ошибка мозга: {e}")

# Обработка ГОЛОСОВЫХ
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action='typing')
    
    try:
        voice_file = await context.bot.get_file(update.message.voice.file_id)
        
        # Скачиваем голосовое
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as temp_audio:
            await voice_file.download_to_drive(custom_path=temp_audio.name)
            temp_audio_path = temp_audio.name

        # Загружаем в Gemini
        uploaded_file = genai.upload_file(temp_audio_path, mime_type="audio/ogg")
        
        # Даем секунду на обработку файла на серверах Google
        await asyncio.sleep(1)

        # ВАЖНО: await + generate_content_async
        response = await model.generate_content_async(
            ["Послушай это сообщение. Ответь на него. Если это вопрос на английском — ответь на английском.", uploaded_file]
        )
        
        # Уборка локального файла
        if os.path.exists(temp_audio_path):
            os.remove(temp_audio_path)
        
        bot_reply = response.text
        TTS_CACHE[chat_id] = bot_reply

        await update.message.reply_text(
            f"🗣 **Ответ на войс:**\n\n{bot_reply}",
            reply_markup=get_pronounce_keyboard()
        )

    except Exception as e:
        await update.message.reply_text(f"Не расслышал... Ошибка: {e}")

# Обработка нажатия КНОПКИ
async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = update.effective_chat.id
    await query.answer() # Убираем часики загрузки

    if query.data == "tts_last":
        text_to_speak = TTS_CACHE.get(chat_id)
        
        if not text_to_speak:
            await context.bot.send_message(chat_id=chat_id, text="⚠ Нечего озвучивать (кэш пуст).")
            return
        
        # 1. Сначала чистим текст от Markdown (*, _)
        clean_text = text_to_speak.replace('*', '').replace('_', '')
        
        # 2. Потом обрезаем ПОЧИЩЕННЫЙ текст, если он слишком длинный
        if len(clean_text) > 1000:
            clean_text = clean_text[:1000]

        await context.bot.send_chat_action(chat_id=chat_id, action='record_audio')
        
        try:
            # 3. Передаем в генератор ЧИСТЫЙ текст
            audio_path = await generate_voice_file(clean_text)
            
            with open(audio_path, 'rb') as audio_file:
                await context.bot.send_voice(chat_id=chat_id, voice=audio_file)
            
            # Уборка аудио файла
            if os.path.exists(audio_path):
                os.remove(audio_path)
            
        except Exception as e:
            await context.bot.send_message(chat_id=chat_id, text=f"Ошибка озвучки: {e}")

# --- ЗАПУСК ---
if __name__ == '__main__':
    # Запуск Flask в фоне
    flask_thread = threading.Thread(target=run_web_server)
    flask_thread.daemon = True
    flask_thread.start()
    
    print("✅ Бот запущен! Все системы в норме.")
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    application.add_handler(CallbackQueryHandler(button_click))

    application.run_polling()