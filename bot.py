import asyncio
import json
import logging
import os
import re
import requests
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from telegram import Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ---------- КОНФИГУРАЦИЯ ----------
TOKEN = "8768850938:AAGXlxCENVXIqUXAJMBG2bl2xgUwNAJOc4Q"
CRYPTOBOT_TOKEN = "555209:AAvWWWiQt0ERfGAjTGozQDu1HEAZICFi4ZW"
DATA_FILE = "bot_data.json"
ADMIN_ID = 2032012311

PRICES_RUB = {"standard": 99, "pro": 199}
PRICES_USD = {"standard": 0.99, "pro": 1.99}

DEFAULT_SETTINGS = {
    "tariff": "free",
    "flood_limit": 5,
    "flood_window": 10,
    "flood_mute": 60,
    "block_links": True,
    "block_media": True,
    "block_bad_words": False,
    "custom_welcome": None,
    "check_links": True,
    "check_files": False,
    "check_content": False,
    "invite_links_block": True,
    "caps_filter": True,
    "silent_mode": False,
    "stats": {"messages": 0, "violations": 0}
}

TARIFF_FEATURES = {
    "free": {
        "block_links": True,
        "block_media": False,
        "block_bad_words": False,
        "custom_welcome": False,
        "check_links": True,
        "check_files": False,
        "check_content": False,
        "invite_links_block": True,
        "caps_filter": True
    },
    "standard": {
        "block_links": True,
        "block_media": True,
        "block_bad_words": False,
        "custom_welcome": True,
        "check_links": True,
        "check_files": True,
        "check_content": False,
        "invite_links_block": True,
        "caps_filter": True
    },
    "pro": {
        "block_links": True,
        "block_media": True,
        "block_bad_words": True,
        "custom_welcome": True,
        "check_links": True,
        "check_files": True,
        "check_content": True,
        "invite_links_block": True,
        "caps_filter": True
    }
}

# ---------- ГЛОБАЛЬНЫЕ ДАННЫЕ ----------
data: Dict = {"groups": {}, "admins": [ADMIN_ID]}
user_messages: Dict[int, List[datetime]] = defaultdict(list)
pending_payments: Dict[str, dict] = {}

# ---------- ЗАГРУЗКА / СОХРАНЕНИЕ ----------
def load_data():
    global data
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if ADMIN_ID not in data.get("admins", []):
                data.setdefault("admins", []).append(ADMIN_ID)
        except Exception as e:
            logging.error(f"Ошибка загрузки данных: {e}")
            data = {"groups": {}, "admins": [ADMIN_ID]}
    else:
        data = {"groups": {}, "admins": [ADMIN_ID]}

def save_data():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Ошибка сохранения данных: {e}")

def get_group_settings(chat_id: int) -> Dict:
    chat_id_str = str(chat_id)
    if chat_id_str not in data["groups"]:
        settings = DEFAULT_SETTINGS.copy()
        data["groups"][chat_id_str] = settings
        save_data()
        logging.info(f"Создана новая запись для группы {chat_id}")
    return data["groups"][chat_id_str]

def set_group_settings(chat_id: int, settings: Dict):
    data["groups"][str(chat_id)] = settings
    save_data()

def update_group_setting(chat_id: int, key: str, value):
    settings = get_group_settings(chat_id)
    settings[key] = value
    set_group_settings(chat_id, settings)

def delete_group(chat_id: int):
    chat_id_str = str(chat_id)
    if chat_id_str in data["groups"]:
        del data["groups"][chat_id_str]
        save_data()
        return True
    return False

def add_group_by_id(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Проверяет, что бот есть в группе, и добавляет её в базу."""
    try:
        chat = context.bot.get_chat(chat_id)
        if chat.type not in ("group", "supergroup"):
            return False
        bot_member = context.bot.get_chat_member(chat_id, context.bot.id)
        if bot_member.status not in ("administrator", "member"):
            return False
        get_group_settings(chat_id)
        return True
    except Exception as e:
        logging.error(f"Ошибка при добавлении группы {chat_id}: {e}")
        return False

def clean_invalid_groups(context: ContextTypes.DEFAULT_TYPE):
    """Удаляет группы, в которых бот больше не состоит."""
    to_delete = []
    for chat_id_str in list(data["groups"].keys()):
        try:
            chat_id = int(chat_id_str)
            bot_member = context.bot.get_chat_member(chat_id, context.bot.id)
            if bot_member.status not in ("administrator", "member"):
                to_delete.append(chat_id_str)
        except Exception as e:
            logging.warning(f"Ошибка при проверке группы {chat_id_str}: {e}")
            to_delete.append(chat_id_str)
    for chat_id_str in to_delete:
        del data["groups"][chat_id_str]
        logging.info(f"Удалена неактивная группа {chat_id_str}")
    if to_delete:
        save_data()

# ---------- ПРОВЕРКИ ----------
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

async def is_admin(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
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
        if not settings.get("silent_mode", False):
            await context.bot.send_message(
                chat_id,
                f"🚫 Пользователь {user_id} получил ограничение на {duration} сек.\nПричина: {reason}"
            )
        settings["stats"]["violations"] += 1
        set_group_settings(chat_id, settings)
    except Exception as e:
        logging.error(f"Не удалось ограничить пользователя {user_id} в чате {chat_id}: {e}")

# ---------- ЗАЩИТА СООБЩЕНИЙ ----------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not update.effective_user:
        return
    chat = update.effective_chat
    user = update.effective_user
    message = update.effective_message

    # Игнорируем сообщения от ботов
    if user.is_bot:
        return

    # Если это группа или супергруппа и она ещё не зарегистрирована, проверяем, что бот участник, и регистрируем
    if chat.type in ("group", "supergroup") and str(chat.id) not in data["groups"]:
        try:
            bot_member = await chat.get_member(context.bot.id)
            if bot_member.status in ("administrator", "member"):
                get_group_settings(chat.id)
                logging.info(f"Автоматическая регистрация группы {chat.id} при получении сообщения")
        except Exception as e:
            logging.warning(f"Не удалось проверить права бота в группе {chat.id}: {e}")

    # Если группа не зарегистрирована после проверки, игнорируем сообщение
    if str(chat.id) not in data["groups"]:
        return

    settings = get_group_settings(chat.id)
    settings["stats"]["messages"] += 1
    set_group_settings(chat.id, settings)

    # Если пользователь администратор – пропускаем все проверки
    if await is_admin(chat.id, user.id, context):
        return

    # Если бот не имеет прав администратора – не можем наказывать
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

    # Инвайт-ссылки
    if settings.get("invite_links_block", True) and message.text and contains_invite_link(message.text):
        await restrict_user(chat.id, user.id, 60, "Запрещённые инвайт-ссылки", context)
        try:
            await message.delete()
        except:
            pass
        return

    # CAPS
    if settings.get("caps_filter", True) and message.text and is_caps_abuse(message.text):
        await restrict_user(chat.id, user.id, 60, "Злоупотребление заглавными буквами", context)
        try:
            await message.delete()
        except:
            pass
        return

    # Медиа
    if settings.get("block_media", True) and any((message.photo, message.video, message.document,
                                                  message.voice, message.audio, message.animation, message.sticker)):
        await restrict_user(chat.id, user.id, 60, "Медиафайлы запрещены", context)
        try:
            await message.delete()
        except:
            pass
        return

    # Запрещённые слова (заглушка)
    if settings.get("block_bad_words", False) and message.text:
        # TODO: проверка по списку слов
        pass

    # Проверка ссылок/файлов (заглушки)
    if settings.get("check_links", False) and message.text and contains_link(message.text):
        pass
    if settings.get("check_files", False) and message.document:
        pass
    if settings.get("check_content", False):
        pass

# ---------- НОВЫЕ УЧАСТНИКИ ----------
async def handle_new_chat_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    for member in update.message.new_chat_members:
        if member.id == context.bot.id:
            # Бот добавлен – регистрируем группу
            get_group_settings(chat.id)
            await update.message.reply_text(
                "🤖 *Бот-защитник активирован!*\n\n"
                "Для настройки напишите мне в личные сообщения /menu и выберите эту группу.\n"
                "По умолчанию активен бесплатный тариф.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

    # Обычное приветствие нового участника (если настроено)
    settings = get_group_settings(chat.id)
    if settings.get("custom_welcome"):
        for member in update.message.new_chat_members:
            welcome = settings["custom_welcome"] or f"Добро пожаловать, {member.full_name}!"
            await update.message.reply_text(welcome)

# ---------- КОМАНДЫ ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот-защитник чатов.\n\n"
        "Добавьте меня в группу и назначьте администратором с правом «Блокировка пользователей».\n"
        "После этого отправьте любое сообщение в группе (или просто напишите что-нибудь), и бот автоматически её зарегистрирует.\n\n"
        "Затем используйте /menu для управления моими группами и настройками."
    )

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    groups = data["groups"]
    clean_invalid_groups(context)

    if not groups:
        await update.message.reply_text(
            "У меня нет добавленных групп.\n\n"
            "Чтобы добавить группу:\n"
            "1. Добавьте бота в группу и назначьте администратором.\n"
            "2. Напишите любое сообщение в этой группе.\n\n"
            "После этого бот автоматически зарегистрирует группу, и она появится здесь для настройки."
        )
        return

    keyboard = []
    for chat_id_str, settings in groups.items():
        try:
            chat = await context.bot.get_chat(int(chat_id_str))
            if chat.type not in ("group", "supergroup"):
                continue
            title = chat.title or f"Группа {chat_id_str}"
        except:
            title = f"Группа {chat_id_str}"
        keyboard.append([InlineKeyboardButton(title, callback_data=f"group_{chat_id_str}")])

    if not keyboard:
        await update.message.reply_text("Нет активных групп. Возможно, бот вышел из всех групп.")
        return

    if user_id in data.get("admins", []):
        keyboard.append([InlineKeyboardButton("🔧 Админ панель", callback_data="admin_panel")])
    keyboard.append([InlineKeyboardButton("❌ Закрыть", callback_data="close")])
    await update.message.reply_text(
        "Выберите группу для настройки:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_group_menu(query, chat_id_int: int):
    settings = get_group_settings(chat_id_int)
    tariff = settings["tariff"]
    text = (
        f"*Группа:* {chat_id_int}\n"
        f"*Тариф:* {tariff.upper()}\n"
        f"*Антиспам:* {settings['flood_limit']} сообщ. за {settings['flood_window']} сек → мут {settings['flood_mute']} сек\n"
        f"*Блокировка ссылок:* {'✅' if settings['block_links'] else '❌'}\n"
        f"*Блокировка инвайт-ссылок:* {'✅' if settings['invite_links_block'] else '❌'}\n"
        f"*Фильтр CAPS:* {'✅' if settings['caps_filter'] else '❌'}\n"
        f"*Блокировка медиа:* {'✅' if settings['block_media'] else '❌'}\n"
        f"*Кастомное приветствие:* {'✅' if settings['custom_welcome'] else '❌'}\n"
        f"*Проверка ссылок:* {'✅' if settings['check_links'] else '❌'}\n"
        f"*Проверка файлов:* {'✅' if settings['check_files'] else '❌'}\n"
        f"*Проверка контента:* {'✅' if settings['check_content'] else '❌'}\n"
        f"*Статистика:* сообщений {settings['stats']['messages']}, нарушений {settings['stats']['violations']}"
    )
    keyboard = [
        [InlineKeyboardButton("Сменить тариф", callback_data=f"choose_tariff_{chat_id_int}")],
        [InlineKeyboardButton("Настроить антиспам", callback_data=f"configure_flood_{chat_id_int}")],
        [InlineKeyboardButton("Вкл/Выкл ссылки", callback_data=f"toggle_links_{chat_id_int}")],
        [InlineKeyboardButton("Вкл/Выкл инвайт-ссылки", callback_data=f"toggle_invite_{chat_id_int}")],
        [InlineKeyboardButton("Вкл/Выкл CAPS фильтр", callback_data=f"toggle_caps_{chat_id_int}")],
        [InlineKeyboardButton("Вкл/Выкл медиа", callback_data=f"toggle_media_{chat_id_int}")],
        [InlineKeyboardButton("Кастомное приветствие", callback_data=f"set_welcome_{chat_id_int}")],
        [InlineKeyboardButton("Назад", callback_data="back_to_groups")],
    ]
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))

async def show_tariff_choice(query, chat_id: int):
    buttons = []
    for tariff in ["free", "standard", "pro"]:
        price = PRICES_RUB.get(tariff, 0)
        label = f"Бесплатный" if tariff == "free" else f"{tariff.upper()} – {price} руб."
        buttons.append([InlineKeyboardButton(label, callback_data=f"select_tariff_{tariff}_{chat_id}")])
    buttons.append([InlineKeyboardButton("Назад", callback_data=f"group_{chat_id}")])
    await query.edit_message_text("Выберите тариф:", reply_markup=InlineKeyboardMarkup(buttons))

# ---------- ОПЛАТА ----------
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

async def start_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, tariff: str):
    query = update.callback_query
    user_id = update.effective_user.id
    price_usd = PRICES_USD[tariff]
    description = f"Активация тарифа {tariff.upper()} для группы {chat_id}"
    invoice = create_crypto_invoice(price_usd, description)
    if not invoice:
        await query.edit_message_text("❌ Ошибка создания счёта. Попробуйте позже.")
        return
    invoice_id = str(invoice["invoice_id"])
    pending_payments[invoice_id] = {"user_id": user_id, "chat_id": chat_id, "tariff": tariff}
    keyboard = [
        [InlineKeyboardButton("💳 Оплатить", url=invoice["pay_url"])],
        [InlineKeyboardButton("✅ Проверить оплату", callback_data=f"check_payment_{invoice_id}")],
        [InlineKeyboardButton("❌ Отмена", callback_data=f"cancel_payment_{chat_id}")]
    ]
    text = (
        f"💸 *Оплата тарифа {tariff.upper()}*\n"
        f"Стоимость: {PRICES_RUB[tariff]} руб. (≈{price_usd} USD)\n"
        f"Группа: {chat_id}\n\n"
        f"После оплаты нажмите «Проверить оплату»."
    )
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))

async def select_tariff(update: Update, context: ContextTypes.DEFAULT_TYPE, tariff: str, chat_id: int):
    query = update.callback_query
    await query.answer()
    if tariff == "free":
        settings = get_group_settings(chat_id)
        settings["tariff"] = "free"
        for key, value in TARIFF_FEATURES["free"].items():
            settings[key] = value
        set_group_settings(chat_id, settings)
        await query.edit_message_text("✅ Бесплатный тариф активирован.")
        await asyncio.sleep(1)
        await show_group_menu(query, chat_id)
    else:
        await start_payment(update, context, chat_id, tariff)

async def check_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, invoice_id: str):
    query = update.callback_query
    await query.answer()
    if invoice_id not in pending_payments:
        await query.edit_message_text("❌ Запрос на оплату не найден или устарел.")
        return
    info = pending_payments[invoice_id]
    status = check_invoice_status(invoice_id)
    if status == "paid":
        chat_id = info["chat_id"]
        tariff = info["tariff"]
        settings = get_group_settings(chat_id)
        settings["tariff"] = tariff
        for key, value in TARIFF_FEATURES[tariff].items():
            settings[key] = value
        set_group_settings(chat_id, settings)
        del pending_payments[invoice_id]
        await query.edit_message_text(f"✅ *Оплата подтверждена!*\nТариф {tariff.upper()} активирован для группы {chat_id}.", parse_mode=ParseMode.MARKDOWN)
        await show_group_menu(query, chat_id)
    else:
        await query.edit_message_text("⏳ Оплата не обнаружена. Убедитесь, что вы завершили платёж, и нажмите снова.")

async def cancel_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    query = update.callback_query
    await query.answer()
    to_remove = [inv for inv, info in pending_payments.items() if info["chat_id"] == chat_id]
    for inv in to_remove:
        del pending_payments[inv]
    await show_tariff_choice(query, chat_id)

# ---------- АДМИН-ПАНЕЛЬ ----------
async def admin_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    if user_id not in data.get("admins", []):
        await query.edit_message_text("⛔ У вас нет доступа к админ-панели.")
        return
    text = "🔧 *Админ-панель*\n\n"
    text += f"Всего групп: {len(data['groups'])}\n"
    text += f"Администраторов бота: {len(data['admins'])}"
    keyboard = [
        [InlineKeyboardButton("Список групп", callback_data="admin_groups")],
        [InlineKeyboardButton("Управление админами", callback_data="admin_admins")],
        [InlineKeyboardButton("Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton("➕ Добавить группу по ID", callback_data="add_group_by_id")],
        [InlineKeyboardButton("🗑 Очистить неактивные группы", callback_data="clean_groups")],
        [InlineKeyboardButton("❌ Закрыть", callback_data="close_admin")],
    ]
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_groups_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    groups = data["groups"]
    if not groups:
        await query.edit_message_text("Нет групп.")
        return

    keyboard = []
    for chat_id_str, settings in groups.items():
        try:
            chat = await context.bot.get_chat(int(chat_id_str))
            title = chat.title or f"Группа {chat_id_str}"
        except:
            title = f"Группа {chat_id_str}"
        keyboard.append([
            InlineKeyboardButton(f"{title}", callback_data=f"group_{chat_id_str}"),
            InlineKeyboardButton("❌ Удалить", callback_data=f"delete_group_{chat_id_str}")
        ])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")])
    await query.edit_message_text(
        "Выберите группу для настройки или удаления:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def add_group_by_id_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Введите ID группы (число):")
    context.user_data["waiting_for_group_id"] = True

async def handle_group_id_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ввода ID группы для добавления."""
    message = update.message
    user_id = update.effective_user.id
    if user_id not in data.get("admins", []):
        await message.reply_text("⛔ У вас нет доступа.")
        return
    if not context.user_data.get("waiting_for_group_id"):
        return
    context.user_data.pop("waiting_for_group_id")
    try:
        chat_id = int(message.text.strip())
    except:
        await message.reply_text("❌ Неверный формат. Введите числовой ID.")
        return

    # Проверяем, что бот есть в группе и может её добавить
    try:
        chat = await context.bot.get_chat(chat_id)
        if chat.type not in ("group", "supergroup"):
            await message.reply_text("❌ Указанный ID не является группой или супергруппой.")
            return
        bot_member = await context.bot.get_chat_member(chat_id, context.bot.id)
        if bot_member.status not in ("administrator", "member"):
            await message.reply_text("❌ Бот не является участником этой группы. Добавьте его сначала.")
            return
        if not bot_member.can_restrict_members:
            await message.reply_text("⚠️ Бот добавлен, но не имеет прав на ограничение пользователей. Назначьте его администратором в группе.")
    except Exception as e:
        await message.reply_text(f"❌ Ошибка при проверке группы: {e}")
        return

    # Добавляем группу
    get_group_settings(chat_id)
    await message.reply_text(f"✅ Группа {chat.title or chat_id} добавлена в базу бота.")
    # Показываем меню с группами
    await menu(update, context)

async def clean_groups_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    clean_invalid_groups(context)
    await query.edit_message_text("✅ Неактивные группы удалены.")
    await asyncio.sleep(1)
    await admin_panel_callback(update, context)

async def delete_group_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id_str: str):
    query = update.callback_query
    keyboard = [
        [
            InlineKeyboardButton("✅ Да, удалить", callback_data=f"confirm_delete_{chat_id_str}"),
            InlineKeyboardButton("❌ Отмена", callback_data="admin_groups")
        ]
    ]
    await query.edit_message_text(
        f"⚠️ Вы уверены, что хотите удалить группу {chat_id_str} из базы бота?\n"
        "Это действие не удаляет группу в Telegram, только настройки бота.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def confirm_delete_group(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id_str: str):
    query = update.callback_query
    chat_id = int(chat_id_str)
    if delete_group(chat_id):
        await query.edit_message_text(f"✅ Группа {chat_id} удалена из настроек бота.")
    else:
        await query.edit_message_text("❌ Группа не найдена.")
    await asyncio.sleep(1)
    await admin_groups_list(update, context)

async def admin_admins_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    admins = data["admins"]
    text = "*Администраторы бота:*\n"
    for aid in admins:
        text += f"- {aid}\n"
    text += "\nДля добавления введите /addadmin <id>\nДля удаления /deladmin <id>"
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    total_messages = sum(s["stats"]["messages"] for s in data["groups"].values())
    total_violations = sum(s["stats"]["violations"] for s in data["groups"].values())
    text = f"*Общая статистика*\nСообщений: {total_messages}\nНарушений: {total_violations}"
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)

# ---------- КОМАНДЫ АДМИНОВ ----------
async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Только главный админ может добавлять администраторов.")
        return
    if not context.args:
        await update.message.reply_text("Использование: /addadmin <user_id>")
        return
    try:
        new_admin = int(context.args[0])
        if new_admin not in data["admins"]:
            data["admins"].append(new_admin)
            save_data()
            await update.message.reply_text(f"✅ Пользователь {new_admin} добавлен как администратор.")
        else:
            await update.message.reply_text("Уже администратор.")
    except:
        await update.message.reply_text("Ошибка: ID должен быть числом.")

async def del_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Только главный админ может удалять администраторов.")
        return
    if not context.args:
        await update.message.reply_text("Использование: /deladmin <user_id>")
        return
    try:
        admin_to_del = int(context.args[0])
        if admin_to_del in data["admins"] and admin_to_del != ADMIN_ID:
            data["admins"].remove(admin_to_del)
            save_data()
            await update.message.reply_text(f"✅ Пользователь {admin_to_del} удалён из администраторов.")
        else:
            await update.message.reply_text("Нельзя удалить главного админа или пользователь не администратор.")
    except:
        await update.message.reply_text("Ошибка.")

# ---------- ОБРАБОТЧИКИ КНОПОК ----------
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data_cb = query.data

    # Общие
    if data_cb == "close":
        await query.edit_message_text("Меню закрыто.")
        return
    if data_cb == "back_to_groups":
        await menu(update, context)
        return
    if data_cb == "admin_panel":
        await admin_panel_callback(update, context)
        return
    if data_cb == "admin_groups":
        await admin_groups_list(update, context)
        return
    if data_cb == "admin_admins":
        await admin_admins_menu(update, context)
        return
    if data_cb == "admin_stats":
        await admin_stats(update, context)
        return
    if data_cb == "close_admin":
        await query.edit_message_text("Админ-панель закрыта.")
        return
    if data_cb == "add_group_by_id":
        await add_group_by_id_callback(update, context)
        return
    if data_cb == "clean_groups":
        await clean_groups_action(update, context)
        return

    # Удаление группы
    if data_cb.startswith("delete_group_"):
        chat_id_str = data_cb.split("_")[2]
        await delete_group_confirm(update, context, chat_id_str)
        return
    if data_cb.startswith("confirm_delete_"):
        chat_id_str = data_cb.split("_")[2]
        await confirm_delete_group(update, context, chat_id_str)
        return

    # Группа из списка
    if data_cb.startswith("group_"):
        chat_id = int(data_cb.split("_")[1])
        await show_group_menu(query, chat_id)
        return

    # Выбор тарифа
    if data_cb.startswith("choose_tariff_"):
        chat_id = int(data_cb.split("_")[2])
        await show_tariff_choice(query, chat_id)
        return
    if data_cb.startswith("select_tariff_"):
        parts = data_cb.split("_")
        tariff = parts[2]
        chat_id = int(parts[3])
        await select_tariff(update, context, tariff, chat_id)
        return

    # Проверка/отмена оплаты
    if data_cb.startswith("check_payment_"):
        invoice_id = data_cb.split("_")[2]
        await check_payment(update, context, invoice_id)
        return
    if data_cb.startswith("cancel_payment_"):
        chat_id = int(data_cb.split("_")[2])
        await cancel_payment(update, context, chat_id)
        return

    # Переключения
    if data_cb.startswith("toggle_links_"):
        chat_id = int(data_cb.split("_")[2])
        settings = get_group_settings(chat_id)
        update_group_setting(chat_id, "block_links", not settings["block_links"])
        await show_group_menu(query, chat_id)
        return
    if data_cb.startswith("toggle_invite_"):
        chat_id = int(data_cb.split("_")[2])
        settings = get_group_settings(chat_id)
        update_group_setting(chat_id, "invite_links_block", not settings["invite_links_block"])
        await show_group_menu(query, chat_id)
        return
    if data_cb.startswith("toggle_caps_"):
        chat_id = int(data_cb.split("_")[2])
        settings = get_group_settings(chat_id)
        update_group_setting(chat_id, "caps_filter", not settings["caps_filter"])
        await show_group_menu(query, chat_id)
        return
    if data_cb.startswith("toggle_media_"):
        chat_id = int(data_cb.split("_")[2])
        settings = get_group_settings(chat_id)
        update_group_setting(chat_id, "block_media", not settings["block_media"])
        await show_group_menu(query, chat_id)
        return

    # Настройка антиспама / приветствия
    if data_cb.startswith("set_welcome_"):
        chat_id = int(data_cb.split("_")[2])
        context.user_data["welcome_chat"] = chat_id
        await query.edit_message_text("Введите текст приветствия (или отправьте пустое сообщение для отключения):")
        return
    if data_cb.startswith("configure_flood_"):
        chat_id = int(data_cb.split("_")[2])
        context.user_data["flood_chat"] = chat_id
        await query.edit_message_text(
            "Введите параметры антиспама в формате:\n"
            "`лимит сообщений окно_секунд длительность_мута_секунд`\n"
            "Пример: `5 10 60`\n\n"
            "Или отправьте пустое сообщение для отмены.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ввода приветствия, антиспама и ID группы."""
    message = update.message
    user_id = update.effective_user.id

    # Если ожидаем ID группы для добавления
    if context.user_data.get("waiting_for_group_id") and user_id in data.get("admins", []):
        await handle_group_id_input(update, context)
        return

    if "welcome_chat" in context.user_data:
        chat_id = context.user_data.pop("welcome_chat")
        text = message.text.strip()
        if text:
            update_group_setting(chat_id, "custom_welcome", text)
            await message.reply_text("✅ Кастомное приветствие сохранено.")
        else:
            update_group_setting(chat_id, "custom_welcome", None)
            await message.reply_text("✅ Кастомное приветствие отключено.")
        await show_group_menu_from_user(update, chat_id, context)
        return

    if "flood_chat" in context.user_data:
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
        await show_group_menu_from_user(update, chat_id, context)
        return

async def show_group_menu_from_user(update: Update, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Показывает меню группы как обычное сообщение."""
    settings = get_group_settings(chat_id)
    tariff = settings["tariff"]
    text = (
        f"*Группа:* {chat_id}\n"
        f"*Тариф:* {tariff.upper()}\n"
        f"*Антиспам:* {settings['flood_limit']} сообщ. за {settings['flood_window']} сек → мут {settings['flood_mute']} сек\n"
        f"*Блокировка ссылок:* {'✅' if settings['block_links'] else '❌'}\n"
        f"*Блокировка инвайт-ссылок:* {'✅' if settings['invite_links_block'] else '❌'}\n"
        f"*Фильтр CAPS:* {'✅' if settings['caps_filter'] else '❌'}\n"
        f"*Блокировка медиа:* {'✅' if settings['block_media'] else '❌'}\n"
        f"*Кастомное приветствие:* {'✅' if settings['custom_welcome'] else '❌'}\n"
        f"*Проверка ссылок:* {'✅' if settings['check_links'] else '❌'}\n"
        f"*Проверка файлов:* {'✅' if settings['check_files'] else '❌'}\n"
        f"*Проверка контента:* {'✅' if settings['check_content'] else '❌'}\n"
        f"*Статистика:* сообщений {settings['stats']['messages']}, нарушений {settings['stats']['violations']}"
    )
    keyboard = [
        [InlineKeyboardButton("Сменить тариф", callback_data=f"choose_tariff_{chat_id}")],
        [InlineKeyboardButton("Настроить антиспам", callback_data=f"configure_flood_{chat_id}")],
        [InlineKeyboardButton("Вкл/Выкл ссылки", callback_data=f"toggle_links_{chat_id}")],
        [InlineKeyboardButton("Вкл/Выкл инвайт-ссылки", callback_data=f"toggle_invite_{chat_id}")],
        [InlineKeyboardButton("Вкл/Выкл CAPS фильтр", callback_data=f"toggle_caps_{chat_id}")],
        [InlineKeyboardButton("Вкл/Выкл медиа", callback_data=f"toggle_media_{chat_id}")],
        [InlineKeyboardButton("Кастомное приветствие", callback_data=f"set_welcome_{chat_id}")],
        [InlineKeyboardButton("Назад", callback_data="back_to_groups")],
    ]
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))

# ---------- ЗАПУСК ----------
def main():
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO
    )
    logging.info("Запуск бота...")
    load_data()
    logging.info(f"Загружено {len(data['groups'])} групп.")

    application = Application.builder().token(TOKEN).build()

    # Защита
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, handle_message))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_chat_members))

    # Команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", menu))
    application.add_handler(CommandHandler("addadmin", add_admin))
    application.add_handler(CommandHandler("deladmin", del_admin))

    # Callback'и
    application.add_handler(CallbackQueryHandler(button_callback, pattern="^(group_|choose_tariff_|select_tariff_|check_payment_|cancel_payment_|toggle_links_|toggle_invite_|toggle_caps_|toggle_media_|set_welcome_|configure_flood_|back_to_groups|close|admin_panel|admin_groups|admin_admins|admin_stats|close_admin|delete_group_|confirm_delete_|add_group_by_id|clean_groups)"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logging.info("Бот запущен и готов к работе!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
