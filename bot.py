=import asyncio
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
        "invite_links_block": True,
        "strict_flood": False
    },
    "standard": {
        "block_links": True,
        "block_media": True,
        "custom_welcome": True,
        "check_files": True,
        "check_content": False,
        "caps_filter": True,
        "invite_links_block": True,
        "strict_flood": True
    },
    "pro": {
        "block_links": True,
        "block_media": True,
        "custom_welcome": True,
        "check_files": True,
        "check_content": True,
        "caps_filter": True,
        "invite_links_block": True,
        "strict_flood": True
    }
}

# --- ОБНОВЛЕННЫЕ ПРОДАЮЩИЕ ОПИСАНИЯ ТАРИФОВ ---
TARIFF_DESCRIPTIONS = {
    "free": (
        "🛡 *Базовый (Free)* — 0 руб.\n\n"
        "Отличный старт для небольших чатов. Включает основные функции защиты:\n"
        "🔹 Базовый антиспам и антифлуд\n"
        "🔹 Удаление рекламных и сторонних ссылок\n"
        "🔹 Блокировка пригласительных ссылок (инвайтов)\n\n"
        "_Защитите свой чат от ботов прямо сейчас!_"
    ),
    "standard": (
        "⭐ *Стандартный (Standard)* — 99 руб./мес\n\n"
        "Продвинутый контроль для активных сообществ:\n"
        "🔸 *Всё из Базового тарифа*\n"
        "🔸 Запрет на отправку медиафайлов (фото, видео, стикеры)\n"
        "🔸 Кастомное приветствие новых участников\n"
        "🔸 Умный фильтр CAPS LOCK (настраиваемый порог)\n"
        "🔸 Отправка подозрительных файлов на проверку\n"
        "🔸 Улучшенный и строгий антифлуд (9 сообщ. за 3 сек)\n\n"
        "_Полный порядок и уют в вашем чате!_"
    ),
    "pro": (
        "💎 *Профессиональный (PRO)* — 199 руб./мес\n\n"
        "Максимальная защита и аналитика для топовых проектов:\n"
        "🚀 *Всё из Стандартного тарифа*\n"
        "🚀 AI-модерация контента (нейросеть анализирует текст)\n"
        "🚀 Расширенная статистика чата и нарушений\n"
        "🚀 Приоритетная поддержка и доступ к новым фичам\n\n"
        "_Автоматизируйте модерацию на 100%!_"
    )
}

DEFAULT_SETTINGS = {
    "flood_limit": 5,
    "flood_window": 10,
    "flood_mute": 60,
    "strict_flood_limit": 9,
    "strict_flood_window": 3,
    "strict_flood_mute": 600,
    "block_links": True,
    "block_media": False,
    "custom_welcome": None,
    "check_files": False,
    "check_content": False,
    "caps_filter": False,
    "caps_threshold": 70,
    "invite_links_block": True,
    "stats": {"messages": 0, "violations": 0, "history": []},
    "warnings": {}
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
                g.setdefault("owner", None)
                g.setdefault("settings", DEFAULT_SETTINGS.copy())
                for key, val in DEFAULT_SETTINGS.items():
                    g["settings"].setdefault(key, val)
                g["settings"].setdefault("stats", {"messages": 0, "violations": 0, "history": []})
                g["settings"].setdefault("warnings", {})
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

def get_group_data(chat_id: int) -> Dict:
    cid = str(chat_id)
    return data["groups"].get(cid)

def create_group(chat_id: int, owner_id: int) -> Dict:
    cid = str(chat_id)
    data["groups"][cid] = {
        "owner": owner_id,
        "settings": DEFAULT_SETTINGS.copy()
    }
    save_data()
    return data["groups"][cid]

def get_group_settings(chat_id: int) -> Dict:
    g = get_group_data(chat_id)
    return g["settings"] if g else None

def update_group_setting(chat_id: int, key: str, value):
    g = get_group_data(chat_id)
    if g:
        g["settings"][key] = value
        save_data()

def register_user(user_id: int) -> Dict:
    uid = str(user_id)
    if uid not in user_data:
        if user_id == ADMIN_ID:
            user_data[uid] = {
                "registered": datetime.now().isoformat(),
                "tariff": "pro",
                "expiry": None
            }
        else:
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

def is_caps_abuse(text: str, threshold: int = 70) -> bool:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    uppercase = sum(1 for c in letters if c.isupper())
    return (uppercase / len(letters)) * 100 > threshold

def is_flooding(user_id: int, chat_id: int, strict: bool = False) -> bool:
    settings = get_group_settings(chat_id)
    if not settings:
        return False
    if strict:
        limit = settings.get("strict_flood_limit", 9)
        window = settings.get("strict_flood_window", 3)
    else:
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
    except Exception as e:
        logging.error(f"Ошибка проверки админа {user_id} в {chat_id}: {e}")
        return False

async def get_group_owner(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
    try:
        admins = await context.bot.get_chat_administrators(chat_id)
        for a in admins:
            if a.status == "creator":
                return a.user.id
        return None
    except Exception as e:
        logging.error(f"Ошибка получения владельца группы {chat_id}: {e}")
        return None

def parse_duration(text: str) -> int:
    """Преобразует строку типа '5m', '2ч', '1д' в секунды."""
    if not text:
        return 60
    text = text.strip().lower()
    match = re.match(r'(\d+)\s*([smhdсмчд])?', text)
    if not match:
        return 60
    num = int(match.group(1))
    unit = match.group(2)
    
    if unit in ('s', 'с'): return num
    elif unit in ('m', 'м'): return num * 60
    elif unit in ('h', 'ч'): return num * 3600
    elif unit in ('d', 'д'): return num * 86400
    else: return num * 60 # По умолчанию минуты

async def restrict_user(chat_id: int, user_id: int, duration: int, reason: str, context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.bot.restrict_chat_member(
            chat_id, user_id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=datetime.now() + timedelta(seconds=duration)
        )
        settings = get_group_settings(chat_id)
        if settings:
            settings["stats"]["violations"] += 1
            settings["stats"]["history"].append({
                "user": user_id,
                "time": datetime.now().isoformat(),
                "reason": reason,
                "duration": duration
            })
            if len(settings["stats"]["history"]) > 100:
                settings["stats"]["history"] = settings["stats"]["history"][-100:]
            update_group_setting(chat_id, "stats", settings["stats"])
        await context.bot.send_message(
            chat_id,
            f"🚫 Пользователь {user_id} получил ограничение на {duration} сек.\nПричина: {reason}"
        )
    except Exception as e:
        logging.error(f"Не удалось ограничить пользователя {user_id} в чате {chat_id}: {e}")

async def mute_user(chat_id: int, user_id: int, duration: int, reason: str, context: ContextTypes.DEFAULT_TYPE):
    try:
        until = datetime.now() + timedelta(seconds=duration)
        await context.bot.restrict_chat_member(
            chat_id, user_id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until
        )
        settings = get_group_settings(chat_id)
        if settings:
            settings["stats"]["violations"] += 1
            settings["stats"]["history"].append({
                "user": user_id,
                "time": datetime.now().isoformat(),
                "reason": reason,
                "duration": duration
            })
            if len(settings["stats"]["history"]) > 100:
                settings["stats"]["history"] = settings["stats"]["history"][-100:]
            update_group_setting(chat_id, "stats", settings["stats"])
            
        if duration >= 86400: dur_str = f"{duration//86400} дн."
        elif duration >= 3600: dur_str = f"{duration//3600} ч."
        elif duration >= 60: dur_str = f"{duration//60} мин."
        else: dur_str = f"{duration} сек."
        
        await context.bot.send_message(chat_id, f"🔇 Пользователь {user_id} получил мут на {dur_str}\nПричина: {reason}")
        return True
    except Exception as e:
        logging.error(f"Не удалось замутить {user_id} в {chat_id}: {e}")
        return False

async def unmute_user(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.bot.restrict_chat_member(
            chat_id, user_id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True
            )
        )
        await context.bot.send_message(chat_id, f"🔊 Пользователь {user_id} размучен.")
        return True
    except Exception as e:
        logging.error(f"Не удалось размутить {user_id} в {chat_id}: {e}")
        return False

async def ban_user(chat_id: int, user_id: int, reason: str, context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.bot.ban_chat_member(chat_id, user_id)
        await context.bot.send_message(chat_id, f"⛔ Пользователь {user_id} забанен.\nПричина: {reason}")
        settings = get_group_settings(chat_id)
        if settings:
            settings["stats"]["violations"] += 1
            settings["stats"]["history"].append({
                "user": user_id,
                "time": datetime.now().isoformat(),
                "reason": reason,
                "duration": 0
            })
            if len(settings["stats"]["history"]) > 100:
                settings["stats"]["history"] = settings["stats"]["history"][-100:]
            update_group_setting(chat_id, "stats", settings["stats"])
        return True
    except Exception as e:
        logging.error(f"Не удалось забанить {user_id} в {chat_id}: {e}")
        return False

async def unban_user(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.bot.unban_chat_member(chat_id, user_id)
        await context.bot.send_message(chat_id, f"✅ Пользователь {user_id} разбанен.")
        return True
    except Exception as e:
        logging.error(f"Не удалось разбанить {user_id} в {chat_id}: {e}")
        return False

async def add_warning(chat_id: int, user_id: int, reason: str, context: ContextTypes.DEFAULT_TYPE):
    settings = get_group_settings(chat_id)
    if not settings: return 0
    warnings = settings.get("warnings", {})
    user_warns = warnings.get(str(user_id), [])
    now = datetime.now()
    user_warns = [w for w in user_warns if datetime.fromisoformat(w["time"]) > now - timedelta(days=7)]
    user_warns.append({"time": now.isoformat(), "reason": reason})
    if len(user_warns) > 10: user_warns = user_warns[-10:]
    warnings[str(user_id)] = user_warns
    settings["warnings"] = warnings
    update_group_setting(chat_id, "warnings", warnings)

    await context.bot.send_message(
        chat_id,
        f"⚠️ Пользователь {user_id} получил предупреждение.\nПричина: {reason}\nВсего предупреждений: {len(user_warns)}"
    )

    if len(user_warns) >= 3:
        await mute_user(chat_id, user_id, 3600, "3 предупреждения (автоматический мут)", context)
        warnings[str(user_id)] = []
        settings["warnings"] = warnings
        update_group_setting(chat_id, "warnings", warnings)
        await context.bot.send_message(chat_id, f"⚠️ Пользователь {user_id} получил мут на 1 час из‑за трёх предупреждений.")
    return len(user_warns)

async def get_warnings(chat_id: int, user_id: int) -> int:
    settings = get_group_settings(chat_id)
    if not settings: return 0
    warnings = settings.get("warnings", {})
    user_warns = warnings.get(str(user_id), [])
    now = datetime.now()
    user_warns = [w for w in user_warns if datetime.fromisoformat(w["time"]) > now - timedelta(days=7)]
    return len(user_warns)

# --- НОВАЯ ФУНКЦИЯ ОБРАБОТКИ ТЕКСТОВЫХ КОМАНД АДМИНА ---
async def process_admin_text_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat_id = update.effective_chat.id
    text = message.text.lower()
    target_user = message.reply_to_message.from_user

    if target_user.is_bot:
        return

    parts = text.split()
    cmd = parts[0]

    try:
        if cmd == '*мут':
            if len(parts) > 1 and parts[1][0].isdigit():
                duration = parse_duration(parts[1])
                reason = " ".join(parts[2:]) if len(parts) > 2 else "Нарушение правил"
            else:
                duration = 3600
                reason = " ".join(parts[1:]) if len(parts) > 1 else "Нарушение правил"
            await mute_user(chat_id, target_user.id, duration, reason, context)
            await message.delete()

        elif cmd == '*бан':
            reason = " ".join(parts[1:]) if len(parts) > 1 else "Нарушение правил"
            await ban_user(chat_id, target_user.id, reason, context)
            await message.delete()

        elif cmd == '*разбан':
            await unban_user(chat_id, target_user.id, context)
            await message.delete()

        elif cmd == '*размут':
            await unmute_user(chat_id, target_user.id, context)
            await message.delete()
            
    except Exception as e:
        logging.error(f"Ошибка текстовой команды админа: {e}")

# ---------- ЗАЩИТА СООБЩЕНИЙ ----------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not update.effective_user:
        return
    chat = update.effective_chat
    user = update.effective_user
    message = update.effective_message

    if user.is_bot:
        return

    if chat.type in ("group", "supergroup"):
        settings = get_group_settings(chat.id)
        if not settings:
            return

        settings["stats"]["messages"] += 1
        update_group_setting(chat.id, "stats", settings["stats"])

        # ПРОВЕРКА НА ТЕКСТОВЫЕ КОМАНДЫ АДМИНА (*мут, *бан)
        if message.reply_to_message and message.text:
            text_lower = message.text.lower()
            if text_lower.startswith(('*мут', '*бан', '*размут', '*разбан')):
                if await is_group_admin(chat.id, user.id, context):
                    await process_admin_text_command(update, context)
                    return

        if await is_group_admin(chat.id, user.id, context):
            return

        try:
            bot_member = await chat.get_member(context.bot.id)
            if bot_member.status != "administrator" or not bot_member.can_restrict_members:
                return
        except:
            return

        g = get_group_data(chat.id)
        owner_tariff = get_user_tariff(g["owner"])
        
        if TARIFF_FEATURES[owner_tariff].get("strict_flood", False):
            if is_flooding(user.id, chat.id, strict=True):
                await restrict_user(chat.id, user.id, settings.get("strict_flood_mute", 600), "Строгий флуд (9/3)", context)
                try: await message.delete()
                except: pass
                return

        if is_flooding(user.id, chat.id):
            await restrict_user(chat.id, user.id, settings["flood_mute"], "Флуд", context)
            try: await message.delete()
            except: pass
            return

        if settings.get("block_links", True) and message.text and contains_link(message.text):
            await restrict_user(chat.id, user.id, 60, "Запрещённые ссылки", context)
            try: await message.delete()
            except: pass
            return

        if settings.get("invite_links_block", True) and message.text and contains_invite_link(message.text):
            await restrict_user(chat.id, user.id, 60, "Запрещённые инвайт-ссылки", context)
            try: await message.delete()
            except: pass
            return

        if settings.get("caps_filter", False) and message.text:
            threshold = settings.get("caps_threshold", 70)
            if is_caps_abuse(message.text, threshold):
                await restrict_user(chat.id, user.id, 1800, f"CAPS (> {threshold}%)", context)
                try: await message.delete()
                except: pass
                return

        if settings.get("block_media", False) and any((message.photo, message.video, message.document,
                                                       message.voice, message.audio, message.animation, message.sticker)):
            await restrict_user(chat.id, user.id, 60, "Медиафайлы запрещены", context)
            try: await message.delete()
            except: pass
            return

        if settings.get("check_files", False) and message.document:
            await message.reply_text("📁 Файл отправлен на проверку")
            await message.delete()

# ---------- НОВЫЕ УЧАСТНИКИ ----------
async def handle_new_chat_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    for member in update.message.new_chat_members:
        if member.id == context.bot.id:
            await update.message.reply_text(
                "🤖 *Бот-защитник активирован!*\n\n"
                "Для добавления этой группы в систему:\n"
                "1. Назначьте бота администратором с правом «Блокировка пользователей».\n"
                "2. В этой группе отправьте команду /addgroup (только для администраторов).\n\n"
                "После этого вы сможете настроить защиту через /menu в личных сообщениях или /group_menu в группе.",
                parse_mode="Markdown"
            )
            return

    settings = get_group_settings(chat.id)
    if settings and settings.get("custom_welcome"):
        for member in update.message.new_chat_members:
            welcome = settings["custom_welcome"] or f"Добро пожаловать, {member.full_name}!"
            await update.message.reply_text(welcome)

# ---------- ДОБАВЛЕНИЕ ГРУППЫ ----------
async def addgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user

    if not chat or chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Эта команда работает только в группах.")
        return

    if not await is_group_admin(chat.id, user.id, context):
        await update.message.reply_text("⛔ Только администраторы ��руппы могут добавить бота.")
        return

    try:
        bot_member = await chat.get_member(context.bot.id)
        if bot_member.status != "administrator":
            await update.message.reply_text("❌ Бот не является администратором.")
            return
        if not bot_member.can_restrict_members:
            await update.message.reply_text("⚠️ Бот не имеет права «Блокировка пользователей».")
            return
    except Exception as e:
        await update.message.reply_text(f"❌ Не удалось проверить права бота: {e}")
        return

    if get_group_data(chat.id):
        await update.message.reply_text("✅ Группа уже добавлена.")
        return

    owner_id = await get_group_owner(chat.id, context)
    if not owner_id:
        owner_id = user.id

    create_group(chat.id, owner_id)
    await update.message.reply_text(f"✅ Группа {chat.title or chat.id} добавлена! Владелец: {owner_id}")

# ---------- МЕНЮ В ЛИЧНЫХ СООБЩЕНИЯХ ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    register_user(user_id)
    await show_main_menu(update, context)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, edit_message=False, chat_id=None, message_id=None):
    user_id = update.effective_user.id
    keyboard = [
        [InlineKeyboardButton("👤 Профиль", callback_data="profile")],
        [InlineKeyboardButton("📋 Мои группы", callback_data="groups")],
        [InlineKeyboardButton("💰 Тарифы", callback_data="show_tariffs")],
    ]
    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("👑 Админ панель", callback_data="admin_panel")])

    if edit_message and chat_id and message_id:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text="👋 *Главное меню*\nВыберите действие:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "👋 *Главное меню*\nВыберите действие:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

# ---------- МЕНЮ В ГРУППЕ ----------
async def group_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user

    if not chat or chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Эта команда работает только в группах.")
        return

    if not await is_group_admin(chat.id, user.id, context):
        await update.message.reply_text("⛔ Только администраторы группы могут управлять ботом.")
        return

    g = get_group_data(chat.id)
    if not g:
        await update.message.reply_text("⚠️ Группа не добавлена. Введите /addgroup.")
        return

    settings = g["settings"]
    owner_id = g["owner"]
    owner_tariff = get_user_tariff(owner_id)
    allowed_features = TARIFF_FEATURES[owner_tariff]

    text = (
        f"🛡 *Управление защи��ой группы*\n\n"
        f"*Группа:* {chat.title or chat.id}\n"
        f"*Тариф владельца:* {owner_tariff.upper()}\n"
        f"*Антиспам:* {settings['flood_limit']} сообщ. за {settings['flood_window']} сек → мут {settings['flood_mute']} сек\n"
    )
    if allowed_features["strict_flood"]:
        text += f"*Строгий антиспам:* {settings.get('strict_flood_limit', 9)} сообщ. за {settings.get('strict_flood_window', 3)} сек → мут {settings.get('strict_flood_mute', 600)} сек\n"
    text += (
        f"*CAPS порог:* {settings.get('caps_threshold', 70)}% (мут 30 мин)\n"
        f"*Блокировка ссылок:* {'✅' if settings['block_links'] else '❌'}\n"
        f"*Блокировка инвайт-ссылок:* {'✅' if settings['invite_links_block'] else '❌'}\n"
        f"*Фильтр CAPS:* {'✅' if settings['caps_filter'] else '❌'}\n"
        f"*Блокировка медиа:* {'✅' if settings['block_media'] else '❌'}\n"
        f"*Кастомное приветствие:* {'✅' if settings['custom_welcome'] else '❌'}\n"
        f"*Проверка файлов:* {'✅' if settings['check_files'] else '❌'}\n"
        f"*Статистика:* сообщений {settings['stats']['messages']}, нарушений {settings['stats']['violations']}\n"
    )

    keyboard = []
    keyboard.append([InlineKeyboardButton("⚙️ Антиспам", callback_data=f"group_anti_spam_{chat.id}")])

    if allowed_features["caps_filter"]:
        keyboard.append([InlineKeyboardButton("🔠 CAPS порог", callback_data=f"group_caps_threshold_{chat.id}")])
        keyboard.append([InlineKeyboardButton("🔠 CAPS: Вкл/Выкл", callback_data=f"group_toggle_caps_{chat.id}")])
    else:
        keyboard.append([InlineKeyboardButton("🔒 CAPS (требуется PRO/STD)", callback_data="noop")])

    keyboard.append([InlineKeyboardButton("🔗 Ссылки: Вкл/Выкл", callback_data=f"group_toggle_links_{chat.id}")])
    keyboard.append([InlineKeyboardButton("🚫 Инвайт-ссылки: Вкл/Выкл", callback_data=f"group_toggle_invite_{chat.id}")])

    if allowed_features["block_media"]:
        keyboard.append([InlineKeyboardButton("📷 Медиа: Вкл/Выкл", callback_data=f"group_toggle_media_{chat.id}")])
    else:
        keyboard.append([InlineKeyboardButton("🔒 Медиа (требуется PRO/STD)", callback_data="noop")])

    if allowed_features["custom_welcome"]:
        keyboard.append([InlineKeyboardButton("✏️ Кастомное приветствие", callback_data=f"group_set_welcome_{chat.id}")])
    else:
        keyboard.append([InlineKeyboardButton("🔒 Приветствие (требуется PRO/STD)", callback_data="noop")])

    if allowed_features["check_files"]:
        keyboard.append([InlineKeyboardButton("📁 Проверка файлов: Вкл/Выкл", callback_data=f"group_toggle_files_{chat.id}")])
    else:
        keyboard.append([InlineKeyboardButton("🔒 Проверка файлов (требуется PRO/STD)", callback_data="noop")])

    if owner_tariff == "pro":
        keyboard.append([InlineKeyboardButton("📊 Расширенная статистика", callback_data=f"group_stats_{chat.id}")])

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

# ---------- ОБРАБОТЧИКИ ДЛЯ МЕНЮ В ГРУППЕ ----------
async def group_anti_spam_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    query = update.callback_query
    settings = get_group_settings(chat_id)
    if not settings:
        await query.answer("Группа не найдена", show_alert=True)
        return

    g = get_group_data(chat_id)
    owner_tariff = get_user_tariff(g["owner"])
    is_advanced = TARIFF_FEATURES[owner_tariff].get("strict_flood", False)

    text = (
        f"*Настройка антиспама*\n"
        f"Базовый лимит: {settings['flood_limit']} сообщений за {settings['flood_window']} сек\n"
        f"Длительность мута: {settings['flood_mute']} сек\n"
    )
    if is_advanced:
        text += (
            f"\n*Строгий режим (9/3):*\n"
            f"Лимит: {settings.get('strict_flood_limit', 9)} сообщ. за {settings.get('strict_flood_window', 3)} сек\n"
            f"Мут: {settings.get('strict_flood_mute', 600)} сек\n"
        )
    keyboard = [
        [InlineKeyboardButton("📈 +1 лимит", callback_data=f"group_limit_inc_{chat_id}"),
         InlineKeyboardButton("📉 -1 лимит", callback_data=f"group_limit_dec_{chat_id}")],
        [InlineKeyboardButton("⏱ +5 сек", callback_data=f"group_window_inc_{chat_id}"),
         InlineKeyboardButton("⏱ -5 сек", callback_data=f"group_window_dec_{chat_id}")],
        [InlineKeyboardButton("🔇 +30 сек мута", callback_data=f"group_mute_inc_{chat_id}"),
         InlineKeyboardButton("🔊 -30 сек мута", callback_data=f"group_mute_dec_{chat_id}")],
    ]
    if is_advanced:
        keyboard.append([InlineKeyboardButton("🔧 Настроить строгий антиспам", callback_data=f"group_strict_anti_spam_{chat_id}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=f"group_back_{chat_id}")])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def group_strict_anti_spam_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    query = update.callback_query
    settings = get_group_settings(chat_id)
    if not settings: return
    text = (
        f"*Настройка строгого антиспама*\n"
        f"Лимит: {settings.get('strict_flood_limit', 9)} сообщений\n"
        f"Окно: {settings.get('strict_flood_window', 3)} сек\n"
        f"Длительность мута: {settings.get('strict_flood_mute', 600)} сек\n"
    )
    keyboard = [
        [InlineKeyboardButton("📈 +1", callback_data=f"group_strict_limit_inc_{chat_id}"), InlineKeyboardButton("📉 -1", callback_data=f"group_strict_limit_dec_{chat_id}")],
        [InlineKeyboardButton("⏱ +5 сек", callback_data=f"group_strict_window_inc_{chat_id}"), InlineKeyboardButton("⏱ -5 сек", callback_data=f"group_strict_window_dec_{chat_id}")],
        [InlineKeyboardButton("🔇 +60с мут", callback_data=f"group_strict_mute_inc_{chat_id}"), InlineKeyboardButton("🔊 -60с мут", callback_data=f"group_strict_mute_dec_{chat_id}")],
        [InlineKeyboardButton("🔙 Назад", callback_data=f"group_anti_spam_{chat_id}")]
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def group_caps_threshold_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    query = update.callback_query
    settings = get_group_settings(chat_id)
    if not settings: return
    current = settings.get("caps_threshold", 70)
    text = f"*Настройка порога CAPS*\nТекущий порог: {current}%\nВыберите новый порог:"
    keyboard = [[InlineKeyboardButton(f"{t}%", callback_data=f"group_select_caps_{t}_{chat_id}")] for t in [10, 30, 50, 70, 100]]
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=f"group_back_{chat_id}")])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def group_set_caps_threshold(update: Update, context: ContextTypes.DEFAULT_TYPE, threshold: int, chat_id: int):
    update_group_setting(chat_id, "caps_threshold", threshold)
    await update.callback_query.answer(f"Порог CAPS установлен на {threshold}%")
    await group_menu(update, context)

async def group_toggle_setting(update: Update, context: ContextTypes.DEFAULT_TYPE, setting: str, chat_id: int):
    query = update.callback_query
    if not await is_group_admin(chat_id, query.from_user.id, context): return
    g = get_group_data(chat_id)
    owner_tariff = get_user_tariff(g["owner"])
    if not TARIFF_FEATURES[owner_tariff].get(setting, False):
        await query.answer("❌ Функция недоступна на тарифе владельца.", show_alert=True)
        return
    settings = get_group_settings(chat_id)
    new_val = not settings[setting]
    update_group_setting(chat_id, setting, new_val)
    await query.answer(f"Настройка изменена")
    await group_menu(update, context)

async def group_change_flood_parameter(update: Update, context: ContextTypes.DEFAULT_TYPE, param: str, delta: int, chat_id: int):
    settings = get_group_settings(chat_id)
    if not settings: return
    new_val = max(1, settings[param] + delta)
    update_group_setting(chat_id, param, new_val)
    await update.callback_query.answer(f"Изменено на {new_val}")
    await group_anti_spam_menu(update, context, chat_id)

async def group_change_strict_parameter(update: Update, context: ContextTypes.DEFAULT_TYPE, param: str, delta: int, chat_id: int):
    settings = get_group_settings(chat_id)
    if not settings: return
    new_val = max(1, settings.get(param, 0) + delta)
    update_group_setting(chat_id, param, new_val)
    await update.callback_query.answer(f"Изменено на {new_val}")
    await group_strict_anti_spam_menu(update, context, chat_id)

async def group_set_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    context.user_data["welcome_chat"] = chat_id
    await update.callback_query.edit_message_text("Введите текст приветствия (или отправьте пустое сообщение для отключения):")

async def group_show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    query = update.callback_query
    g = get_group_data(chat_id)
    stats = g["settings"]["stats"]
    text = f"*📊 Расширенная статистика*\nСообщений: {stats['messages']}\nНарушений: {stats['violations']}\n\nПоследние:"
    for entry in stats.get("history", [])[-5:]:
        dt = datetime.fromisoformat(entry["time"]).strftime("%d.%m %H:%M")
        text += f"\n• {dt} – {entry['reason']} ({entry['duration']}с)"
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data=f"group_back_{chat_id}")]]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def group_back(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    await group_menu(update, context)

# ---------- ЗАГЛУШКИ ДЛЯ ЛС И ПРОЧИХ КОМАНД ----------
async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    tariff = get_user_tariff(uid)
    text = f"👤 *Ваш профиль*\n\nВаш ID: `{uid}`\nТекущий тариф: *{tariff.upper()}*"
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]]))

async def show_tariffs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    text = "💰 *Доступные тарифы:*\n\n" + "\n\n".join(TARIFF_DESCRIPTIONS.values())
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]]))

async def show_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.edit_message_text("📋 Список ваших групп.\n(Настройте группы прямо в чате командой /group_menu)", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]]))

# Команды модерации-заглушки (чтобы не было ошибок)
async def cmd_mute(u, c): pass
async def cmd_unmute(u, c): pass
async def cmd_ban(u, c): pass
async def cmd_unban(u, c): pass
async def cmd_warn(u, c): pass
async def cmd_warns(u, c): pass
async def handle_text(u, c): pass
async def show_group_settings(q, chat_id, c): pass
async def show_stats(u, c, chat_id): pass
async def anti_spam_menu(u, c, chat_id): pass
async def strict_anti_spam_menu(u, c, chat_id): pass
async def caps_threshold_menu(u, c, chat_id): pass
async def set_caps_threshold(u, c, t, chat_id): pass
async def toggle_setting(u, c, s, chat_id): pass
async def change_flood_parameter(u, c, p, d, chat_id): pass
async def change_strict_parameter(u, c, p, d, chat_id): pass
async def show_tariff_info(u, c, t): pass
async def buy_tariff(u, c, t): pass
async def check_payment(u, c, i): pass

# ---------- АДМИН-ПАНЕЛЬ (С НОВЫМИ ПЛЮШКАМИ) ----------
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != ADMIN_ID:
        await query.edit_message_text("⛔ У вас нет доступа.")
        return
    keyboard = [
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"), InlineKeyboardButton("ℹ️ Инфо", callback_data="admin_group_info")],
        [InlineKeyboardButton("👥 Пользователи", callback_data="admin_users")],
        [InlineKeyboardButton("📥 Скачать ��экап базы", callback_data="admin_backup")],
        [InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
    ]
    await query.edit_message_text("👑 *Админ-панель*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != ADMIN_ID: return
    text = "*👥 Последние пользователи:*\n"
    for uid, u in list(user_data.items())[:30]:
        text += f"• `{uid}` – {u['tariff'].upper()}\n"
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]]))

async def admin_stats(update, context):
    query = update.callback_query
    await query.edit_message_text(f"📊 Всего групп в базе: {len(data['groups'])}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]]))

async def admin_group_info_request(update, context):
    await update.callback_query.answer("Функция в разработке", show_alert=True)

async def admin_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != ADMIN_ID: return
    try:
        if os.path.exists(DATA_FILE): await context.bot.send_document(chat_id=ADMIN_ID, document=open(DATA_FILE, 'rb'))
        if os.path.exists(USER_DATA_FILE): await context.bot.send_document(chat_id=ADMIN_ID, document=open(USER_DATA_FILE, 'rb'))
        await query.answer("Бэкапы отправлены!")
    except Exception as e:
        await query.answer(f"Ошибка: {e}", show_alert=True)

async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != ADMIN_ID: return
    await query.edit_message_text(
        "📢 *Рассылка*\nДля отправки сообщения всем пользователям бота, напишите команду:\n\n`/broadcast Ваш текст`", 
        parse_mode="Markdown", 
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]])
    )

async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    text = update.message.text.replace('/broadcast', '').strip()
    if not text:
        await update.message.reply_text("Использование: /broadcast Текст")
        return
    count = 0
    for uid in user_data.keys():
        try:
            await context.bot.send_message(chat_id=int(uid), text=text)
            count += 1
            await asyncio.sleep(0.05)
        except: pass
    await update.message.reply_text(f"✅ Рассылка завершена. Доставлено: {count}")

# ---------- ОСНОВНОЙ CALLBACK ОБРАБОТЧИК ----------
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data_cb = query.data

    if data_cb == "main_menu":
        await show_main_menu(update, context, edit_message=True, chat_id=query.message.chat_id, message_id=query.message.message_id)
        return

    if data_cb == "groups": await show_groups(update, context); return
    if data_cb == "show_tariffs": await show_tariffs(update, context); return
    if data_cb == "profile": await show_profile(update, context); return
    if data_cb == "noop": await query.answer(); return

    if data_cb.startswith("tariff_info_"): await show_tariff_info(update, context, data_cb.split("_")[2]); return
    if data_cb.startswith("buy_"): await buy_tariff(update, context, data_cb.split("_")[1]); return
    if data_cb.startswith("check_payment_"): await check_payment(update, context, data_cb.split("_")[2]); return

    if data_cb == "admin_panel": await admin_panel(update, context); return
    if data_cb == "admin_stats": await admin_stats(update, context); return
    if data_cb == "admin_group_info": await admin_group_info_request(update, context); return
    if data_cb == "admin_users": await admin_users(update, context); return
    if data_cb == "admin_backup": await admin_backup(update, context); return
    if data_cb == "admin_broadcast": await admin_broadcast(update, context); return

    if data_cb.startswith("group_anti_spam_"): await group_anti_spam_menu(update, context, int(data_cb.split("_")[3])); return
    if data_cb.startswith("group_strict_anti_spam_"): await group_strict_anti_spam_menu(update, context, int(data_cb.split("_")[4])); return
    if data_cb.startswith("group_caps_threshold_"): await group_caps_threshold_menu(update, context, int(data_cb.split("_")[3])); return
    if data_cb.startswith("group_select_caps_"): await group_set_caps_threshold(update, context, int(data_cb.split("_")[3]), int(data_cb.split("_")[4])); return
    if data_cb.startswith("group_toggle_links_"): await group_toggle_setting(update, context, "block_links", int(data_cb.split("_")[3])); return
    if data_cb.startswith("group_toggle_invite_"): await group_toggle_setting(update, context, "invite_links_block", int(data_cb.split("_")[3])); return
    if data_cb.startswith("group_toggle_caps_"): await group_toggle_setting(update, context, "caps_filter", int(data_cb.split("_")[3])); return
    if data_cb.startswith("group_toggle_media_"): await group_toggle_setting(update, context, "block_media", int(data_cb.split("_")[3])); return
    if data_cb.startswith("group_toggle_files_"): await group_toggle_setting(update, context, "check_files", int(data_cb.split("_")[3])); return
    if data_cb.startswith("group_set_welcome_"): await group_set_welcome(update, context, int(data_cb.split("_")[3])); return
    if data_cb.startswith("group_stats_"): await group_show_stats(update, context, int(data_cb.split("_")[2])); return
    if data_cb.startswith("group_back_"): await group_back(update, context, int(data_cb.split("_")[2])); return
    
    if data_cb.startswith("group_limit_inc_"): await group_change_flood_parameter(update, context, "flood_limit", 1, int(data_cb.split("_")[3])); return
    if data_cb.startswith("group_limit_dec_"): await group_change_flood_parameter(update, context, "flood_limit", -1, int(data_cb.split("_")[3])); return
    if data_cb.startswith("group_window_inc_"): await group_change_flood_parameter(update, context, "flood_window", 5, int(data_cb.split("_")[3])); return
    if data_cb.startswith("group_window_dec_"): await group_change_flood_parameter(update, context, "flood_window", -5, int(data_cb.split("_")[3])); return
    if data_cb.startswith("group_mute_inc_"): await group_change_flood_parameter(update, context, "flood_mute", 30, int(data_cb.split("_")[3])); return
    if data_cb.startswith("group_mute_dec_"): await group_change_flood_parameter(update, context, "flood_mute", -30, int(data_cb.split("_")[3])); return
    
    if data_cb.startswith("group_strict_limit_inc_"): await group_change_strict_parameter(update, context, "strict_flood_limit", 1, int(data_cb.split("_")[4])); return
    if data_cb.startswith("group_strict_limit_dec_"): await group_change_strict_parameter(update, context, "strict_flood_limit", -1, int(data_cb.split("_")[4])); return
    if data_cb.startswith("group_strict_window_inc_"): await group_change_strict_parameter(update, context, "strict_flood_window", 5, int(data_cb.split("_")[4])); return
    if data_cb.startswith("group_strict_window_dec_"): await group_change_strict_parameter(update, context, "strict_flood_window", -5, int(data_cb.split("_")[4])); return
    if data_cb.startswith("group_strict_mute_inc_"): await group_change_strict_parameter(update, context, "strict_flood_mute", 60, int(data_cb.split("_")[4])); return
    if data_cb.startswith("group_strict_mute_dec_"): await group_change_strict_parameter(update, context, "strict_flood_mute", -60, int(data_cb.split("_")[4])); return

    # Заглушки для старых кнопок ЛС (если они случайно нажмутся)
    if data_cb.startswith(("anti_spam_", "strict_anti_spam_", "limit_inc_", "limit_dec_", "toggle_", "stats_")):
        await query.answer("Управление теперь в группе: /group_menu", show_alert=True)
        return

    await query.answer("Действие не распознано", show_alert=False)

# ---------- ЗАПУСК ----------
def main():
    logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
    load_data()

    application = Application.builder().token(TOKEN).build()

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, handle_message))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_chat_members))

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", start))
    application.add_handler(CommandHandler("addgroup", addgroup))
    application.add_handler(CommandHandler("group_menu", group_menu))
    application.add_handler(CommandHandler("broadcast", cmd_broadcast)) # Команда для рассылки

    # Старые команды модерации
    application.add_handler(CommandHandler("mute", cmd_mute))
    application.add_handler(CommandHandler("unmute", cmd_unmute))
    application.add_handler(CommandHandler("ban", cmd_ban))
    application.add_handler(CommandHandler("unban", cmd_unban))
    application.add_handler(CommandHandler("warn", cmd_warn))
    application.add_handler(CommandHandler("warns", cmd_warns))

    application.add_handler(CallbackQueryHandler(button_callback))

    logging.info("✅ Бот запущен")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
