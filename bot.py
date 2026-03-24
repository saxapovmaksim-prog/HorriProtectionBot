import asyncio
import json
import logging
import os
import re
import requests
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ChatPermissions, Chat
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

# ---------- КОНФИГУРАЦИЯ ----------
TOKEN = "8768850938:AAGXlxCENVXIqUXAJMBG2bl2xgUwNAJOc4Q"
CRYPTOBOT_TOKEN = "555209:AAvWWWiQt0ERfGAjTGozQDu1HEAZICFi4ZW"
ADMIN_ID = 2032012311
DATA_FILE = "bot_data.json"
USER_DATA_FILE = "user_data.json"

PRICES_RUB = {"standard": 99, "pro": 199}
PRICES_USD = {"standard": 0.99, "pro": 1.99}

TARIFF_FEATURES = {
    "free": {
        "block_links": True,
        "block_media": False,
        "custom_welcome": False,
        "check_files": False,
        "check_content": False,
        "caps_filter": False,
        "invite_links_block": True
    },
    "standard": {
        "block_links": True,
        "block_media": True,
        "custom_welcome": True,
        "check_files": True,
        "check_content": False,
        "caps_filter": True,
        "invite_links_block": True
    },
    "pro": {
        "block_links": True,
        "block_media": True,
        "custom_welcome": True,
        "check_files": True,
        "check_content": True,
        "caps_filter": True,
        "invite_links_block": True
    }
}

DEFAULT_SETTINGS = {
    "flood_limit": 5,
    "flood_window": 10,
    "flood_mute": 60,
    "block_links": True,
    "block_media": False,
    "custom_welcome": None,
    "check_files": False,
    "check_content": False,
    "caps_filter": False,
    "invite_links_block": True,
    "stats": {"messages": 0, "violations": 0}
}

# ---------- ГЛОБАЛЬНЫЕ ДАННЫЕ ----------
data: Dict = {"groups": {}}
user_data: Dict = {}
user_messages: Dict[int, List[datetime]] = defaultdict(list)
pending_payments: Dict[str, dict] = {}
user_states: Dict[int, str] = {}

# ---------- ЗАГРУЗКА / СОХРАНЕНИЕ ----------
def load_data():
    global data, user_data
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for cid in data.get("groups", {}):
                g = data["groups"][cid]
                for key, val in DEFAULT_SETTINGS.items():
                    g.setdefault(key, val)
                g.setdefault("stats", {"messages": 0, "violations": 0})
        except Exception as e:
            logging.error(f"Ошибка загрузки групп: {e}")
            data = {"groups": {}}
    else:
        data = {"groups": {}}

    if os.path.exists(USER_DATA_FILE):
        try:
            with open(USER_DATA_FILE, "r", encoding="utf-8") as f:
                user_data = json.load(f)
        except Exception as e:
            logging.error(f"Ошибка загрузки пользователей: {e}")
            user_data = {}
    else:
        user_data = {}

def save_data():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Ошибка сохранения групп: {e}")

def save_user_data():
    try:
        with open(USER_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(user_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Ошибка сохранения пользователей: {e}")

def get_group_settings(chat_id: int) -> Dict:
    cid = str(chat_id)
    if cid not in data["groups"]:
        settings = DEFAULT_SETTINGS.copy()
        data["groups"][cid] = settings
        save_data()
        logging.info(f"Создана запись для группы {chat_id}")
    return data["groups"][cid]

def set_group_settings(chat_id: int, settings: Dict):
    data["groups"][str(chat_id)] = settings
    save_data()

def update_group_setting(chat_id: int, key: str, value):
    settings = get_group_settings(chat_id)
    settings[key] = value
    set_group_settings(chat_id, settings)

def register_user(user_id: int) -> Dict:
    uid = str(user_id)
    if uid not in user_data:
        user_data[uid] = {
            "registered": datetime.now().isoformat(),
            "tariff": "free",
            "expiry": None
        }
        save_user_data()
        logging.info(f"Зарегистрирован новый пользователь {user_id}")
    return user_data[uid]

def get_user_tariff(user_id: int) -> str:
    uid = str(user_id)
    if uid not in user_data:
        register_user(user_id)
    user = user_data[uid]
    if user["tariff"] != "free" and user["expiry"]:
        expiry = datetime.fromisoformat(user["expiry"])
        if datetime.now() > expiry:
            user["tariff"] = "free"
            user["expiry"] = None
            save_user_data()
            logging.info(f"Тариф пользователя {user_id} истёк, сброшен на бесплатный")
    return user["tariff"]

def set_user_tariff(user_id: int, tariff: str, duration_days: int = 30):
    uid = str(user_id)
    if uid not in user_data:
        register_user(user_id)
    user = user_data[uid]
    user["tariff"] = tariff
    user["expiry"] = (datetime.now() + timedelta(days=duration_days)).isoformat()
    save_user_data()

# ---------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ----------
def contains_link(text: str) -> bool:
    return bool(re.search(r'(https?://|www\.)\S+', text, re.IGNORECASE))

def contains_invite_link(text: str) -> bool:
    patterns = [
        r'(?:https?://)?t\.me/joinchat/\S+',
        r'(?:https?://)?t\.me/\+[\w-]+',
        r'(?:https?://)?t\.me/c/\d+/\d+',
        r'(?:https?://)?t\.me/join\b'
    ]
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)

def is_caps_abuse(text: str, threshold: float = 0.7) -> bool:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    uppercase = sum(1 for c in letters if c.isupper())
    return (uppercase / len(letters)) > threshold

def is_flooding(user_id: int, chat_id: int) -> bool:
    settings = get_group_settings(chat_id)
    limit = settings["flood_limit"]
    window = settings["flood_window"]
    now = datetime.now()
    timestamps = user_messages[user_id]
    cutoff = now - timedelta(seconds=window)
    timestamps = [ts for ts in timestamps if ts > cutoff]
    user_messages[user_id] = timestamps
    timestamps.append(now)
    return len(timestamps) > limit

async def is_group_admin(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except:
        return False

async def restrict_user(chat_id: int, user_id: int, duration: int, reason: str, context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.bot.restrict_chat_member(
            chat_id, user_id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=datetime.now() + timedelta(seconds=duration)
        )
        settings = get_group_settings(chat_id)
        settings["stats"]["violations"] += 1
        set_group_settings(chat_id, settings)
        await context.bot.send_message(
            chat_id,
            f"🚫 Пользователь {user_id} получил ограничение на {duration} сек.\nПричина: {reason}"
        )
    except Exception as e:
        logging.error(f"Не удалось ограничить пользователя {user_id} в чате {chat_id}: {e}")

# ---------- ЗАЩИТА СООБЩЕНИЙ ----------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not update.effective_user:
        return
    chat = update.effective_chat
    user = update.effective_user
    message = update.effective_message

    # Логируем полученное сообщение
    logging.info(f"Получено сообщение от {user.id} в чате {chat.id} (тип {chat.type})")

    if user.is_bot:
        return

    # Если это группа, применяем защиту
    if chat.type in ("group", "supergroup"):
        # Убеждаемся, что настройки есть
        settings = get_group_settings(chat.id)

        # Статистика
        settings["stats"]["messages"] += 1
        set_group_settings(chat.id, settings)

        # Если пользователь админ – пропускаем проверки
        if await is_group_admin(chat.id, user.id, context):
            return

        # Проверка прав бота
        try:
            bot_member = await chat.get_member(context.bot.id)
            if not bot_member.can_restrict_members:
                return
        except:
            return

        # Антифлуд
        if is_flooding(user.id, chat.id):
            await restrict_user(chat.id, user.id, settings["flood_mute"], "Флуд", context)
            try:
                await message.delete()
            except:
                pass
            return

        # Блокировка обычных ссылок
        if settings.get("block_links", True) and message.text and contains_link(message.text):
            await restrict_user(chat.id, user.id, 60, "Запрещённые ссылки", context)
            try:
                await message.delete()
            except:
                pass
            return

        # Блокировка инвайт-ссылок
        if settings.get("invite_links_block", True) and message.text and contains_invite_link(message.text):
            await restrict_user(chat.id, user.id, 60, "Запрещённые инвайт-ссылки", context)
            try:
                await message.delete()
            except:
                pass
            return

        # CAPS (если включено)
        if settings.get("caps_filter", False) and message.text and is_caps_abuse(message.text):
            await restrict_user(chat.id, user.id, 60, "Злоупотребление заглавными буквами", context)
            try:
                await message.delete()
            except:
                pass
            return

        # Блокировка медиа (если включено)
        if settings.get("block_media", False) and any((message.photo, message.video, message.document,
                                                       message.voice, message.audio, message.animation, message.sticker)):
            await restrict_user(chat.id, user.id, 60, "Медиафайлы запрещены", context)
            try:
                await message.delete()
            except:
                pass
            return

        # Отправка файлов на проверку
        if settings.get("check_files", False) and message.document:
            await message.reply_text("📁 Файл отправлен на проверку")
            await message.delete()
    # Для личных сообщений ничего не делаем, они будут обработаны handle_text

# ---------- НОВЫЕ УЧАСТНИКИ ----------
async def handle_new_chat_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    for member in update.message.new_chat_members:
        if member.id == context.bot.id:
            get_group_settings(chat.id)
            await update.message.reply_text(
                "🤖 *Бот-защитник активирован!*\n\n"
                "Для настройки используйте /menu в личных сообщениях.\n"
                "По умолчанию активны базовые функции.",
                parse_mode="Markdown"
            )
            return

    settings = get_group_settings(chat.id)
    if settings.get("custom_welcome"):
        for member in update.message.new_chat_members:
            welcome = settings["custom_welcome"] or f"Добро пожаловать, {member.full_name}!"
            await update.message.reply_text(welcome)

# ---------- МЕНЮ ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    register_user(user_id)
    await menu(update, context)

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    keyboard = [
        [InlineKeyboardButton("📋 Мои группы", callback_data="groups")],
        [InlineKeyboardButton("💰 Тарифы", callback_data="show_tariffs")],
    ]
    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("👑 Админ панель", callback_data="admin_panel")])
    await update.message.reply_text(
        "👋 *Главное меню*\nВыберите действие:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

# ---------- СПИСОК ГРУПП ----------
async def show_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not data["groups"]:
        await query.edit_message_text("Нет добавленных групп.")
        return
    keyboard = []
    for cid in data["groups"]:
        try:
            chat = await context.bot.get_chat(int(cid))
            name = chat.title or f"Группа {cid}"
        except:
            name = f"Группа {cid}"
        keyboard.append([InlineKeyboardButton(name, callback_data=f"group_{cid}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="main_menu")])
    await query.edit_message_text("📋 *Список групп:*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def show_group_settings(query, chat_id: int):
    user_id = query.from_user.id
    if not await is_group_admin(chat_id, user_id, query.message.get_bot()):
        await query.answer("⛔ Только администраторы группы могут настраивать бота.", show_alert=True)
        return

    user_tariff = get_user_tariff(user_id)
    settings = get_group_settings(chat_id)
    allowed_features = TARIFF_FEATURES[user_tariff]

    text = (
        f"*Группа:* `{chat_id}`\n"
        f"*Ваш тариф:* {user_tariff.upper()}\n"
        f"*Антиспам:* {settings['flood_limit']} сообщ. за {settings['flood_window']} сек\n"
        f"*Блокировка ссылок:* {'✅' if settings['block_links'] else '❌'}\n"
        f"*Блокировка инвайт-ссылок:* {'✅' if settings['invite_links_block'] else '❌'}\n"
        f"*Фильтр CAPS:* {'✅' if settings['caps_filter'] else '❌'}\n"
        f"*Блокировка медиа:* {'✅' if settings['block_media'] else '❌'}\n"
        f"*Кастомное приветствие:* {'✅' if settings['custom_welcome'] else '❌'}\n"
        f"*Проверка файлов:* {'✅' if settings['check_files'] else '❌'}\n"
        f"*Статистика:* сообщений {settings['stats']['messages']}, нарушений {settings['stats']['violations']}"
    )
    keyboard = []
    keyboard.append([InlineKeyboardButton("⚙️ Настроить антиспам", callback_data=f"configure_flood_{chat_id}")])

    if allowed_features["block_links"]:
        keyboard.append([InlineKeyboardButton("🔗 Ссылки: Вкл/Выкл", callback_data=f"toggle_links_{chat_id}")])
    if allowed_features["invite_links_block"]:
        keyboard.append([InlineKeyboardButton("🚫 Инвайт-ссылки: Вкл/Выкл", callback_data=f"toggle_invite_{chat_id}")])
    if allowed_features["caps_filter"]:
        keyboard.append([InlineKeyboardButton("🔠 CAPS фильтр: Вкл/Выкл", callback_data=f"toggle_caps_{chat_id}")])
    if allowed_features["block_media"]:
        keyboard.append([InlineKeyboardButton("📷 Медиа: Вкл/Выкл", callback_data=f"toggle_media_{chat_id}")])
    if allowed_features["custom_welcome"]:
        keyboard.append([InlineKeyboardButton("✏️ Кастомное приветствие", callback_data=f"set_welcome_{chat_id}")])
    if allowed_features["check_files"]:
        keyboard.append([InlineKeyboardButton("📁 Проверка файлов: Вкл/Выкл", callback_data=f"toggle_files_{chat_id}")])

    if user_tariff == "free":
        text += "\n\n⚠️ *Ваш тариф: бесплатный.* Чтобы включить дополнительные функции (медиа, CAPS, приветствие, проверку файлов), приобретите платный тариф в разделе «💰 Тарифы»."
    else:
        expiry = user_data[str(user_id)].get("expiry")
        if expiry:
            exp_date = datetime.fromisoformat(expiry).strftime("%d.%m.%Y")
            text += f"\n\n💎 *Тариф активен до {exp_date}*"

    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="groups")])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

# ---------- НАСТРОЙКИ ГРУППЫ ----------
async def toggle_setting(update: Update, context: ContextTypes.DEFAULT_TYPE, setting: str, chat_id: int):
    query = update.callback_query
    user_id = query.from_user.id
    user_tariff = get_user_tariff(user_id)
    allowed_features = TARIFF_FEATURES[user_tariff]
    if not allowed_features.get(setting, False):
        await query.answer("❌ Эта функция недоступна на вашем тарифе. Приобретите платный тариф в разделе «💰 Тарифы».", show_alert=True)
        return
    settings = get_group_settings(chat_id)
    new_val = not settings[setting]
    update_group_setting(chat_id, setting, new_val)
    await query.answer(f"{setting.replace('_',' ').title()} {'включена' if new_val else 'выключена'}")
    await show_group_settings(query, chat_id)

async def configure_flood(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    query = update.callback_query
    context.user_data["flood_chat"] = chat_id
    await query.edit_message_text(
        "Введите параметры антиспама в формате:\n"
        "`лимит окно_секунд длительность_мута_секунд`\n"
        "Пример: `5 10 60`\n\n"
        "Или отправьте пустое сообщение для отмены.",
        parse_mode="Markdown"
    )

async def set_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    query = update.callback_query
    user_id = query.from_user.id
    user_tariff = get_user_tariff(user_id)
    if not TARIFF_FEATURES[user_tariff]["custom_welcome"]:
        await query.answer("❌ Кастомное приветствие доступно только на платных тарифах.", show_alert=True)
        return
    context.user_data["welcome_chat"] = chat_id
    await query.edit_message_text("Введите текст приветствия (или отправьте пустое сообщение для отключения):")

async def toggle_files(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    await toggle_setting(update, context, "check_files", chat_id)

# ---------- ТАРИФЫ ----------
async def show_tariffs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    text = (
        "*Доступные тарифы:*\n\n"
        "🆓 *Бесплатный* – 0 руб.\n"
        "• Антиспам\n"
        "• Блокировка ссылок\n"
        "• Блокировка инвайт-ссылок\n\n"
        "⭐ *Стандартный* – 99 руб.\n"
        "• Блокировка медиа\n"
        "• Кастомное приветствие\n"
        "• Фильтр CAPS\n"
        "• Отправка файлов на проверку\n\n"
        "💎 *Профессиональный* – 199 руб.\n"
        "• Проверка контента (AI)\n"
        "• Все функции стандартного тарифа\n\n"
        "⏰ *Все тарифы действуют 1 месяц.*\n"
        "Для покупки выберите тариф ниже."
    )
    keyboard = [
        [InlineKeyboardButton("⭐ Стандартный (99 руб)", callback_data="buy_standard")],
        [InlineKeyboardButton("💎 Профессиональный (199 руб)", callback_data="buy_pro")],
        [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def buy_tariff(update: Update, context: ContextTypes.DEFAULT_TYPE, tariff: str):
    query = update.callback_query
    user_id = query.from_user.id
    price_rub = PRICES_RUB[tariff]
    price_usd = PRICES_USD[tariff]
    description = f"Активация тарифа {tariff.upper()} на 30 дней"
    invoice = create_crypto_invoice(price_usd, description)
    if not invoice:
        await query.edit_message_text("❌ Ошибка создания счёта. Попробуйте позже.")
        return
    invoice_id = str(invoice["invoice_id"])
    pending_payments[invoice_id] = {"user_id": user_id, "tariff": tariff}
    keyboard = [
        [InlineKeyboardButton("💳 Оплатить", url=invoice["pay_url"])],
        [InlineKeyboardButton("✅ Проверить оплату", callback_data=f"check_payment_{invoice_id}")],
        [InlineKeyboardButton("❌ Отмена", callback_data="show_tariffs")]
    ]
    text = (
        f"💸 *Оплата тарифа {tariff.upper()}*\n"
        f"Стоимость: {price_rub} руб. (≈{price_usd} USD)\n"
        f"Тариф будет активирован на 30 дней.\n\n"
        f"После оплаты нажмите «Проверить оплату»."
    )
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def check_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, invoice_id: str):
    query = update.callback_query
    await query.answer()
    if invoice_id not in pending_payments:
        await query.edit_message_text("❌ Запрос на оплату не найден или устарел.")
        return
    info = pending_payments[invoice_id]
    status = check_invoice_status(invoice_id)
    if status == "paid":
        user_id = info["user_id"]
        tariff = info["tariff"]
        set_user_tariff(user_id, tariff, 30)
        del pending_payments[invoice_id]
        await query.edit_message_text(
            f"✅ *Оплата подтверждена!*\nТариф {tariff.upper()} активирован на 30 дней.\n"
            f"Теперь вы можете использовать расширенные функции в ваших группах.",
            parse_mode="Markdown"
        )
        await menu(update, context)
    else:
        await query.edit_message_text("⏳ Оплата не обнаружена. Убедитесь, что вы завершили платёж, и нажмите снова.")

def create_crypto_invoice(amount_usd: float, description: str) -> Optional[Dict]:
    url = "https://pay.crypt.bot/api/createInvoice"
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN, "Content-Type": "application/json"}
    payload = {
        "asset": "USDT",
        "amount": amount_usd,
        "description": description,
        "paid_btn_name": "callback",
        "paid_btn_url": "https://t.me/YourBotUsername"  # замените на имя бота
    }
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        result = response.json()
        if result.get("ok"):
            return result["result"]
    except Exception as e:
        logging.error(f"CryptoBot error: {e}")
    return None

def check_invoice_status(invoice_id: str) -> Optional[str]:
    url = "https://pay.crypt.bot/api/getInvoices"
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}
    params = {"invoice_ids": invoice_id}
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        result = response.json()
        if result.get("ok") and result["result"]["items"]:
            invoice = result["result"]["items"][0]
            if invoice["status"] == "paid":
                return "paid"
    except Exception as e:
        logging.error(f"Ошибка проверки счёта: {e}")
    return None

# ---------- АДМИН-ПАНЕЛЬ ----------
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != ADMIN_ID:
        await query.edit_message_text("⛔ У вас нет доступа.")
        return
    keyboard = [
        [InlineKeyboardButton("📊 Статистика групп", callback_data="admin_stats")],
        [InlineKeyboardButton("ℹ️ Информация о группе", callback_data="admin_group_info")],
        [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
    ]
    await query.edit_message_text("👑 *Админ-панель*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != ADMIN_ID:
        await query.edit_message_text("⛔ У вас нет доступа.")
        return
    text = "*📊 Список групп:*\n"
    for cid in data["groups"]:
        try:
            chat = await context.bot.get_chat(int(cid))
            name = chat.title or f"Группа {cid}"
        except:
            name = f"Группа {cid}"
        text += f"• {name} (`{cid}`)\n"
    await query.edit_message_text(text, parse_mode="Markdown")

async def admin_group_info_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != ADMIN_ID:
        await query.edit_message_text("⛔ У вас нет доступа.")
        return
    user_states[update.effective_user.id] = "await_group_id"
    await query.message.reply_text(
        "🔍 Введите ID группы (например, -1001234567890):\n"
        "*(можно скопировать из списка групп)*"
    )
    await query.edit_message_text("Админ-панель ожидает ввод ID...")

async def show_group_info(chat_id: int, message, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_obj = await context.bot.get_chat(chat_id)
        admins = await context.bot.get_chat_administrators(chat_id)

        owner = None
        admin_ids = []
        for a in admins:
            if a.status == "creator":
                owner = a.user.id
            else:
                admin_ids.append(a.user.id)

        try:
            member_count = await context.bot.get_chat_member_count(chat_id)
        except:
            member_count = "неизвестно"

        text = (
            f"📊 *Информация о группе*\n"
            f"*Название:* {chat_obj.title}\n"
            f"*ID:* `{chat_id}`\n"
            f"*Тип:* {chat_obj.type}\n"
            f"*Участников:* {member_count}\n\n"
            f"👑 *Владелец:* `{owner}`\n"
            f"🛡 *Администраторы:*\n"
        )
        for aid in admin_ids:
            text += f"   • `{aid}`\n"
        if not admin_ids:
            text += "   (нет)\n"
        await message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        await message.reply_text(f"❌ Не удалось получить информацию о группе:\n{e}")

# ---------- ОБРАБОТЧИК ТЕКСТА (ввод ID, антиспам, приветствие) ----------
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user_id = update.effective_user.id
    chat = update.effective_chat

    # Ожидание ввода ID группы для админа
    if user_id == ADMIN_ID and user_states.get(user_id) == "await_group_id":
        try:
            chat_id = int(message.text.strip())
            await show_group_info(chat_id, message, context)
        except Exception as e:
            await message.reply_text(f"❌ Ошибка: {e}")
        del user_states[user_id]
        return

    # Настройка антиспама (только в личных сообщениях)
    if chat.type == "private" and "flood_chat" in context.user_data:
        chat_id = context.user_data.pop("flood_chat")
        text = message.text.strip()
        if not text:
            await message.reply_text("Настройка отменена.")
            return
        try:
            parts = text.split()
            if len(parts) != 3:
                raise ValueError
            limit, window, mute = map(int, parts)
            if limit <= 0 or window <= 0 or mute <= 0:
                raise ValueError
            update_group_setting(chat_id, "flood_limit", limit)
            update_group_setting(chat_id, "flood_window", window)
            update_group_setting(chat_id, "flood_mute", mute)
            await message.reply_text("✅ Настройки антиспама обновлены.")
        except:
            await message.reply_text("❌ Неверный формат. Используйте: `лимит окно длительность` (числа >0)")
            return
        # Показываем меню группы
        await show_group_settings_from_user(message, chat_id, context)
        return

    # Настройка приветствия
    if chat.type == "private" and "welcome_chat" in context.user_data:
        chat_id = context.user_data.pop("welcome_chat")
        text = message.text.strip()
        if text:
            update_group_setting(chat_id, "custom_welcome", text)
            await message.reply_text("✅ Кастомное приветствие сохранено.")
        else:
            update_group_setting(chat_id, "custom_welcome", None)
            await message.reply_text("✅ Кастомное приветствие отключено.")
        await show_group_settings_from_user(message, chat_id, context)
        return

async def show_group_settings_from_user(message, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Показывает меню настроек группы как обычное сообщение (после ввода)."""
    user_id = message.from_user.id
    user_tariff = get_user_tariff(user_id)
    settings = get_group_settings(chat_id)
    allowed_features = TARIFF_FEATURES[user_tariff]

    text = (
        f"*Группа:* `{chat_id}`\n"
        f"*Ваш тариф:* {user_tariff.upper()}\n"
        f"*Антиспам:* {settings['flood_limit']} сообщ. за {settings['flood_window']} сек\n"
        f"*Блокировка ссылок:* {'✅' if settings['block_links'] else '❌'}\n"
        f"*Блокировка инвайт-ссылок:* {'✅' if settings['invite_links_block'] else '❌'}\n"
        f"*Фильтр CAPS:* {'✅' if settings['caps_filter'] else '❌'}\n"
        f"*Блокировка медиа:* {'✅' if settings['block_media'] else '❌'}\n"
        f"*Кастомное приветствие:* {'✅' if settings['custom_welcome'] else '❌'}\n"
        f"*Проверка файлов:* {'✅' if settings['check_files'] else '❌'}\n"
        f"*Статистика:* сообщений {settings['stats']['messages']}, нарушений {settings['stats']['violations']}"
    )
    keyboard = []
    keyboard.append([InlineKeyboardButton("⚙️ Настроить антиспам", callback_data=f"configure_flood_{chat_id}")])

    if allowed_features["block_links"]:
        keyboard.append([InlineKeyboardButton("🔗 Ссылки: Вкл/Выкл", callback_data=f"toggle_links_{chat_id}")])
    if allowed_features["invite_links_block"]:
        keyboard.append([InlineKeyboardButton("🚫 Инвайт-ссылки: Вкл/Выкл", callback_data=f"toggle_invite_{chat_id}")])
    if allowed_features["caps_filter"]:
        keyboard.append([InlineKeyboardButton("🔠 CAPS фильтр: Вкл/Выкл", callback_data=f"toggle_caps_{chat_id}")])
    if allowed_features["block_media"]:
        keyboard.append([InlineKeyboardButton("📷 Медиа: Вкл/Выкл", callback_data=f"toggle_media_{chat_id}")])
    if allowed_features["custom_welcome"]:
        keyboard.append([InlineKeyboardButton("✏️ Кастомное приветствие", callback_data=f"set_welcome_{chat_id}")])
    if allowed_features["check_files"]:
        keyboard.append([InlineKeyboardButton("📁 Проверка файлов: Вкл/Выкл", callback_data=f"toggle_files_{chat_id}")])

    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="groups")])

    await message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

# ---------- ОСНОВНОЙ CALLBACK ОБРАБОТЧИК ----------
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data_cb = query.data

    if data_cb == "main_menu":
        await menu(update, context)
        return
    if data_cb == "groups":
        await show_groups(update, context)
        return
    if data_cb == "show_tariffs":
        await show_tariffs(update, context)
        return
    if data_cb == "buy_standard":
        await buy_tariff(update, context, "standard")
        return
    if data_cb == "buy_pro":
        await buy_tariff(update, context, "pro")
        return
    if data_cb == "admin_panel":
        await admin_panel(update, context)
        return
    if data_cb == "admin_stats":
        await admin_stats(update, context)
        return
    if data_cb == "admin_group_info":
        await admin_group_info_request(update, context)
        return

    if data_cb.startswith("group_"):
        chat_id = int(data_cb.split("_")[1])
        await show_group_settings(query, chat_id)
        return

    if data_cb.startswith("toggle_links_"):
        chat_id = int(data_cb.split("_")[2])
        await toggle_setting(update, context, "block_links", chat_id)
        return
    if data_cb.startswith("toggle_invite_"):
        chat_id = int(data_cb.split("_")[2])
        await toggle_setting(update, context, "invite_links_block", chat_id)
        return
    if data_cb.startswith("toggle_caps_"):
        chat_id = int(data_cb.split("_")[2])
        await toggle_setting(update, context, "caps_filter", chat_id)
        return
    if data_cb.startswith("toggle_media_"):
        chat_id = int(data_cb.split("_")[2])
        await toggle_setting(update, context, "block_media", chat_id)
        return
    if data_cb.startswith("toggle_files_"):
        chat_id = int(data_cb.split("_")[2])
        await toggle_setting(update, context, "check_files", chat_id)
        return

    if data_cb.startswith("configure_flood_"):
        chat_id = int(data_cb.split("_")[2])
        await configure_flood(update, context, chat_id)
        return
    if data_cb.startswith("set_welcome_"):
        chat_id = int(data_cb.split("_")[2])
        await set_welcome(update, context, chat_id)
        return

    if data_cb.startswith("check_payment_"):
        invoice_id = data_cb.split("_")[2]
        await check_payment(update, context, invoice_id)
        return

# ---------- ЗАПУСК ----------
def main():
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO
    )
    load_data()

    application = Application.builder().token(TOKEN).build()

    # Защита
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, handle_message))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_chat_members))

    # Команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", menu))

    # Обработчики кнопок и текста
    application.add_handler(CallbackQueryHandler(button_callback, pattern="^(groups|show_tariffs|buy_standard|buy_pro|admin_panel|admin_stats|admin_group_info|group_|toggle_links_|toggle_invite_|toggle_caps_|toggle_media_|toggle_files_|configure_flood_|set_welcome_|check_payment_|main_menu)"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logging.info("✅ Бот запущен")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
