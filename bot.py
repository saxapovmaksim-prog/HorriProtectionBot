import asyncio
import json
import logging
import os
import re
import uuid
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
        "Максимальная защита и аналит��ка для топовых проектов:\n"
        "🚀 *Всё из Стандартного тарифа*\n"
        "🚀 Умная проверка ссылок соцсетей (Telegram, YT, TikTok, Insta, VK)\n"
        "🚀 AI-модерация контента (настраиваемая нейросеть)\n"
        "🚀 Расширенная статистика чата и нарушений\n\n"
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
    "warnings": {},
    # НОВЫЕ НАСТРОЙКИ
    "link_review": {"tg": False, "yt": False, "tt": False, "ig": False, "vk": False},
    "whitelisted_links": {},
    "seen_users": [],
    "ai_enabled": False,
    "ai_prompt": "Ты модератор чата. Анализируй сообщения на токсичность и спам.",
    "ai_strictness": 50
}

# ---------- ГЛОБАЛЬНЫЕ ДАННЫЕ ----------
data: Dict = {"groups": {}}
user_data: Dict = {}
user_messages: Dict[int, List[datetime]] = defaultdict(list)
pending_payments: Dict[str, dict] = {}
user_states: Dict[int, str] = {}
pending_reviews: Dict[str, dict] = {} # Хранилище ссылок на проверке

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
def mask_id(uid) -> str:
    """Скрывает ID владельца бота от чужих глаз"""
    return "[Скрыто]" if str(uid) == str(ADMIN_ID) else str(uid)

def extract_urls(text: str) -> List[str]:
    """Извлекает все ссылки из текста"""
    return re.findall(r'(https?://[^\s]+|www\.[^\s]+)', text)

def get_platform(url: str) -> Optional[str]:
    """Определяет платформу по ссылке"""
    u = url.lower()
    if 't.me' in u or 'telegram.me' in u: return 'tg'
    if 'youtube.com' in u or 'youtu.be' in u: return 'yt'
    if 'tiktok.com' in u: return 'tt'
    if 'instagram.com' in u: return 'ig'
    if 'vk.com' in u or 'vk.cc' in u: return 'vk'
    return None

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
        return False

async def get_group_owner(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
    try:
        admins = await context.bot.get_chat_administrators(chat_id)
        for a in admins:
            if a.status == "creator":
                return a.user.id
        return None
    except Exception as e:
        return None

def parse_duration(text: str) -> int:
    """Умный парсер времени"""
    if not text:
        return 60
    text = text.strip().lower()
    match = re.match(r'(\d+)\s*([smhdсмчд])?', text)
    if not match:
        return 60
    num = int(match.group(1))
    unit = match.group(2)
    if unit in ('s', 'с'):
        return num
    elif unit in ('m', 'м'):
        return num * 60
    elif unit in ('h', 'ч'):
        return num * 3600
    elif unit in ('d', 'д'):
        return num * 86400
    else:
        return num * 60

async def process_admin_text_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ОБРАБОТКА КОМАНД АДМИНА ЧЕРЕЗ ОТВЕТ (*мут 2ч, *бан)"""
    message = update.effective_message
    chat_id = update.effective_chat.id
    target_user = message.reply_to_message.from_user

    if target_user.is_bot:
        return

    parts = message.text.lower().split()
    cmd = parts[0]

    try:
        if cmd == '*мут':
            duration = 3600 # 1 час по умолчанию
            reason = "Нарушение правил"
            if len(parts) > 1:
                if parts[1][0].isdigit():
                    duration = parse_duration(parts[1])
                    reason = " ".join(message.text.split()[2:]) if len(parts) > 2 else reason
                else:
                    reason = " ".join(message.text.split()[1:])
            
            await mute_user(chat_id, target_user.id, duration, reason, context)
            await message.delete()

        elif cmd == '*бан':
            reason = " ".join(message.text.split()[1:]) if len(parts) > 1 else "Нарушение правил"
            await ban_user(chat_id, target_user.id, reason, context)
            await message.delete()

        elif cmd == '*размут':
            await unmute_user(chat_id, target_user.id, context)
            await message.delete()

        elif cmd == '*разбан':
            await unban_user(chat_id, target_user.id, context)
            await message.delete()
            
    except Exception as e:
        logging.error(f"Ошибка выполнения текстовой команды: {e}")

async def restrict_user(chat_id: int, user_id: int, duration: int, reason: str, context: ContextTypes.DEFAULT_TYPE):
    await mute_user(chat_id, user_id, duration, reason, context)

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
            
        await context.bot.send_message(
            chat_id,
            f"🔇 Пользователь `{mask_id(user_id)}` получил мут на {dur_str}.\nПричина: {reason}",
            parse_mode="Markdown"
        )
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
        await context.bot.send_message(
            chat_id,
            f"🔊 Пользователь `{mask_id(user_id)}` размучен.",
            parse_mode="Markdown"
        )
        return True
    except Exception as e:
        logging.error(f"Не удалось размутить {user_id} в {chat_id}: {e}")
        return False

async def ban_user(chat_id: int, user_id: int, reason: str, context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.bot.ban_chat_member(chat_id, user_id)
        await context.bot.send_message(
            chat_id,
            f"⛔ Пользова��ель `{mask_id(user_id)}` забанен.\nПричина: {reason}",
            parse_mode="Markdown"
        )
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
        await context.bot.send_message(
            chat_id,
            f"✅ Пользователь `{mask_id(user_id)}` разбанен.",
            parse_mode="Markdown"
        )
        return True
    except Exception as e:
        logging.error(f"Не удалось разбанить {user_id} в {chat_id}: {e}")
        return False

async def add_warning(chat_id: int, user_id: int, reason: str, context: ContextTypes.DEFAULT_TYPE):
    settings = get_group_settings(chat_id)
    if not settings:
        return 0
    warnings = settings.get("warnings", {})
    user_warns = warnings.get(str(user_id), [])
    now = datetime.now()
    user_warns = [w for w in user_warns if datetime.fromisoformat(w["time"]) > now - timedelta(days=7)]
    user_warns.append({"time": now.isoformat(), "reason": reason})
    if len(user_warns) > 10:
        user_warns = user_warns[-10:]
    warnings[str(user_id)] = user_warns
    settings["warnings"] = warnings
    update_group_setting(chat_id, "warnings", warnings)

    await context.bot.send_message(
        chat_id,
        f"⚠️ Пользователь `{mask_id(user_id)}` получил предупреждение.\nПричина: {reason}\nВсего предупреждений: {len(user_warns)}",
        parse_mode="Markdown"
    )

    if len(user_warns) >= 3:
        await mute_user(chat_id, user_id, 3600, "3 предупреждения (автоматический мут)", context)
        warnings[str(user_id)] = []
        settings["warnings"] = warnings
        update_group_setting(chat_id, "warnings", warnings)
    return len(user_warns)

async def get_warnings(chat_id: int, user_id: int) -> int:
    settings = get_group_settings(chat_id)
    if not settings:
        return 0
    warnings = settings.get("warnings", {})
    user_warns = warnings.get(str(user_id), [])
    now = datetime.now()
    user_warns = [w for w in user_warns if datetime.fromisoformat(w["time"]) > now - timedelta(days=7)]
    return len(user_warns)

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

        # Трекинг участников для админ-панели (Найти группу)
        if user.id not in settings.setdefault("seen_users", []):
            settings["seen_users"].append(user.id)
            update_group_setting(chat.id, "seen_users", settings["seen_users"])

        settings["stats"]["messages"] += 1
        update_group_setting(chat.id, "stats", settings["stats"])

        # ПРОВЕРКА НА ТЕКСТОВЫЕ КОМАНДЫ АДМИНА (*мут, *бан)
        if message.reply_to_message and message.text:
            text_lower = message.text.lower()
            if text_lower.startswith(('*мут', '*бан', '*размут', '*разбан')):
                if await is_group_admin(chat.id, user.id, context):
                    await process_admin_text_command(update, context)
                    return # Прерываем, если это команда

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
        
        # 1. АНТИФЛУД
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

        # 2. УМНАЯ ПРОВЕРКА ССЫЛОК СОЦСЕТЕЙ (ТОЛЬКО ДЛЯ PRO)
        if message.text and owner_tariff == "pro":
            urls = extract_urls(message.text)
            review_triggered = None
            for url in urls:
                plat = get_platform(url)
                if plat and settings.get("link_review", {}).get(plat):
                    wl = settings.get("whitelisted_links", {})
                    # Очистка устаревших ссылок
                    now = datetime.now()
                    wl = {k: v for k, v in wl.items() if datetime.fromisoformat(v) > now}
                    update_group_setting(chat.id, "whitelisted_links", wl)
                    
                    if url not in wl:
                        review_triggered = url
                        break
            
            if review_triggered:
                try: await message.delete()
                except: pass
                lid = str(uuid.uuid4())[:8]
                pending_reviews[lid] = {"url": review_triggered, "user_id": user.id, "chat_id": chat.id, "text": message.text}
                kb = [[InlineKeyboardButton("✅ Одобрить", callback_data=f"aprv_{lid}"),
                       InlineKeyboardButton("❌ Отказать", callback_data=f"rjct_{lid}")]]
                await context.bot.send_message(
                    chat.id,
                    f"🚨 *Ссылка отправлена на проверку администраторам*\nОт: {user.full_name} (`{mask_id(user.id)}`)\nТекст: {message.text}\nСсылка: {review_triggered}",
                    reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
                )
                return

        # 3. ОБЫЧНЫЕ ССЫЛКИ
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

        # 4. КАПС И МЕДИА
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

        # 5. ИИ МОДЕРАТОР
        if settings.get("ai_enabled", False) and owner_tariff == "pro":
            # prompt = settings.get("ai_prompt")
            # strictness = settings.get("ai_strictness")
            pass

# ---------- НОВЫЕ УЧАСТНИКИ ----------
async def handle_new_chat_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    for member in update.message.new_chat_members:
        if member.id == context.bot.id:
            await update.message.reply_text(
                "🤖 *Бот-защитник активирован!*\n\n"
                "Для добавления этой группы в систему:\n"
                "1. Назначьте бота администратором.\n"
                "2. В этой группе отправьте команду /addgroup\n\n"
                "Настройте защиту через /menu в личных сообщениях или /group_menu в группе.",
                parse_mode="Markdown"
            )
            return

    settings = get_group_settings(chat.id)
    if settings and settings.get("custom_welcome"):
        for member in update.message.new_chat_members:
            welcome = settings["custom_welcome"].replace("{name}", member.full_name)
            await update.message.reply_text(welcome)

# ---------- ДОБАВЛЕНИЕ ГРУППЫ ----------
async def addgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user

    if not chat or chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Эта команда работает только в группах.")
        return

    if not await is_group_admin(chat.id, user.id, context):
        await update.message.reply_text("⛔ Только администраторы группы могут добавить бота.")
        return

    if get_group_data(chat.id):
        await update.message.reply_text("✅ Группа уже добавлена.")
        return

    owner_id = await get_group_owner(chat.id, context)
    if not owner_id:
        owner_id = user.id

    create_group(chat.id, owner_id)
    await update.message.reply_text(f"✅ Группа {chat.title or chat.id} добавлена! Владелец: `{mask_id(owner_id)}`", parse_mode="Markdown")

# ---------- КОМАНДЫ МОДЕРАЦИИ ----------
async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if not chat or chat.type not in ("group", "supergroup"):
        return
    if not await is_group_admin(chat.id, user.id, context):
        return

    if not context.args:
        await update.message.reply_text("Использование: /mute @пользователь [время]")
        return
    target = None
    duration_str = None
    if context.args[0].startswith('@'):
        try: target = (await chat.get_member(context.args[0][1:])).user.id
        except: return await update.message.reply_text("Пользователь не найден.")
        if len(context.args) > 1: duration_str = context.args[1]
    else:
        try: target = int(context.args[0])
        except: return await update.message.reply_text("Укажите пользователя (ID или @username).")
        if len(context.args) > 1: duration_str = context.args[1]

    if not target: return await update.message.reply_text("Пользователь не найден.")
    duration = parse_duration(duration_str)
    await mute_user(chat.id, target, duration, f"Команда /mute от {mask_id(user.id)}", context)

async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if not chat or chat.type not in ("group", "supergroup"): return
    if not await is_group_admin(chat.id, user.id, context): return
    if not context.args: return await update.message.reply_text("Использование: /unmute @пользователь")
    try: target = (await chat.get_member(context.args[0][1:])).user.id if context.args[0].startswith('@') else int(context.args[0])
    except: return
    await unmute_user(chat.id, target, context)

async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if not chat or chat.type not in ("group", "supergroup"): return
    if not await is_group_admin(chat.id, user.id, context): return
    if not context.args: return await update.message.reply_text("Использование: /ban @пользователь")
    try: target = (await chat.get_member(context.args[0][1:])).user.id if context.args[0].startswith('@') else int(context.args[0])
    except: return
    await ban_user(chat.id, target, f"Команда /ban от {mask_id(user.id)}", context)

async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if not chat or chat.type not in ("group", "supergroup"): return
    if not await is_group_admin(chat.id, user.id, context): return
    if not context.args: return await update.message.reply_text("Использование: /unban @пользователь")
    try: target = int(context.args[0])
    except: return await update.message.reply_text("Укажите числовой ID пользователя.")
    await unban_user(chat.id, target, context)

async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if not chat or chat.type not in ("group", "supergroup"): return
    if not await is_group_admin(chat.id, user.id, context): return
    if not context.args: return await update.message.reply_text("Использование: /warn @пользователь [причина]")
    try: target = (await chat.get_member(context.args[0][1:])).user.id if context.args[0].startswith('@') else int(context.args[0])
    except: return
    reason = " ".join(context.args[1:]) if len(context.args) > 1 else "Без указания причины"
    await add_warning(chat.id, target, reason, context)

async def cmd_warns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if not chat or chat.type not in ("group", "supergroup"): return
    if not await is_group_admin(chat.id, user.id, context): return
    if not context.args: return await update.message.reply_text("Использование: /warns @пользователь")
    try: target = (await chat.get_member(context.args[0][1:])).user.id if context.args[0].startswith('@') else int(context.args[0])
    except: return
    count = await get_warnings(chat.id, target)
    await update.message.reply_text(f"📊 У пользователя `{mask_id(target)}` {count} предупреждений.", parse_mode="Markdown")
    # ---------- МЕНЮ ЛИЧНЫХ СООБЩЕНИЙ ----------
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

    text = "👋 *Главное меню*\nВыберите действие:"
    if edit_message and chat_id and message_id:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    user = register_user(user_id)
    tariff = get_user_tariff(user_id)
    reg_date = datetime.fromisoformat(user["registered"]).strftime("%d.%m.%Y %H:%M")
    
    text = (
        f"👤 *Ваш профиль*\n\n"
        f"🆔 ID: `{mask_id(user_id)}`\n"
        f"📅 Регистрация: {reg_date}\n"
        f"💎 Тариф: *{tariff.upper()}*\n"
    )
    if tariff != "free" and user.get("expiry"):
        text += f"⏰ Действует до: {datetime.fromisoformat(user['expiry']).strftime('%d.%m.%Y')}\n"

    if user_id == ADMIN_ID:
        text += "\n👑 *Вы являетесь главным администратором.*"

    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def show_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not data["groups"]:
        await query.edit_message_text(
            "Нет добавленных групп.\n\n"
            "Чтобы добавить группу:\n"
            "1. Добавьте бота в группу и дайте ему права администратора.\n"
            "2. В группе отправьте команду /addgroup (только для администраторов).",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]])
        )
        return
    keyboard = []
    for cid in data["groups"].keys():
        try:
            name = (await context.bot.get_chat(int(cid))).title or f"Группа {cid}"
        except:
            name = f"Группа {cid}"
        keyboard.append([InlineKeyboardButton(name, callback_data=f"group_main_{cid}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="main_menu")])
    await query.edit_message_text("📋 *Список групп:*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

# ---------- МЕНЮ И ИНТЕРФЕЙС ГРУППЫ ----------
async def group_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, query=None, override_chat_id=None):
    """Единое меню настроек (работает и из ЛС, и из группы)"""
    chat_id = override_chat_id or update.effective_chat.id
    user_id = query.from_user.id if query else update.effective_user.id
    
    if not await is_group_admin(chat_id, user_id, context):
        if not query:
            await update.message.reply_text("⛔ Только администраторы группы могут настраивать бота.")
        return

    g = get_group_data(chat_id)
    if not g:
        if not query: 
            await update.message.reply_text("⚠️ Группа не добавлена. Введите /addgroup.")
        return

    owner_id = g["owner"]
    tariff = get_user_tariff(owner_id)

    text = (
        f"🛡 *Настройки группы*\n"
        f"ID: `{chat_id}`\n"
        f"Владелец: `{mask_id(owner_id)}` ({tariff.upper()})\n\n"
        f"Выберите раздел для настройки:"
    )
    keyboard = [
        [InlineKeyboardButton("⚙️ Антиспам", callback_data=f"group_anti_spam_{chat_id}"),
         InlineKeyboardButton("🔗 Ссылки и Модерация", callback_data=f"group_links_menu_{chat_id}")],
        [InlineKeyboardButton("🔠 CAPS и Медиа", callback_data=f"group_media_menu_{chat_id}"),
         InlineKeyboardButton("🤖 Настройка ИИ", callback_data=f"group_ai_menu_{chat_id}")],
        [InlineKeyboardButton("✏️ Приветствие", callback_data=f"group_set_welcome_{chat_id}"),
         InlineKeyboardButton("📊 Статистика", callback_data=f"group_stats_{chat_id}")]
    ]
    if query and query.message.chat.type == "private":
        keyboard.append([InlineKeyboardButton("🔙 К списку групп", callback_data="groups")])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    elif query:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def group_anti_spam_menu(query, chat_id, context):
    settings = get_group_settings(chat_id)
    tariff = get_user_tariff(get_group_data(chat_id)["owner"])
    is_adv = TARIFF_FEATURES[tariff].get("strict_flood", False)
    
    text = (
        f"*Настройка антиспама*\n"
        f"Базовый лимит: {settings['flood_limit']} сообщ. за {settings['flood_window']} сек\n"
        f"Мут: {settings['flood_mute']} сек\n"
    )
    if is_adv:
        text += (
            f"\n*Строгий (9/3):*\n"
            f"Лимит: {settings.get('strict_flood_limit',9)} за {settings.get('strict_flood_window',3)} сек\n"
            f"Мут: {settings.get('strict_flood_mute',600)} сек\n"
        )
    
    keyboard = [
        [InlineKeyboardButton("📈 +1", callback_data=f"limit_inc_{chat_id}"),
         InlineKeyboardButton("📉 -1", callback_data=f"limit_dec_{chat_id}")],
        [InlineKeyboardButton("⏱ +5с", callback_data=f"window_inc_{chat_id}"),
         InlineKeyboardButton("⏱ -5с", callback_data=f"window_dec_{chat_id}")],
        [InlineKeyboardButton("🔇 +30с мут", callback_data=f"mute_inc_{chat_id}"),
         InlineKeyboardButton("🔊 -30с мут", callback_data=f"mute_dec_{chat_id}")],
    ]
    if is_adv:
        keyboard.append([InlineKeyboardButton("🔧 Настроить строгий режим", callback_data=f"group_strict_anti_spam_{chat_id}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=f"group_main_{chat_id}")])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def group_strict_anti_spam_menu(query, chat_id, context):
    settings = get_group_settings(chat_id)
    text = (
        f"*Настройка строгого антиспама*\n"
        f"Лимит: {settings.get('strict_flood_limit',9)}\n"
        f"Окно: {settings.get('strict_flood_window',3)} сек\n"
        f"Мут: {settings.get('strict_flood_mute',600)} сек"
    )
    keyboard = [
        [InlineKeyboardButton("📈 +1", callback_data=f"s_limit_inc_{chat_id}"),
         InlineKeyboardButton("📉 -1", callback_data=f"s_limit_dec_{chat_id}")],
        [InlineKeyboardButton("⏱ +5с", callback_data=f"s_window_inc_{chat_id}"),
         InlineKeyboardButton("⏱ -5с", callback_data=f"s_window_dec_{chat_id}")],
        [InlineKeyboardButton("🔇 +60с мут", callback_data=f"s_mute_inc_{chat_id}"),
         InlineKeyboardButton("🔊 -60с мут", callback_data=f"s_mute_dec_{chat_id}")],
        [InlineKeyboardButton("🔙 Назад", callback_data=f"group_anti_spam_{chat_id}")]
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def group_links_menu(query, chat_id, context):
    settings = get_group_settings(chat_id)
    tariff = get_user_tariff(get_group_data(chat_id)["owner"])
    text = (
        f"🔗 *Настройка ссылок*\n\n"
        f"Блокировка любых ссылок: {'✅' if settings['block_links'] else '❌'}\n"
        f"Блокировка инвайтов: {'✅' if settings['invite_links_block'] else '❌'}"
    )
    keyboard = [
        [InlineKeyboardButton(f"Обычные ссылки: {'ВКЛ' if settings['block_links'] else 'ВЫКЛ'}", callback_data=f"toggle_block_links_{chat_id}")],
        [InlineKeyboardButton(f"Инвайты: {'ВКЛ' if settings['invite_links_block'] else 'ВЫКЛ'}", callback_data=f"toggle_invite_links_block_{chat_id}")]
    ]
    if tariff == "pro":
        keyboard.append([InlineKeyboardButton("🛡 Умная проверка соцсетей", callback_data=f"group_link_review_{chat_id}")])
    else:
        keyboard.append([InlineKeyboardButton("🔒 Проверка соцсетей (Только PRO)", callback_data="noop")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=f"group_main_{chat_id}")])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def group_link_review_menu(query, chat_id, context):
    settings = get_group_settings(chat_id).setdefault("link_review", {"tg": False, "yt": False, "tt": False, "ig": False, "vk": False})
    text = (
        "🛡 *Отправка ссылок на проверку админам*\n\n"
        "Выберите платформы, ссылки на которые бот будет удалять и отправлять вам в чат "
        "с кнопками Одобрить/Отказать (При одобрении выдается иммунитет на 1 час):"
    )
    keyboard = [
        [InlineKeyboardButton(f"Telegram {'✅' if settings['tg'] else '❌'}", callback_data=f"tog_rev_tg_{chat_id}"),
         InlineKeyboardButton(f"YouTube {'✅' if settings['yt'] else '❌'}", callback_data=f"tog_rev_yt_{chat_id}")],
        [InlineKeyboardButton(f"TikTok {'✅' if settings['tt'] else '❌'}", callback_data=f"tog_rev_tt_{chat_id}"),
         InlineKeyboardButton(f"Insta {'✅' if settings['ig'] else '❌'}", callback_data=f"tog_rev_ig_{chat_id}")],
        [InlineKeyboardButton(f"VK {'✅' if settings['vk'] else '❌'}", callback_data=f"tog_rev_vk_{chat_id}")],
        [InlineKeyboardButton("🔙 Назад", callback_data=f"group_links_menu_{chat_id}")]
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def group_media_menu(query, chat_id, context):
    settings = get_group_settings(chat_id)
    tariff = get_user_tariff(get_group_data(chat_id)["owner"])
    text = "🔠 *CAPS и Медиафайлы*"
    keyboard = []
    
    if TARIFF_FEATURES[tariff]["caps_filter"]:
        keyboard.append([InlineKeyboardButton(f"CAPS фильтр: {'ВКЛ' if settings['caps_filter'] else 'ВЫКЛ'}", callback_data=f"toggle_caps_filter_{chat_id}")])
        keyboard.append([InlineKeyboardButton(f"Порог CAPS: {settings.get('caps_threshold',70)}%", callback_data=f"group_caps_threshold_{chat_id}")])
    if TARIFF_FEATURES[tariff]["block_media"]:
        keyboard.append([InlineKeyboardButton(f"Блок медиа: {'ВКЛ' if settings['block_media'] else 'ВЫКЛ'}", callback_data=f"toggle_block_media_{chat_id}")])
        
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=f"group_main_{chat_id}")])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def group_caps_threshold_menu(query, chat_id, context):
    settings = get_group_settings(chat_id)
    text = f"*Настройка порога CAPS*\nТекущий порог: {settings.get('caps_threshold', 70)}%\n\nВыберите новый порог:"
    keyboard = []
    for t in [10, 30, 50, 70, 100]:
        keyboard.append([InlineKeyboardButton(f"{t}%", callback_data=f"set_caps_{t}_{chat_id}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=f"group_media_menu_{chat_id}")])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def group_ai_menu(query, chat_id, context):
    settings = get_group_settings(chat_id)
    tariff = get_user_tariff(get_group_data(chat_id)["owner"])
    if tariff != "pro":
        await query.answer("🤖 ИИ модерация доступна только на тарифе PRO!", show_alert=True)
        return
    
    text = (
        f"🤖 *Настройки ИИ Модератора*\n\n"
        f"Состояние: *{'Включен' if settings.get('ai_enabled') else 'Выключен'}*\n"
        f"Строгость: {settings.get('ai_strictness', 50)}/100\n\n"
        f"*Текущий промпт (инструкция нейросети):*\n_{settings.get('ai_prompt', 'Стандартный')}_"
    )
    keyboard = [
        [InlineKeyboardButton(f"ИИ: {'Выключить' if settings.get('ai_enabled') else 'Включить'}", callback_data=f"toggle_ai_enabled_{chat_id}")],
        [InlineKeyboardButton("✏️ Изменить промпт", callback_data=f"set_ai_prompt_{chat_id}")],
        [InlineKeyboardButton("🔙 Назад", callback_data=f"group_main_{chat_id}")]
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def group_show_stats(query, chat_id, context):
    g = get_group_data(chat_id)
    if get_user_tariff(g["owner"]) != "pro":
        await query.answer("📊 Доступно только на PRO", show_alert=True)
        return
        
    stats = g["settings"]["stats"]
    text = f"*📊 Статистика группы*\n\nСообщений: {stats['messages']}\nНарушений: {stats['violations']}\n\n*Последние нарушения:*\n"
    for e in stats.get("history", [])[-10:]:
        dt = datetime.fromisoformat(e['time']).strftime('%d.%m %H:%M')
        text += f"• {dt} – {e['reason']}\n"
        
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data=f"group_main_{chat_id}")]]))

# ---------- ТАРИФЫ И КРИПТО ОПЛАТА ----------
async def show_tariffs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    text = "*Доступные тарифы:*\n\n" + "\n\n".join(TARIFF_DESCRIPTIONS.values()) + "\n\n⏰ *Все платные тарифы действуют 1 месяц.*"
    keyboard = [
        [InlineKeyboardButton("🆓 Бесплатный", callback_data="tariff_info_free")],
        [InlineKeyboardButton("⭐ Стандартный (99 руб)", callback_data="tariff_info_standard")],
        [InlineKeyboardButton("💎 Профессиональный (199 руб)", callback_data="tariff_info_pro")],
        [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def show_tariff_info(query, tariff: str, context):
    if tariff == "free":
        return await query.edit_message_text(
            TARIFF_DESCRIPTIONS["free"] + "\n\n✅ Этот тариф уже активен по умолчанию.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="show_tariffs")]])
        )
    
    text = (
        TARIFF_DESCRIPTIONS[tariff] + 
        f"\n\nСтоимость: {PRICES_RUB[tariff]} руб. (≈{PRICES_USD[tariff]} USD)\n"
        "После оплаты тариф будет активирован на 30 дней.\n\nВыберите действие:"
    )
    keyboard = [
        [InlineKeyboardButton("💳 Купить", callback_data=f"buy_{tariff}")],
        [InlineKeyboardButton("🔙 Назад", callback_data="show_tariffs")]
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

def create_crypto_invoice(amount_usd: float, description: str) -> Optional[Dict]:
    url = "https://pay.crypt.bot/api/createInvoice"
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN, "Content-Type": "application/json"}
    payload = {
        "asset": "USDT", "amount": amount_usd, "description": description,
        "paid_btn_name": "callback", "paid_btn_url": "https://t.me/YourBotUsername"
    }
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        if response.json().get("ok"): return response.json()["result"]
    except Exception as e:
        logging.error(f"CryptoBot error: {e}")
    return None

def check_invoice_status(invoice_id: str) -> str:
    url = "https://pay.crypt.bot/api/getInvoices"
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}
    try:
        response = requests.get(url, headers=headers, params={"invoice_ids": invoice_id}, timeout=10).json()
        if response.get("ok") and response["result"]["items"]:
            return response["result"]["items"][0]["status"]
    except Exception as e:
        logging.error(f"Check invoice error: {e}")
    return ""

async def buy_tariff(query, tariff: str, context):
    price_usd = PRICES_USD[tariff]
    invoice = create_crypto_invoice(price_usd, f"Активация тарифа {tariff.upper()} на 30 дней")
    
    if not invoice:
        await query.edit_message_text("❌ Ошибка создания счёта. Попробуйте позже.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="show_tariffs")]]))
        return
    
    invoice_id = str(invoice["invoice_id"])
    pending_payments[invoice_id] = {"user_id": query.from_user.id, "tariff": tariff}
    
    keyboard = [
        [InlineKeyboardButton("💳 Оплатить", url=invoice["pay_url"])],
        [InlineKeyboardButton("✅ Проверить оплату", callback_data=f"check_payment_{invoice_id}")],
        [InlineKeyboardButton("❌ Отмена", callback_data="show_tariffs")]
    ]
    text = (
        f"💸 *Оплата тарифа {tariff.upper()}*\n"
        f"Стоимость: {PRICES_RUB[tariff]} руб. (≈{price_usd} USD)\n"
        f"Тариф будет активирован на 30 дней.\n\n"
        f"После оплаты нажмите «Проверить оплату»."
    )
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def check_payment(query, invoice_id: str, context):
    if invoice_id not in pending_payments:
        await query.edit_message_text("❌ Счёт не найден или устарел.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="show_tariffs")]]))
        return
        
    info = pending_payments[invoice_id]
    if check_invoice_status(invoice_id) == "paid":
        set_user_tariff(info["user_id"], info["tariff"], 30)
        del pending_payments[invoice_id]
        await query.edit_message_text(
            f"✅ *Оплата подтверждена!*\nТариф {info['tariff'].upper()} активирован на 30 дней.\n",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 В меню", callback_data="main_menu")]])
        )
    else:
        await query.answer("⏳ Оплата пока не обнаружена. Попробуйте снова чуть позже.", show_alert=True)

# ---------- АДМИН-ПАНЕЛЬ ВЛАДЕЛЬЦА ----------
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != ADMIN_ID:
        return await query.edit_message_text("⛔ У вас нет доступа.")
        
    keyboard = [
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"), InlineKeyboardButton("👥 Пользователи", callback_data="admin_users")],
        [InlineKeyboardButton("🔍 Найти группу", callback_data="admin_find_group")],
        [InlineKeyboardButton("📥 Скачать бэкапы", callback_data="admin_backup"), InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
    ]
    await query.edit_message_text("👑 *Админ-панель владельца*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def admin_stats(query, context):
    text = "*📊 Список групп:*\n"
    for cid, g in data["groups"].items():
        try:
            name = (await context.bot.get_chat(int(cid))).title
        except:
            name = f"Группа {cid}"
        owner_tariff = get_user_tariff(g['owner'])
        text += f"• {name} (`{cid}`) – владелец `{mask_id(g['owner'])}` ({owner_tariff.upper()})\n"
        
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]]))

async def admin_users(query, context):
    text = "*👥 Пользователи бота:*\n"
    for uid, u in user_data.items():
        reg_d = datetime.fromisoformat(u['registered']).strftime('%d.%m.%Y')
        text += f"• `{mask_id(uid)}` – {u['tariff'].upper()}, рег: {reg_d}\n"
        if len(text) > 3500:
            text += "\n...(список слишком длинный, показана часть)"
            break
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]]))

async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    text = update.message.text.replace('/broadcast', '').strip()
    if not text:
        await update.message.reply_text("Использование: /broadcast Ваш текст")
        return
    await update.message.reply_text("⏳ Начинаю рассылку...")
    count = 0
    for uid in list(user_data.keys()):
        try:
            await context.bot.send_message(chat_id=int(uid), text=text)
            count += 1
            await asyncio.sleep(0.05)
        except:
            pass
    await update.message.reply_text(f"✅ Рассылка завершена! Доставлено: {count} пользователям.")

# ---------- ОБРАБОТЧИКИ ТЕКСТА (ВВОД ID/ПРОМПТА) ----------
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user_id = update.effective_user.id
    if not msg or not msg.text: return

    state = user_states.get(user_id)
    
    # 1. Поиск группы админом
    if user_id == ADMIN_ID and state == "await_find_group":
        del user_states[user_id]
        try:
            chat_id = int(msg.text.strip())
            chat_obj = await context.bot.get_chat(chat_id)
            admins = await context.bot.get_chat_administrators(chat_id)
            g = get_group_data(chat_id)
            seen = g["settings"].get("seen_users", []) if g else []
            
            owner = next((a.user.id for a in admins if a.status == "creator"), None)
            text = f"📊 *Группа:* {chat_obj.title} (`{chat_id}`)\n👑 *Владелец:* `{mask_id(owner)}`\n\n🛡 *Админы:*\n"
            for a in admins:
                text += f"- {a.user.full_name} (`{mask_id(a.user.id)}`)\n"
            
            text += f"\n👥 *Замечено участников: {len(seen)}*\n"
            for u in seen[:30]:
                text += f"- `{mask_id(u)}`\n" 
            if len(seen) > 30:
                text += f"...и еще {len(seen)-30}"
                
            await msg.reply_text(text, parse_mode="Markdown")
        except Exception as e:
            await msg.reply_text(f"❌ Ошибка поиска: {e}")
        return

    # 2. Ввод промпта ИИ
    if state and state.startswith("await_ai_prompt_"):
        chat_id = int(state.split("_")[3])
        del user_states[user_id]
        update_group_setting(chat_id, "ai_prompt", msg.text[:500])
        await msg.reply_text("✅ Промпт ИИ успешно обновлен!")
        return
        
    # 3. Ввод кастомного приветствия
    if chat.type == "private" and state and state.startswith("welcome_chat_"):
        chat_id = int(state.split("_")[2])
        del user_states[user_id]
        if msg.text.strip():
            update_group_setting(chat_id, "custom_welcome", msg.text.strip())
            await msg.reply_text("✅ Кастомное приветствие сохранено.")
        else:
            update_group_setting(chat_id, "custom_welcome", None)
            await msg.reply_text("✅ Кастомное приветствие отключено.")
        return

# ---------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ИЗМЕНЕНИЯ ПАРАМЕТРОВ ----------
async def change_flood_parameter(update: Update, context: ContextTypes.DEFAULT_TYPE, param: str, delta: int, chat_id: int):
    settings = get_group_settings(chat_id)
    if settings:
        update_group_setting(chat_id, param, max(1, settings[param] + delta))

async def change_strict_parameter(update: Update, context: ContextTypes.DEFAULT_TYPE, param: str, delta: int, chat_id: int):
    settings = get_group_settings(chat_id)
    if settings:
        update_group_setting(chat_id, param, max(1, settings.get(param, 0) + delta))

# ---------- CALLBACK ROUTER ----------
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    d = q.data
    uid = q.from_user.id

    # -- Главное меню и базовые навигации --
    if d == "main_menu":
        await show_main_menu(update, context, True, q.message.chat_id, q.message.message_id)
        return
    if d == "profile": await show_profile(update, context); return
    if d == "groups": await show_groups(update, context); return
    if d == "show_tariffs": await show_tariffs(update, context); return
    if d.startswith("tariff_info_"): await show_tariff_info(q, d.split("_")[2], context); return
    if d.startswith("buy_"): await buy_tariff(q, d.split("_")[1], context); return
    if d.startswith("check_payment_"): await check_payment(q, d.split("_")[2], context); return

    # -- Админ панель владельца --
    if d == "admin_panel": await admin_panel(update, context); return
    if d == "admin_stats": await admin_stats(q, context); return
    if d == "admin_users": await admin_users(q, context); return
    
    if d == "admin_find_group" and uid == ADMIN_ID:
        user_states[uid] = "await_find_group"
        await q.message.reply_text("🔍 Введите ID группы (с минусом, если это супергруппа):")
        await q.answer()
        return
        
    if d == "admin_backup" and uid == ADMIN_ID:
        try:
            if os.path.exists(DATA_FILE): await context.bot.send_document(uid, open(DATA_FILE, 'rb'))
            if os.path.exists(USER_DATA_FILE): await context.bot.send_document(uid, open(USER_DATA_FILE, 'rb'))
            await q.answer("Бэкапы отправлены в ЛС!")
        except Exception as e:
            await q.answer(f"Ошибка выгрузки: {e}", show_alert=True)
        return
        
    if d == "admin_broadcast" and uid == ADMIN_ID:
        await q.edit_message_text(
            "📢 Для рассылки всем пользователям введите команду:\n`/broadcast Ваш текст`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]])
        )
        return

    # -- Проверка ссылок (Одобрить/Отказать) --
    if d.startswith("aprv_"):
        lid = d.split("_")[1]
        if lid in pending_reviews:
            if not await is_group_admin(pending_reviews[lid]["chat_id"], uid, context):
                return await q.answer("Это могут нажимать только администраторы группы!", show_alert=True)
            info = pending_reviews.pop(lid)
            wl = get_group_settings(info["chat_id"]).get("whitelisted_links", {})
            wl[info["url"]] = (datetime.now() + timedelta(hours=1)).isoformat()
            update_group_setting(info["chat_id"], "whitelisted_links", wl)
            await q.edit_message_text(f"✅ Ссылка одобрена администратором {q.from_user.full_name} на 1 час.")
        else:
            await q.answer("Запрос устарел.", show_alert=True)
        return

    if d.startswith("rjct_"):
        lid = d.split("_")[1]
        if lid in pending_reviews:
            if not await is_group_admin(pending_reviews[lid]["chat_id"], uid, context):
                return await q.answer("Это могут нажимать только администраторы группы!", show_alert=True)
            keyboard = [
                [InlineKeyboardButton("Мут 1 час", callback_data=f"pnsh_m1_{lid}"),
                 InlineKeyboardButton("Мут 24 часа", callback_data=f"pnsh_m24_{lid}")],
                [InlineKeyboardButton("Бан", callback_data=f"pnsh_b_{lid}"),
                 InlineKeyboardButton("Просто удалить", callback_data=f"pnsh_d_{lid}")]
            ]
            await q.edit_message_text(f"❌ Выберите наказание для нарушителя:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if d.startswith("pnsh_"):
        parts = d.split("_")
        action, lid = parts[1], parts[2]
        if lid in pending_reviews:
            info = pending_reviews.pop(lid)
            cid, t_id = info["chat_id"], info["user_id"]
            if action == "m1":
                await mute_user(cid, t_id, 3600, "Запрещенная ссылка (Решение админа)", context)
            elif action == "m24":
                await mute_user(cid, t_id, 86400, "Запрещенная ссылка (Решение админа)", context)
            elif action == "b":
                await ban_user(cid, t_id, "Запрещенная ссылка (Решение админа)", context)
            # action "d" ничего не делает, сообщение уже удалено
            await q.edit_message_text(f"✅ Наказание применено администратором {q.from_user.full_name}.")
        return

    # -- Навигация по групповым меню --
    if d.startswith("group_main_"): await group_menu(update, context, q, int(d.split("_")[2])); return
    if d.startswith("group_anti_spam_"): await group_anti_spam_menu(q, int(d.split("_")[3]), context); return
    if d.startswith("group_strict_anti_spam_"): await group_strict_anti_spam_menu(q, int(d.split("_")[4]), context); return
    if d.startswith("group_links_menu_"): await group_links_menu(q, int(d.split("_")[3]), context); return
    if d.startswith("group_link_review_"): await group_link_review_menu(q, int(d.split("_")[3]), context); return
    if d.startswith("group_media_menu_"): await group_media_menu(q, int(d.split("_")[3]), context); return
    if d.startswith("group_caps_threshold_"): await group_caps_threshold_menu(q, int(d.split("_")[3]), context); return
    if d.startswith("group_ai_menu_"): await group_ai_menu(q, int(d.split("_")[3]), context); return
    if d.startswith("group_stats_"): await group_show_stats(q, int(d.split("_")[2]), context); return
    
    # -- Тогглы настроек --
    if d.startswith("toggle_"):
        parts = d.split("_")
        chat_id = int(parts[-1])
        key = "_".join(parts[1:-1])
        settings = get_group_settings(chat_id)
        if key in settings:
            update_group_setting(chat_id, key, not settings[key])
            await q.answer("Настройка изменена!")
            if key in ["block_links", "invite_links_block"]: await group_links_menu(q, chat_id, context)
            elif key in ["caps_filter", "block_media"]: await group_media_menu(q, chat_id, context)
            elif key == "ai_enabled": await group_ai_menu(q, chat_id, context)
        return

    if d.startswith("tog_rev_"):
        parts = d.split("_")
        plat = parts[2]
        chat_id = int(parts[3])
        settings = get_group_settings(chat_id)
        rev = settings.setdefault("link_review", {})
        rev[plat] = not rev.get(plat, False)
        update_group_setting(chat_id, "link_review", rev)
        await group_link_review_menu(q, chat_id, context)
        return

    # -- Изменение параметров антиспама --
    if d.startswith("limit_inc_"):
        chat_id = int(d.split("_")[2])
        await change_flood_parameter(update, context, "flood_limit", 1, chat_id)
        await group_anti_spam_menu(q, chat_id, context)
        return
    if d.startswith("limit_dec_"):
        chat_id = int(d.split("_")[2])
        await change_flood_parameter(update, context, "flood_limit", -1, chat_id)
        await group_anti_spam_menu(q, chat_id, context)
        return
    if d.startswith("window_inc_"):
        chat_id = int(d.split("_")[2])
        await change_flood_parameter(update, context, "flood_window", 5, chat_id)
        await group_anti_spam_menu(q, chat_id, context)
        return
    if d.startswith("window_dec_"):
        chat_id = int(d.split("_")[2])
        await change_flood_parameter(update, context, "flood_window", -5, chat_id)
        await group_anti_spam_menu(q, chat_id, context)
        return
    if d.startswith("mute_inc_"):
        chat_id = int(d.split("_")[2])
        await change_flood_parameter(update, context, "flood_mute", 30, chat_id)
        await group_anti_spam_menu(q, chat_id, context)
        return
    if d.startswith("mute_dec_"):
        chat_id = int(d.split("_")[2])
        await change_flood_parameter(update, context, "flood_mute", -30, chat_id)
        await group_anti_spam_menu(q, chat_id, context)
        return

    # -- Строгий антиспам параметры --
    if d.startswith("s_limit_inc_"):
        chat_id = int(d.split("_")[3])
        await change_strict_parameter(update, context, "strict_flood_limit", 1, chat_id)
        await group_strict_anti_spam_menu(q, chat_id, context)
        return
    if d.startswith("s_limit_dec_"):
        chat_id = int(d.split("_")[3])
        await change_strict_parameter(update, context, "strict_flood_limit", -1, chat_id)
        await group_strict_anti_spam_menu(q, chat_id, context)
        return
    if d.startswith("s_window_inc_"):
        chat_id = int(d.split("_")[3])
        await change_strict_parameter(update, context, "strict_flood_window", 5, chat_id)
        await group_strict_anti_spam_menu(q, chat_id, context)
        return
    if d.startswith("s_window_dec_"):
        chat_id = int(d.split("_")[3])
        await change_strict_parameter(update, context, "strict_flood_window", -5, chat_id)
        await group_strict_anti_spam_menu(q, chat_id, context)
        return
    if d.startswith("s_mute_inc_"):
        chat_id = int(d.split("_")[3])
        await change_strict_parameter(update, context, "strict_flood_mute", 60, chat_id)
        await group_strict_anti_spam_menu(q, chat_id, context)
        return
    if d.startswith("s_mute_dec_"):
        chat_id = int(d.split("_")[3])
        await change_strict_parameter(update, context, "strict_flood_mute", -60, chat_id)
        await group_strict_anti_spam_menu(q, chat_id, context)
        return

    # -- Капс порог --
    if d.startswith("set_caps_"):
        threshold = int(d.split("_")[2])
        chat_id = int(d.split("_")[3])
        update_group_setting(chat_id, "caps_threshold", threshold)
        await group_media_menu(q, chat_id, context)
        return

    # -- ИИ и Приветствие (запрос текста) --
    if d.startswith("set_ai_prompt_"):
        chat_id = d.split("_")[3]
        user_states[uid] = f"await_ai_prompt_{chat_id}"
        await q.message.reply_text("✏️ Отправьте новую инструкцию для ИИ-модератора (до 500 символов):")
        await q.answer()
        return

    if d.startswith("group_set_welcome_"):
        chat_id = d.split("_")[3]
        user_states[uid] = f"welcome_chat_{chat_id}"
        await q.message.reply_text("✏️ Введите текст приветствия (или отправьте пустое сообщение для отключения).\nВы можете использовать `{name}` для подстановки имени пользователя:")
        await q.answer()
        return

    await q.answer("Действие не распознано", show_alert=False)

# ---------- ЗАПУСК ----------
def main():
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO
    )
    load_data()

    application = Application.builder().token(TOKEN).build()

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, handle_message))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_chat_members))

    application.add_handler(CommandHandler(["start", "menu"], start))
    application.add_handler(CommandHandler("addgroup", addgroup))
    application.add_handler(CommandHandler("group_menu", group_menu))
    application.add_handler(CommandHandler("broadcast", cmd_broadcast))

    # Команды модерации
    application.add_handler(CommandHandler("mute", cmd_mute))
    application.add_handler(CommandHandler("unmute", cmd_unmute))
    application.add_handler(CommandHandler("ban", cmd_ban))
    application.add_handler(CommandHandler("unban", cmd_unban))
    application.add_handler(CommandHandler("warn", cmd_warn))
    application.add_handler(CommandHandler("warns", cmd_warns))

    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logging.info("✅ Бот запущен и готов к работе!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
