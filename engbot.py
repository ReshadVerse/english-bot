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

# --- ТВОЙ ОРИГИНАЛЬНЫЙ ПРОМПТ (С небольшим дополнением про аудио) ---
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

# Используем твою любимую модель (она умеет работать с аудио!)
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
    app.run(host="0.0.0.0", port=port)

# --- 2. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

# Функция для создания кнопки "Озвучить"
def get_pronounce_keyboard(text_to_speak):
    # Ограничиваем длину текста для кнопки (Telegram имеет лимиты на данные в кнопках)
    callback_data = f"tts|{text_to_speak[:50]}" 
    keyboard = []
    return InlineKeyboardMarkup(keyboard)

# Функция генерации речи (Edge TTS)
async def generate_voice_file(text):
    # Голос: en-US-ChristopherNeural (мужской) или en-US-AriaNeural (женский)
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
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')

    try:
        response = model.generate_content(user_text)
        # Отправляем ответ с кнопкой "Произнести" (озвучиваем исходный запрос пользователя)
        await update.message.reply_text(
            response.text, 
            reply_markup=get_pronounce_keyboard(user_text)
        ) 
    except Exception as e:
        await update.message.reply_text(f"Ошибка мозга: {e}")

# Обработка ГОЛОСОВЫХ (Новая фича!)
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
    
    try:
        # 1. Скачиваем файл голосового
        voice_file = await context.bot.get_file(update.message.voice.file_id)
        
        # Сохраняем во временный файл
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as temp_audio:
            await voice_file.download_to_drive(custom_path=temp_audio.name)
            temp_audio_path = temp_audio.name

        # 2. Загружаем файл в Gemini (через Files API или как байты - Flash 2.5 умеет принимать файлы)
        # Для простоты и скорости загружаем как MIME data
        uploaded_file = genai.upload_file(temp_audio_path, mime_type="audio/ogg")
        
        # 3. Отправляем в модель
        response = model.generate_content(
            ["Послушай это сообщение. Ответь на него. Если это вопрос на английском — ответь на английском.", uploaded_file]
        )
        
        # Чистим за собой
        os.remove(temp_audio_path)

        # Отправляем ответ
        await update.message.reply_text(f"🗣 **Ответ на войс:**\n\n{response.text}")

    except Exception as e:
        await update.message.reply_text(f"Не расслышал... Ошибка: {e}")

# Обработка нажатия КНОПКИ
async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer() # Чтобы кнопка перестала мигать

    data = query.data.split("|")
    if data == "tts":
        text_to_speak = data[1] # Текст, который мы зашили в кнопку
        
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='record_audio')
        
        try:
            # Генерируем аудио
            audio_path = await generate_voice_file(text_to_speak)
            
            # Отправляем как голосовое
            await context.bot.send_voice(chat_id=update.effective_chat.id, voice=open(audio_path, 'rb'))
            
            # Удаляем файл
            os.remove(audio_path)
            
        except Exception as e:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Ошибка озвучки: {e}")

# --- ЗАПУСК ---
if __name__ == '__main__':
    # Запускаем веб-сервер в фоне
    threading.Thread(target=run_web_server).start()
    
    print("Бот запускается...")
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Регистрируем хендлеры
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text)) # Текст
    application.add_handler(MessageHandler(filters.VOICE, handle_voice)) # Голосовые
    application.add_handler(CallbackQueryHandler(button_click)) # Кнопки

    application.run_polling()