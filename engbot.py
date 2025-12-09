import os
import asyncio
import google.generativeai as genai
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

# --- БЕРЕМ КЛЮЧИ ИЗ СИСТЕМЫ (БЕЗОПАСНО) ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

if not GEMINI_API_KEY or not TELEGRAM_TOKEN:
    raise ValueError("Ключи не найдены! Проверь переменные окружения.")

# Настройка Gemini
genai.configure(api_key=GEMINI_API_KEY)

# Системная инструкция - это то, что делает бота "учителем", а не просто переводчиком
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

# --- ЛОГИКА БОТА ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Yo! Я готов. Скидывай слово, фразу или спрашивай 'как сказать...'.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    
    # Показываем статус "печатает...", пока ждем ответа от Gemini
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')

    try:
        # Отправляем запрос в Gemini
        response = model.generate_content(user_text)
        ai_reply = response.text
        
        # Отправляем ответ пользователю (Markdown поддерживается для красоты)
        await update.message.reply_text(ai_reply)
        
    except Exception as e:
        await update.message.reply_text(f"Произошла ошибка связи с мозгом: {e}")

if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Хендлеры
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))

    print("Бот запущен...")
    app.run_polling()