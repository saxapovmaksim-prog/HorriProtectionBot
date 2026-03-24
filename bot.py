import asyncio
import json
import logging
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List

from telegram import Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

TOKEN = "YOUR_BOT_TOKEN"
DATA_FILE = "bot_data.json"
ADMIN_ID = 2032012311

data: Dict = {"groups": {}, "admins": [ADMIN_ID]}
user_messages: Dict[int, List[datetime]] = defaultdict(list)

# ---------- DATA ----------
def load_data():
    global data
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {"groups": {}, "admins": [ADMIN_ID]}

def save_data():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def get_group_settings(chat_id: int):
    cid = str(chat_id)
    if cid not in data["groups"]:
        data["groups"][cid] = {
            "flood_limit": 5,
            "flood_window": 10,
            "flood_mute": 60,
            "block_links": True,
            "block_media": True,
            "invite_links_block": True,
            "caps_filter": True,
            "stats": {"messages": 0, "violations": 0}
        }
        save_data()
    return data["groups"][cid]

# ---------- UTILS ----------
def contains_link(text):
    return bool(re.search(r'(https?://|www\.)\S+', text))

def contains_invite_link(text):
    return bool(re.search(r't\.me', text))

def is_caps_abuse(text):
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    return sum(1 for c in letters if c.isupper()) / len(letters) > 0.7

def is_flooding(user_id, chat_id):
    settings = get_group_settings(chat_id)
    now = datetime.now()
    msgs = user_messages[user_id]
    msgs = [m for m in msgs if m > now - timedelta(seconds=settings["flood_window"])]
    msgs.append(now)
    user_messages[user_id] = msgs
    return len(msgs) > settings["flood_limit"]

async def is_admin(chat_id, user_id, context):
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except:
        return False

async def restrict_user(chat_id, user_id, duration, reason, context):
    try:
        await context.bot.restrict_chat_member(
            chat_id,
            user_id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=datetime.now() + timedelta(seconds=duration)
        )
        await context.bot.send_message(chat_id, f"🚫 {user_id} мут на {duration} сек ({reason})")
    except Exception as e:
        logging.error(e)

# ---------- CORE ----------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    message = update.effective_message

    if not chat:
        return

    # 🔥 регистрация любой группы
    if chat.type in ("group", "supergroup"):
        if str(chat.id) not in data["groups"]:
            try:
                bot_member = await chat.get_member(context.bot.id)
                if bot_member.status in ("administrator", "member"):
                    get_group_settings(chat.id)
                    logging.info(f"NEW GROUP: {chat.id}")
            except:
                pass

    if not user or user.is_bot:
        return

    if str(chat.id) not in data["groups"]:
        return

    settings = get_group_settings(chat.id)
    settings["stats"]["messages"] += 1
    save_data()

    if await is_admin(chat.id, user.id, context):
        return

    try:
        bot_member = await chat.get_member(context.bot.id)
        if not bot_member.can_restrict_members:
            return
    except:
        return

    # антифлуд
    if is_flooding(user.id, chat.id):
        await restrict_user(chat.id, user.id, settings["flood_mute"], "Флуд", context)
        if message:
            await message.delete()
        return

    if message and message.text:
        text = message.text

        if settings["block_links"] and contains_link(text):
            await restrict_user(chat.id, user.id, 60, "Ссылка", context)
            await message.delete()
            return

        if settings["invite_links_block"] and contains_invite_link(text):
            await restrict_user(chat.id, user.id, 60, "Инвайт", context)
            await message.delete()
            return

        if settings["caps_filter"] and is_caps_abuse(text):
            await restrict_user(chat.id, user.id, 60, "CAPS", context)
            await message.delete()
            return

    if message and settings["block_media"]:
        if any((message.photo, message.video, message.document,
                message.voice, message.audio, message.animation, message.sticker)):
            await restrict_user(chat.id, user.id, 60, "Медиа", context)
            await message.delete()

# ---------- COMMANDS ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Бот работает. /mygroups")

async def my_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not data["groups"]:
        await update.message.reply_text("Нет групп")
        return

    text = "Группы:\n"
    for gid in data["groups"]:
        try:
            chat = await context.bot.get_chat(int(gid))
            text += f"{chat.title} ({gid})\n"
        except:
            text += f"{gid}\n"

    await update.message.reply_text(text)

# ---------- MAIN ----------
def main():
    logging.basicConfig(level=logging.INFO)
    load_data()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(MessageHandler(filters.ALL, handle_message), group=0)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("mygroups", my_groups))

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
