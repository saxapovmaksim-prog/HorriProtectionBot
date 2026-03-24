import asyncio
import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Set

from telegram import Update, ChatPermissions
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ---------- КОНФИГУРАЦИЯ ----------
# ВНИМАНИЕ: в реальном проекте токен должен храниться в переменных окружения!
TOKEN = "8768850938:AAGXlxCENVXIqUXAJMBG2bl2xgUwNAJOc4Q"

# Настройки антифлуда
FLOOD_LIMIT = 5           # максимальное количество сообщений
FLOOD_WINDOW = 10         # за N секунд
FLOOD_MUTE_DURATION = 60  # блокировка на N секунд

# Настройки фильтров
BLOCK_LINKS = True        # блокировать ссылки
BLOCK_MEDIA = True        # блокировать медиа (фото, видео, документы и т.д.)
BLOCK_BAD_WORDS = True    # блокировать по списку запрещённых слов

# Путь к файлу со списком запрещённых слов (одно слово на строку)
BAD_WORDS_FILE = "bad_words.txt"

# ID чата для логов (укажите при необходимости)
LOG_CHAT_ID = None

# ---------- ГЛОБАЛЬНЫЕ СТРУКТУРЫ ----------
# Счётчики сообщений для антифлуда: {user_id: [timestamps]}
user_messages: Dict[int, List[datetime]] = defaultdict(list)

# Множество запрещённых слов (загружаются из файла)
bad_words: Set[str] = set()

# ---------- ЗАГРУЗКА ЗАПРЕЩЁННЫХ СЛОВ ----------
def load_bad_words():
    """Загружает список запрещённых слов из файла."""
    try:
        with open(BAD_WORDS_FILE, "r", encoding="utf-8") as f:
            words = [line.strip().lower() for line in f if line.strip()]
        bad_words.update(words)
        logging.info(f"Загружено {len(bad_words)} запрещённых слов.")
    except FileNotFoundError:
        logging.warning(f"Файл {BAD_WORDS_FILE} не найден, список запрещённых слов пуст.")
    except Exception as e:
        logging.error(f"Ошибка загрузки запрещённых слов: {e}")

# ---------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ----------
def contains_bad_words(text: str) -> bool:
    """Проверяет, содержит ли текст запрещённые слова."""
    if not text:
        return False
    text_lower = text.lower()
    for word in bad_words:
        if word in text_lower:
            return True
    return False

def contains_link(text: str) -> bool:
    """Проверяет наличие ссылки в тексте."""
    url_pattern = re.compile(r'(https?://|www\.)\S+', re.IGNORECASE)
    return bool(url_pattern.search(text))

def is_flooding(user_id: int) -> bool:
    """Проверяет, превышает ли пользователь лимит сообщений."""
    now = datetime.now()
    timestamps = user_messages[user_id]
    # Удаляем записи старше окна
    cutoff = now - timedelta(seconds=FLOOD_WINDOW)
    timestamps = [ts for ts in timestamps if ts > cutoff]
    user_messages[user_id] = timestamps
    timestamps.append(now)
    return len(timestamps) > FLOOD_LIMIT

# ---------- ФУНКЦИИ БАНА/МУТА ----------
async def restrict_user(update: Update, user_id: int, duration_seconds: int, reason: str):
    """Ограничивает пользователя (мут) на указанное время."""
    try:
        await update.effective_chat.restrict_member(
            user_id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=datetime.now() + timedelta(seconds=duration_seconds)
        )
        await update.message.reply_text(
            f"Пользователь {update.effective_user.mention_html()} получил ограничение на {duration_seconds} секунд.\nПричина: {reason}",
            parse_mode=ParseMode.HTML
        )
        # Логирование
        if LOG_CHAT_ID:
            await update.get_bot().send_message(
                LOG_CHAT_ID,
                f"🚫 Заблокирован {update.effective_user.mention_html()} в {update.effective_chat.title}.\nПричина: {reason}",
                parse_mode=ParseMode.HTML
            )
    except Exception as e:
        logging.error(f"Не удалось ограничить пользователя {user_id}: {e}")

async def kick_user(update: Update, user_id: int, reason: str):
    """Удаляет пользователя из чата."""
    try:
        await update.effective_chat.ban_member(user_id)
        await update.effective_chat.unban_member(user_id)  # чтобы можно было вернуться по ссылке
        await update.message.reply_text(
            f"Пользователь {update.effective_user.mention_html()} был удалён из чата.\nПричина: {reason}",
            parse_mode=ParseMode.HTML
        )
        if LOG_CHAT_ID:
            await update.get_bot().send_message(
                LOG_CHAT_ID,
                f"👢 Кикнут {update.effective_user.mention_html()} из {update.effective_chat.title}.\nПричина: {reason}",
                parse_mode=ParseMode.HTML
            )
    except Exception as e:
        logging.error(f"Не удалось кикнуть пользователя {user_id}: {e}")

# ---------- ОБРАБОТЧИКИ СООБЩЕНИЙ ----------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Главный обработчик всех сообщений. Проверяет спам, запрещённые слова, ссылки и медиа."""
    if not update.effective_chat:
        return

    chat = update.effective_chat
    user = update.effective_user
    message = update.effective_message

    # Игнорируем сообщения от ботов и служебные
    if user.is_bot:
        return

    # Проверка на права бота (должен иметь права администратора для удаления/бана)
    bot_member = await chat.get_member(context.bot.id)
    if not bot_member.can_restrict_members:
        await message.reply_text("❌ У бота нет прав на удаление сообщений и ограничение пользователей. Назначьте его администратором с правами на блокировку.")
        return

    # 1. Антифлуд
    if is_flooding(user.id):
        await restrict_user(update, user.id, FLOOD_MUTE_DURATION, "Флуд")
        try:
            await message.delete()
        except:
            pass
        return

    # 2. Запрещённые слова
    if BLOCK_BAD_WORDS and message.text and contains_bad_words(message.text):
        await restrict_user(update, user.id, FLOOD_MUTE_DURATION, "Запрещённое слово")
        try:
            await message.delete()
        except:
            pass
        return

    # 3. Ссылки
    if BLOCK_LINKS and message.text and contains_link(message.text):
        await restrict_user(update, user.id, FLOOD_MUTE_DURATION, "Запрещённые ссылки")
        try:
            await message.delete()
        except:
            pass
        return

    # 4. Медиа (фото, видео, документы, голосовые и т.д.)
    if BLOCK_MEDIA:
        if (message.photo or message.video or message.document or message.voice or
            message.audio or message.animation or message.sticker):
            await restrict_user(update, user.id, FLOOD_MUTE_DURATION, "Медиафайлы запрещены")
            try:
                await message.delete()
            except:
                pass
            return

    # Если все проверки пройдены – сообщение остаётся
    # Можно добавить дополнительную логику (например, запись в лог)

async def handle_new_chat_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветствие новых участников."""
    for member in update.message.new_chat_members:
        if member.id == context.bot.id:
            # Бот добавлен в группу – приветствие
            await update.message.reply_text(
                "🤖 Бот-защитник активирован!\n"
                "Я буду следить за порядком: блокировать флуд, запрещённые слова, ссылки и медиа.\n"
                "Команды:\n"
                "/settings – показать текущие настройки\n"
                "/addword <слово> – добавить слово в чёрный список (только для админов)\n"
                "/delword <слово> – удалить слово из чёрного списка\n"
                "/setflood <лимит> <окно> – настроить антифлуд\n"
                "/mute <user> – замутить пользователя\n"
                "/kick <user> – кикнуть пользователя"
            )
        else:
            await update.message.reply_text(f"Добро пожаловать, {member.full_name}! Пожалуйста, соблюдайте правила чата.")

# ---------- КОМАНДЫ АДМИНИСТРАТОРА ----------
async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает текущие настройки бота."""
    text = (
        "⚙️ *Текущие настройки бота:*\n"
        f"• Антифлуд: {FLOOD_LIMIT} сообщений за {FLOOD_WINDOW} сек → мут {FLOOD_MUTE_DURATION} сек\n"
        f"• Блокировка ссылок: {'✅' if BLOCK_LINKS else '❌'}\n"
        f"• Блокировка медиа: {'✅' if BLOCK_MEDIA else '❌'}\n"
        f"• Блокировка слов: {'✅' if BLOCK_BAD_WORDS else '❌'} (загружено {len(bad_words)} слов)"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def add_word_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавляет слово в чёрный список (только для админов)."""
    if not await is_admin(update, context):
        return
    if not context.args:
        await update.message.reply_text("Укажите слово: /addword слово")
        return
    word = context.args[0].lower()
    bad_words.add(word)
    await update.message.reply_text(f"Слово '{word}' добавлено в чёрный список.")
    # Опционально сохранить в файл
    save_bad_words()

async def del_word_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаляет слово из чёрного списка (только для админов)."""
    if not await is_admin(update, context):
        return
    if not context.args:
        await update.message.reply_text("Укажите слово: /delword слово")
        return
    word = context.args[0].lower()
    if word in bad_words:
        bad_words.remove(word)
        await update.message.reply_text(f"Слово '{word}' удалено из чёрного списка.")
        save_bad_words()
    else:
        await update.message.reply_text(f"Слово '{word}' не найдено в чёрном списке.")

async def set_flood_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Устанавливает лимиты антифлуда (только для админов)."""
    if not await is_admin(update, context):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Использование: /setflood <лимит> <окно_сек>")
        return
    try:
        limit = int(context.args[0])
        window = int(context.args[1])
        global FLOOD_LIMIT, FLOOD_WINDOW
        FLOOD_LIMIT = limit
        FLOOD_WINDOW = window
        await update.message.reply_text(f"Антифлуд настроен: {limit} сообщений за {window} секунд.")
    except ValueError:
        await update.message.reply_text("Ошибка: лимит и окно должны быть числами.")

async def mute_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Замутить пользователя (только для админов)."""
    if not await is_admin(update, context):
        return
    # Простейший парсинг: либо ответ на сообщение, либо упоминание
    if update.message.reply_to_message:
        user_id = update.message.reply_to_message.from_user.id
        duration = int(context.args[0]) if context.args else 60
        await restrict_user(update, user_id, duration, "Мут по команде администратора")
    else:
        await update.message.reply_text("Ответьте на сообщение пользователя командой /mute [секунды]")

async def kick_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кикнуть пользователя (только для админов)."""
    if not await is_admin(update, context):
        return
    if update.message.reply_to_message:
        user_id = update.message.reply_to_message.from_user.id
        await kick_user(update, user_id, "Кик по команде администратора")
    else:
        await update.message.reply_text("Ответьте на сообщение пользователя командой /kick")

# ---------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ АДМИНОВ ----------
async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Проверяет, является ли пользователь администратором чата."""
    user = update.effective_user
    try:
        member = await update.effective_chat.get_member(user.id)
        if member.status in ("administrator", "creator"):
            return True
        else:
            await update.message.reply_text("⛔ Эта команда доступна только администраторам.")
            return False
    except Exception as e:
        logging.error(f"Ошибка проверки прав администратора: {e}")
        return False

def save_bad_words():
    """Сохраняет текущий список запрещённых слов в файл."""
    try:
        with open(BAD_WORDS_FILE, "w", encoding="utf-8") as f:
            for word in bad_words:
                f.write(word + "\n")
        logging.info("Список запрещённых слов сохранён.")
    except Exception as e:
        logging.error(f"Ошибка сохранения списка слов: {e}")

# ---------- ЗАПУСК БОТА ----------
def main():
    # Настройка логирования
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO
    )
    # Загружаем запрещённые слова
    load_bad_words()

    # Создаём приложение
    application = Application.builder().token(TOKEN).build()

    # Регистрируем обработчики сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, handle_message))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_chat_members))

    # Регистрируем команды
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("addword", add_word_command))
    application.add_handler(CommandHandler("delword", del_word_command))
    application.add_handler(CommandHandler("setflood", set_flood_command))
    application.add_handler(CommandHandler("mute", mute_command))
    application.add_handler(CommandHandler("kick", kick_command))

    # Запускаем бота
    application.run_polling()

if __name__ == "__main__":
    main()
