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

data = {"groups": {}, "admins": [ADMIN_ID]}
user_messages = defaultdict(list)
pending_links = {}

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
            "tariff": "free",
            "flood_limit": 5,
            "flood_window": 10,
            "admins": [],
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

async def restrict(chat_id, user_id, context):
    try:
        await context.bot.restrict_chat_member(
            chat_id,
            user_id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=datetime.now() + timedelta(seconds=60)
        )
    except:
        pass

# ---------- CORE ----------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    msg = update.effective_message

    if not chat:
        return

    if chat.type in ("group", "supergroup"):
        group = get_group(chat.id)

    if not user or user.is_bot:
        return

    # антифлуд
    if is_flood(user.id, chat.id):
        await restrict(chat.id, user.id, context)
        return

    # ссылки
    if msg.text and contains_link(msg.text):
        await msg.delete()

        link_id = f"{chat.id}_{user.id}_{int(datetime.now().timestamp())}"
        pending_links[link_id] = {
            "chat_id": chat.id,
            "user_id": user.id,
            "text": msg.text
        }

        # уведомление в группе
        await context.bot.send_message(
            chat.id,
            "🔍 Ссылка отправлена на проверку"
        )

        # отправка админам группы
        for admin_id in group["admins"]:
            keyboard = [
                [
                    InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_{link_id}"),
                    InlineKeyboardButton("❌ Отклонить", callback_data=f"decline_{link_id}")
                ]
            ]

            try:
                await context.bot.send_message(
                    admin_id,
                    f"Проверка ссылки:\n{msg.text}\nОт: {user.id}",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            except:
                pass

# ---------- МЕНЮ ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📋 Группы", callback_data="groups")],
        [InlineKeyboardButton("⚙️ Настройки", callback_data="settings")],
    ]

    if update.effective_user.id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("👑 Админ панель", callback_data="admin")])

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
                InlineKeyboardButton(f"Группа {gid}", callback_data=f"group_{gid}")
            ])
        await query.edit_message_text("Твои группы:", reply_markup=InlineKeyboardMarkup(keyboard))

    # выбор группы
    elif data_cb.startswith("group_"):
        chat_id = int(data_cb.split("_")[1])
        keyboard = [
            [InlineKeyboardButton("Антиспам", callback_data=f"spam_{chat_id}")],
            [InlineKeyboardButton("Админы", callback_data=f"admins_{chat_id}")]
        ]
        await query.edit_message_text("Управление группой:", reply_markup=InlineKeyboardMarkup(keyboard))

    # антиспам настройки
    elif data_cb.startswith("spam_"):
        chat_id = int(data_cb.split("_")[1])
        settings = get_group(chat_id)

        text = f"Антиспам:\nЛимит: {settings['flood_limit']}\nОкно: {settings['flood_window']} сек"

        keyboard = [
            [InlineKeyboardButton("+1 лимит", callback_data=f"inc_limit_{chat_id}")],
            [InlineKeyboardButton("+5 сек", callback_data=f"inc_time_{chat_id}")]
        ]

        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data_cb.startswith("inc_limit_"):
        chat_id = int(data_cb.split("_")[2])
        get_group(chat_id)["flood_limit"] += 1
        save_data()
        await query.answer("Лимит увеличен")

    elif data_cb.startswith("inc_time_"):
        chat_id = int(data_cb.split("_")[2])
        get_group(chat_id)["flood_window"] += 5
        save_data()
        await query.answer("Окно увеличено")

    # админы группы
    elif data_cb.startswith("admins_"):
        chat_id = int(data_cb.split("_")[1])
        group = get_group(chat_id)

        text = "Админы:\n" + "\n".join(map(str, group["admins"]))
        await query.edit_message_text(text)

    # одобрение
    elif data_cb.startswith("approve_"):
        link_id = data_cb.split("_", 1)[1]

        if link_id in pending_links:
            info = pending_links[link_id]

            await context.bot.send_message(
                info["chat_id"],
                f"✅ Одобрено:\n{info['text']}\nID: {info['user_id']}"
            )

            del pending_links[link_id]
            await query.edit_message_text("Одобрено")

    # отклонение
    elif data_cb.startswith("decline_"):
        link_id = data_cb.split("_", 1)[1]

        if link_id in pending_links:
            del pending_links[link_id]
            await query.edit_message_text("❌ Отклонено")

    # админ панель
    elif data_cb == "admin" and update.effective_user.id == ADMIN_ID:
        text = f"👑 Админ панель\nГрупп: {len(data['groups'])}"
        await query.edit_message_text(text)

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
