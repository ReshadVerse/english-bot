import os
import threading
from flask import Flask
import google.generativeai as genai
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
from dotenv import load_dotenv  # <--- 1. Импортируем

load_dotenv()  # <--- 2. Загружаем ключи из файла .env
# --- НАСТРОЙКИ ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

if not GEMINI_API_KEY or not TELEGRAM_TOKEN:
    raise ValueError("Ключи не найдены! Проверь переменные окружения.")

genai.configure(api_key=GEMINI_API_KEY)

# --- ТВОЙ ИЗНАЧАЛЬНЫЙ "УМНЫЙ" ПРОМПТ ---
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
"""

model = genai.GenerativeModel(
    model_name="gemini-2.5-flash",
    system_instruction=SYSTEM_PROMPT
)

# --- 1. ФЕЙКОВЫЙ ВЕБ-СЕРВЕР (Чтобы Render не убивал бота) ---
app = Flask(__name__)

@app.route('/')
def alive():
    return "I am alive!"

def run_web_server():
    # Render выдает порт через переменную окружения PORT
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# --- 2. ЛОГИКА БОТА ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Yo! Я готов. Скидывай слово, фразу или спрашивай 'как сказать...'.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    
    # Показываем статус "печатает...", пока ждем ответ
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')

    try:
        response = model.generate_content(user_text)
        # Отправляем ответ (без Markdown, чтобы избежать ошибок с символами)
        await update.message.reply_text(response.text) 
    except Exception as e:
        await update.message.reply_text(f"Произошла ошибка связи с мозгом: {e}")

# --- ЗАПУСК ВСЕГО ВМЕСТЕ ---
if __name__ == '__main__':
    # Запускаем веб-сервер в отдельном потоке (фоном)
    threading.Thread(target=run_web_server).start()
    
    # Запускаем бота
    print("Бот запускается...")
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    application.run_polling()