import json
import logging
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ChatPermissions
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

TOKEN = "8768850938:AAGXlxCENVXIqUXAJMBG2bl2xgUwNAJOc4Q"
ADMIN_ID = 2032012311
DATA_FILE = "bot_data.json"

data = {"groups": {}}
user_messages = defaultdict(list)
user_states = {}  # для ввода ID

# ---------- DATA ----------
def load_data():
    global data
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

def save_data():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def get_group(chat_id):
    cid = str(chat_id)
    if cid not in data["groups"]:
        data["groups"][cid] = {
            "flood_limit": 5,
            "flood_window": 10,
            "links": True,
            "files": False
        }
        save_data()
    return data["groups"][cid]

# ---------- UTILS ----------
def contains_link(text):
    return bool(re.search(r'(https?://|t\.me|www\.)\S+', text))

def is_flood(user_id, chat_id):
    settings = get_group(chat_id)
    now = datetime.now()

    msgs = user_messages[user_id]
    msgs = [m for m in msgs if m > now - timedelta(seconds=settings["flood_window"])]
    msgs.append(now)
    user_messages[user_id] = msgs

    return len(msgs) > settings["flood_limit"]

# ---------- CORE ----------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    msg = update.effective_message

    if not chat:
        return

    if chat.type in ("group", "supergroup"):
        get_group(chat.id)

    # обработка ввода ID
    if user.id in user_states:
        state = user_states[user.id]

        if state == "await_group_id":
            try:
                chat_id = int(msg.text)

                chat_obj = await context.bot.get_chat(chat_id)

                admins = await context.bot.get_chat_administrators(chat_id)

                owner = None
                admin_list = []

                for a in admins:
                    if a.status == "creator":
                        owner = a.user.id
                    else:
                        admin_list.append(a.user.id)

                text = (
                    f"📊 Информация о группе\n"
                    f"Название: {chat_obj.title}\n"
                    f"ID: {chat_id}\n\n"
                    f"👑 Владелец: {owner}\n"
                    f"🛡 Админы: {admin_list}\n"
                )

                await msg.reply_text(text)

            except Exception as e:
                await msg.reply_text(f"Ошибка: {e}")

            del user_states[user.id]
        return

    if not user or user.is_bot:
        return

    settings = get_group(chat.id)

    if is_flood(user.id, chat.id):
        await msg.delete()
        return

    if msg.text and contains_link(msg.text) and settings["links"]:
        await msg.delete()
        await context.bot.send_message(chat.id, "🔍 Ссылка на проверке")

    if settings["files"]:
        if msg.document or msg.video or msg.photo:
            await msg.delete()
            await context.bot.send_message(chat.id, "📁 Файл на проверке")

# ---------- UI ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📋 Группы", callback_data="groups")],
        [InlineKeyboardButton("⚙️ Настройки", callback_data="settings")],
        [InlineKeyboardButton("👑 Админ панель", callback_data="admin")]
    ]

    await update.message.reply_text(
        "Главное меню:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ---------- CALLBACK ----------
async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data_cb = query.data

    # список групп
    if data_cb == "groups":
        keyboard = []
        for gid in data["groups"]:
            keyboard.append([
                InlineKeyboardButton(f"{gid}", callback_data=f"group_{gid}")
            ])

        await query.edit_message_text("Группы:", reply_markup=InlineKeyboardMarkup(keyboard))

    # управление группой
    elif data_cb.startswith("group_"):
        chat_id = int(data_cb.split("_")[1])
        settings = get_group(chat_id)

        text = (
            f"Группа {chat_id}\n"
            f"Антиспам: {settings['flood_limit']}/{settings['flood_window']}\n"
            f"Ссылки: {settings['links']}\n"
            f"Файлы: {settings['files']}"
        )

        keyboard = [
            [InlineKeyboardButton("➕ Лимит", callback_data=f"limit_{chat_id}")],
            [InlineKeyboardButton("⏱ Окно", callback_data=f"time_{chat_id}")],
            [InlineKeyboardButton("🔗 Ссылки ON/OFF", callback_data=f"links_{chat_id}")],
            [InlineKeyboardButton("📁 Файлы ON/OFF", callback_data=f"files_{chat_id}")]
        ]

        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    # изменение лимита
    elif data_cb.startswith("limit_"):
        chat_id = int(data_cb.split("_")[1])
        get_group(chat_id)["flood_limit"] += 1
        save_data()
        await query.answer("Лимит увеличен")

    # изменение окна
    elif data_cb.startswith("time_"):
        chat_id = int(data_cb.split("_")[1])
        get_group(chat_id)["flood_window"] += 5
        save_data()
        await query.answer("Окно увеличено")

    # toggle ссылки
    elif data_cb.startswith("links_"):
        chat_id = int(data_cb.split("_")[1])
        g = get_group(chat_id)
        g["links"] = not g["links"]
        save_data()
        await query.answer("Переключено")

    # toggle файлы
    elif data_cb.startswith("files_"):
        chat_id = int(data_cb.split("_")[1])
        g = get_group(chat_id)
        g["files"] = not g["files"]
        save_data()
        await query.answer("Переключено")

    # админ панель
    elif data_cb == "admin":
        keyboard = [
            [InlineKeyboardButton("📊 Статистика", callback_data="stats")],
            [InlineKeyboardButton("ℹ️ Инфо о группе", callback_data="group_info")]
        ]
        await query.edit_message_text("Админ панель:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data_cb == "stats":
        await query.edit_message_text(f"Групп: {len(data['groups'])}")

    elif data_cb == "group_info":
        user_states[query.from_user.id] = "await_group_id"
        await query.message.reply_text("Введи ID группы:")

# ---------- MAIN ----------
def main():
    logging.basicConfig(level=logging.INFO)
    load_data()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.ALL, handle_message))

    print("Бот запущен")
    app.run_polling()

if __name__ == "__main__":
    main()
