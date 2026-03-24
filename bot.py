import json
import logging
import os
import requests
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

TOKEN = "8768850938:AAGXlxCENVXIqUXAJMBG2bl2xgUwNAJOc4Q"
CRYPTO_TOKEN = "555209:AAvWWWiQt0ERfGAjTGozQDu1HEAZICFi4ZW"

ADMIN_ID = 2032012311
DATA_FILE = "bot_data.json"

PRICES = {
    "standard": 1.0,
    "pro": 2.0
}

data: Dict = {"groups": {}, "admins": [ADMIN_ID]}
user_messages: Dict[int, List[datetime]] = defaultdict(list)
payments: Dict = {}

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
            "stats": {"messages": 0, "violations": 0}
        }
        save_data()
    return data["groups"][cid]

# ---------- CRYPTO ----------
def create_invoice(amount, desc):
    url = "https://pay.crypt.bot/api/createInvoice"
    headers = {"Crypto-Pay-API-Token": CRYPTO_TOKEN}
    payload = {
        "asset": "USDT",
        "amount": amount,
        "description": desc
    }
    r = requests.post(url, json=payload, headers=headers).json()
    if r.get("ok"):
        return r["result"]
    return None

def check_invoice(invoice_id):
    url = "https://pay.crypt.bot/api/getInvoices"
    headers = {"Crypto-Pay-API-Token": CRYPTO_TOKEN}
    params = {"invoice_ids": invoice_id}
    r = requests.get(url, headers=headers, params=params).json()
    if r.get("ok"):
        items = r["result"]["items"]
        if items and items[0]["status"] == "paid":
            return True
    return False

# ---------- АНТИСПАМ ----------
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

    if not chat:
        return

    if chat.type in ("group", "supergroup"):
        get_group(chat.id)

    if not user or user.is_bot:
        return

    settings = get_group(chat.id)
    settings["stats"]["messages"] += 1
    save_data()

    if is_flood(user.id, chat.id):
        await restrict(chat.id, user.id, context)
        settings["stats"]["violations"] += 1
        save_data()

# ---------- MENU ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await menu(update, context)

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = []
    for gid in data["groups"]:
        keyboard.append([InlineKeyboardButton(f"Группа {gid}", callback_data=f"group_{gid}")])

    await update.message.reply_text("Выбери группу:", reply_markup=InlineKeyboardMarkup(keyboard))

# ---------- ГРУППА ----------
async def group_menu(query, chat_id):
    settings = get_group(chat_id)

    text = f"Группа {chat_id}\nТариф: {settings['tariff']}"

    keyboard = [
        [InlineKeyboardButton("Сменить тариф", callback_data=f"tariff_{chat_id}")]
    ]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

# ---------- ТАРИФ ----------
async def tariff_menu(query, chat_id):
    keyboard = [
        [InlineKeyboardButton("FREE", callback_data=f"set_free_{chat_id}")],
        [InlineKeyboardButton("STANDARD", callback_data=f"buy_standard_{chat_id}")],
        [InlineKeyboardButton("PRO", callback_data=f"buy_pro_{chat_id}")]
    ]
    await query.edit_message_text("Выбери тариф:", reply_markup=InlineKeyboardMarkup(keyboard))

# ---------- ПОКУПКА ----------
async def buy_tariff(query, chat_id, tariff):
    price = PRICES[tariff]
    invoice = create_invoice(price, f"{tariff} for {chat_id}")

    if not invoice:
        await query.edit_message_text("Ошибка оплаты")
        return

    invoice_id = str(invoice["invoice_id"])
    payments[invoice_id] = {"chat_id": chat_id, "tariff": tariff}

    keyboard = [
        [InlineKeyboardButton("💳 Оплатить", url=invoice["pay_url"])],
        [InlineKeyboardButton("✅ Проверить", callback_data=f"check_{invoice_id}")]
    ]

    await query.edit_message_text("Оплати и нажми проверить", reply_markup=InlineKeyboardMarkup(keyboard))

# ---------- CALLBACK ----------
async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data_cb = query.data

    if data_cb.startswith("group_"):
        await group_menu(query, int(data_cb.split("_")[1]))

    elif data_cb.startswith("tariff_"):
        await tariff_menu(query, int(data_cb.split("_")[1]))

    elif data_cb.startswith("set_free_"):
        chat_id = int(data_cb.split("_")[2])
        get_group(chat_id)["tariff"] = "free"
        save_data()
        await query.edit_message_text("FREE активирован")

    elif data_cb.startswith("buy_"):
        parts = data_cb.split("_")
        tariff = parts[1]
        chat_id = int(parts[2])
        await buy_tariff(query, chat_id, tariff)

    elif data_cb.startswith("check_"):
        invoice_id = data_cb.split("_")[1]

        if invoice_id not in payments:
            await query.edit_message_text("Платеж не найден")
            return

        if check_invoice(invoice_id):
            info = payments[invoice_id]
            chat_id = info["chat_id"]
            tariff = info["tariff"]

            get_group(chat_id)["tariff"] = tariff
            save_data()

            del payments[invoice_id]

            await query.edit_message_text(f"✅ Оплата прошла! Тариф {tariff}")
        else:
            await query.edit_message_text("❌ Не оплачено")

# ---------- MAIN ----------
def main():
    logging.basicConfig(level=logging.INFO)
    load_data()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))

    app.add_handler(CallbackQueryHandler(buttons))

    app.add_handler(MessageHandler(filters.ALL, handle_message))

    print("Бот запущен")
    app.run_polling()

if __name__ == "__main__":
    main()
