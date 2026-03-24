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
TOKEN = "8768850938:AAGXlxCENVXIqUXAJMBG2bl2xgUwNAJOc4Q"

# Настройки антифлуда
FLOOD_LIMIT = 5
FLOOD_WINDOW = 10
FLOOD_MUTE_DURATION = 60

# Включение/отключение функций
BLOCK_LINKS = True
BLOCK_MEDIA = True
BLOCK_BAD_WORDS = True

BAD_WORDS_FILE = "bad_words.txt"
LOG_CHAT_ID = None          # ID чата для логов, если нужен

# ---------- ГЛОБАЛЬНЫЕ СТРУКТУРЫ ----------
user_messages: Dict[int, List[datetime]] = defaultdict(list)
bad_words: Set[str] = set()

# ---------- ЗАГРУЗКА ЗАПРЕЩЁННЫХ СЛОВ ----------
def load_bad_words():
    try:
        with open(BAD_WORDS_FILE, "r", encoding="utf-8") as f:
            words = [line.strip().lower() for line in f if line.strip()]
        bad_words.update(words)
        logging.info(f"Загружено {len(bad_words)} запрещённых слов.")
    except FileNotFoundError:
        logging.warning(f"Файл {BAD_WORDS_FILE} не найден, список запрещённых слов пуст.")
    except Exception as e:
        logging.error(f"Ошибка загрузки запрещённых слов: {e}")

def save_bad_words():
    try:
        with open(BAD_WORDS_FILE, "w", encoding="utf-8") as f:
            for word in bad_words:
                f.write(word + "\n")
        logging.info("Список запрещённых слов сохранён.")
    except Exception as e:
        logging.error(f"Ошибка сохранения списка слов: {e}")

# ---------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ----------
def contains_bad_words(text: str) -> bool:
    if not text:
        return False
    text_lower = text.lower()
    for word in bad_words:
        if word in text_lower:
            return True
    return False

def contains_link(text: str) -> bool:
    url_pattern = re.compile(r'(https?://|www\.)\S+', re.IGNORECASE)
    return bool(url_pattern.search(text))

def is_flooding(user_id: int) -> bool:
    now = datetime.now()
    timestamps = user_messages[user_id]
    cutoff = now - timedelta(seconds=FLOOD_WINDOW)
    timestamps = [ts for ts in timestamps if ts > cutoff]
    user_messages[user_id] = timestamps
    timestamps.append(now)
    return len(timestamps) > FLOOD_LIMIT

# ---------- ФУНКЦИИ ОГРАНИЧЕНИЙ ----------
async def restrict_user(update: Update, user_id: int, duration_seconds: int, reason: str):
    try:
        await update.effective_chat.restrict_member(
            user_id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=datetime.now() + timedelta(seconds=duration_seconds)
        )
        await update.message.reply_text(
            f"🚫 Пользователь {update.effective_user.mention_html()} получил ограничение на {duration_seconds} секунд.\nПричина: {reason}",
            parse_mode=ParseMode.HTML
        )
        if LOG_CHAT_ID:
            await update.get_bot().send_message(
                LOG_CHAT_ID,
                f"🚫 Заблокирован {update.effective_user.mention_html()} в {update.effective_chat.title}.\nПричина: {reason}",
                parse_mode=ParseMode.HTML
            )
    except Exception as e:
        logging.error(f"Не удалось ограничить пользователя {user_id}: {e}")

async def kick_user(update: Update, user_id: int, reason: str):
    try:
        await update.effective_chat.ban_member(user_id)
        await update.effective_chat.unban_member(user_id)
        await update.message.reply_text(
            f"👢 Пользователь {update.effective_user.mention_html()} был удалён из чата.\nПричина: {reason}",
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

# ---------- ОБРАБОТЧИК СООБЩЕНИЙ ----------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Главная проверка сообщений (антифлуд, запрещённые слова, ссылки, медиа)."""
    if not update.effective_chat or not update.effective_user:
        return

    chat = update.effective_chat
    user = update.effective_user
    message = update.effective_message

    # Игнорируем сообщения от ботов
    if user.is_bot:
        return

    # Проверка прав бота
    try:
        bot_member = await chat.get_member(context.bot.id)
        if not bot_member.can_restrict_members:
            await message.reply_text("❌ У бота нет прав на удаление сообщений и ограничение пользователей. Назначьте его администратором с правами на блокировку.")
            return
    except Exception:
        return

    # Антифлуд
    if is_flooding(user.id):
        await restrict_user(update, user.id, FLOOD_MUTE_DURATION, "Флуд")
        try:
            await message.delete()
        except:
            pass
        return

    # Запрещённые слова
    if BLOCK_BAD_WORDS and message.text and contains_bad_words(message.text):
        await restrict_user(update, user.id, FLOOD_MUTE_DURATION, "Запрещённое слово")
        try:
            await message.delete()
        except:
            pass
        return

    # Ссылки
    if BLOCK_LINKS and message.text and contains_link(message.text):
        await restrict_user(update, user.id, FLOOD_MUTE_DURATION, "Запрещённые ссылки")
        try:
            await message.delete()
        except:
            pass
        return

    # Медиа
    if BLOCK_MEDIA:
        if (message.photo or message.video or message.document or message.voice or
            message.audio or message.animation or message.sticker):
            await restrict_user(update, user.id, FLOOD_MUTE_DURATION, "Медиафайлы запрещены")
            try:
                await message.delete()
            except:
                pass
            return

# ---------- ОБРАБОТЧИК НОВЫХ УЧАСТНИКОВ ----------
async def handle_new_chat_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for member in update.message.new_chat_members:
        if member.id == context.bot.id:
            await update.message.reply_text(
                "🤖 *Бот-защитник активирован!*\n\n"
                "Я буду следить за порядком: блокировать флуд, запрещённые слова, ссылки и медиа.\n\n"
                "Команды:\n"
                "/settings – показать текущие настройки\n"
                "/addword <слово> – добавить слово в чёрный список\n"
                "/delword <слово> – удалить слово из чёрного списка\n"
                "/setflood <лимит> <окно> – настроить антифлуд\n"
                "/mute [секунды] – замутить пользователя (ответом на сообщение)\n"
                "/kick – кикнуть пользователя (ответом на сообщение)",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await update.message.reply_text(f"Добро пожаловать, {member.full_name}! Пожалуйста, соблюдайте правила чата.")

# ---------- КОМАНДЫ ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветственное сообщение в личных сообщениях или группе."""
    await update.message.reply_text(
        "👋 Привет! Я бот-защитник чатов.\n"
        "Добавьте меня в группу и назначьте администратором с правами на блокировку пользователей.\n\n"
        "В группе используйте /help для списка команд."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Список доступных команд."""
    text = (
        "📋 *Доступные команды:*\n\n"
        "/start – приветствие\n"
        "/help – эта справка\n"
        "/settings – текущие настройки\n"
        "/addword <слово> – добавить слово в чёрный список\n"
        "/delword <слово> – удалить слово из чёрного списка\n"
        "/setflood <лимит> <окно> – настроить антифлуд\n"
        "/mute [секунды] – замутить пользователя (ответом)\n"
        "/kick – кикнуть пользователя (ответом)\n\n"
        "⚠️ Команды настройки доступны только администраторам группы."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать текущие настройки."""
    text = (
        "⚙️ *Текущие настройки бота:*\n"
        f"• Антифлуд: {FLOOD_LIMIT} сообщений за {FLOOD_WINDOW} сек → мут {FLOOD_MUTE_DURATION} сек\n"
        f"• Блокировка ссылок: {'✅' if BLOCK_LINKS else '❌'}\n"
        f"• Блокировка медиа: {'✅' if BLOCK_MEDIA else '❌'}\n"
        f"• Блокировка слов: {'✅' if BLOCK_BAD_WORDS else '❌'} (загружено {len(bad_words)} слов)"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def add_word_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return
    if not context.args:
        await update.message.reply_text("Укажите слово: /addword слово")
        return
    word = context.args[0].lower()
    bad_words.add(word)
    save_bad_words()
    await update.message.reply_text(f"✅ Слово '{word}' добавлено в чёрный список.")

async def del_word_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return
    if not context.args:
        await update.message.reply_text("Укажите слово: /delword слово")
        return
    word = context.args[0].lower()
    if word in bad_words:
        bad_words.remove(word)
        save_bad_words()
        await update.message.reply_text(f"✅ Слово '{word}' удалено из чёрного списка.")
    else:
        await update.message.reply_text(f"❌ Слово '{word}' не найдено в чёрном списке.")

async def set_flood_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        await update.message.reply_text(f"✅ Антифлуд настроен: {limit} сообщений за {window} секунд.")
    except ValueError:
        await update.message.reply_text("Ошибка: лимит и окно должны быть числами.")

async def mute_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("Ответьте на сообщение пользователя командой /mute [секунды]")
        return
    user_id = update.message.reply_to_message.from_user.id
    duration = int(context.args[0]) if context.args and context.args[0].isdigit() else 60
    await restrict_user(update, user_id, duration, "Мут по команде администратора")

async def kick_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("Ответьте на сообщение пользователя командой /kick")
        return
    user_id = update.message.reply_to_message.from_user.id
    await kick_user(update, user_id, "Кик по команде администратора")

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

# ---------- ЗАПУСК БОТА ----------
def main():
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO
    )
    load_bad_words()

    application = Application.builder().token(TOKEN).build()

    # Обработчики сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, handle_message))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_chat_members))

    # Команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("addword", add_word_command))
    application.add_handler(CommandHandler("delword", del_word_command))
    application.add_handler(CommandHandler("setflood", set_flood_command))
    application.add_handler(CommandHandler("mute", mute_command))
    application.add_handler(CommandHandler("kick", kick_command))

    application.run_polling()

if __name__ == "__main__":
    main()
