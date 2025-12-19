import sqlite3
import datetime

class Database:
    def __init__(self, db_file="vocab.db"):
        self.connection = sqlite3.connect(db_file, check_same_thread=False)
        self.cursor = self.connection.cursor()
        self.create_table()

    def create_table(self):
        """Создает таблицу, если её нет."""
        with self.connection:
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS words (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    word TEXT,
                    translation TEXT,
                    next_review TIMESTAMP,
                    stage INTEGER DEFAULT 0
                )
            """)

    def add_word(self, user_id, word, translation):
        """Добавляет слово. Возвращает True, если добавлено, False если уже было."""
        with self.connection:
            exists = self.cursor.execute("SELECT id FROM words WHERE user_id = ? AND word = ?", (user_id, word)).fetchone()
            if not exists:
                next_review = datetime.datetime.now() + datetime.timedelta(days=1)
                self.cursor.execute(
                    "INSERT INTO words (user_id, word, translation, next_review, stage) VALUES (?, ?, ?, ?, ?)",
                    (user_id, word, translation, next_review, 1)
                )
                return True
            return False

    def get_all_words(self, user_id):
        """Возвращает все слова пользователя для команды /mywords."""
        with self.connection:
            return self.cursor.execute(
                "SELECT word, translation, stage FROM words WHERE user_id = ? ORDER BY id DESC", 
                (user_id,)
            ).fetchall()

    def get_words_to_review(self):
        """Ищет слова, которые пора повторять (для таймера)."""
        now = datetime.datetime.now()
        with self.connection:
            return self.cursor.execute(
                "SELECT id, user_id, word, translation, stage FROM words WHERE next_review <= ?", 
                (now,)
            ).fetchall()

    def update_word_stage(self, word_id, stage):
        """Обновляет дату следующего повтора."""
        # Интервалы: 1 -> 3 -> 7 -> 14 -> 30 дней
        intervals = {1: 1, 2: 3, 3: 7, 4: 14, 5: 30}
        days_to_add = intervals.get(stage, 30)
        
        next_review = datetime.datetime.now() + datetime.timedelta(days=days_to_add)
        
        with self.connection:
            self.cursor.execute(
                "UPDATE words SET stage = ?, next_review = ? WHERE id = ?", 
                (stage, next_review, word_id)
            )

    def delete_word(self, user_id, word):
        """Удаляет слово по названию (для команды /delete)."""
        with self.connection:
            self.cursor.execute("DELETE FROM words WHERE user_id = ? AND word = ?", (user_id, word))
            return self.cursor.rowcount > 0

    def delete_word_by_id(self, word_id):
        """Удаляет слово по ID (для кнопки)."""
        with self.connection:
            self.cursor.execute("DELETE FROM words WHERE id = ?", (word_id,))


    def get_word_by_id(self, word_id):
        """Возвращает слово и перевод по ID (для показа ответа)."""
        with self.connection:
            return self.cursor.execute(
                "SELECT word, translation FROM words WHERE id = ?", 
                (word_id,)
            ).fetchone()