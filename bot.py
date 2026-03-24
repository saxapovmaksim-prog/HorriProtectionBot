import asyncio
import json
import logging
import os
import re
import requests
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

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
        "strict_flood": False,
        "extended_stats": False,
        "ai_moderation": False
    },
    "standard": {
        "block_links": True,
        "block_media": True,
        "custom_welcome": True,
        "check_files": True,
        "check_content": False,
        "caps_filter": True,
        "invite_links_block": True,
        "strict_flood": True,
        "extended_stats": True,
        "ai_moderation": False
    },
    "pro": {
        "block_links": True,
        "block_media": True,
        "custom_welcome": True,
        "check_files": True,
        "check_content": True,
        "caps_filter": True,
        "invite_links_block": True,
        "strict_flood": True,
        "extended_stats": True,
        "ai_moderation": True
    }
}

TARIFF_DESCRIPTIONS = {
    "free": (
        "🆓 *Бесплатный* – 0 руб.\n"
        "• Антиспам\n"
        "• Блокировка ссылок\n"
        "• Блокировка инвайт-ссылок"
    ),
    "standard": (
        "⭐ *Стандартный* – 99 руб.\n"
        "• Блокировка медиа\n"
        "• Кастомное приветствие\n"
        "• Фильтр CAPS (настраиваемый порог)\n"
        "• Отправка файлов на проверку\n"
        "• Улучшенный антифлуд (9/3)\n"
        "• Расширенная статистика"
    ),
    "pro": (
        "💎 *Профессиональный* – 199 руб.\n"
        "• Проверка контента (AI)\n"
        "• Проверка аватарок (NSFW)\n"
        "• Все функции стандартного тарифа"
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
    "stats": {"messages": 0, "violations": 0},
    "violations_history": []  # [{user_id, reason, timestamp, duration}]
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
                g["settings"].setdefault("stats", {"messages": 0, "violations": 0})
                g["settings"].setdefault("violations_history", [])
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
    if cid not in data["groups"]:
        return None
    return data["groups"][cid]

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
    if not g:
        return None
    return g["settings"]

def update_group_setting(chat_id: int, key: str, value):
    g = get_group_data(chat_id)
    if g:
        g["settings"][key] = value
        save_data()

def add_violation(chat_id: int, user_id: int, reason: str, duration: int):
    g = get_group_data(chat_id)
    if g:
        g["settings"]["violations_history"].append({
            "user_id": user_id,
            "reason": reason,
            "timestamp": datetime.now().isoformat(),
            "duration": duration
        })
        # Храним только последние 100 записей
        if len(g["settings"]["violations_history"]) > 100:
            g["settings"]["violations_history"] = g["settings"]["violations_history"][-100:]
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

def is_flooding(user_id: int, chat_id: int, limit: int, window: int) -> bool:
    now = datetime.now()
    timestamps = user_messages[user_id]
    cutoff = now - timedelta(seconds=window)
    timestamps = [ts for ts in timestamps if ts > cutoff]
    user_messages[user_id] = timestamps
    timestamps.append(now)
    return len(timestamps) > limit

def get_user_flood_count(user_id: int, chat_id: int, window: int) -> int:
    now = datetime.now()
    cutoff = now - timedelta(seconds=window)
    return sum(1 for ts in user_messages[user_id] if ts > cutoff)

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
            update_group_setting(chat_id, "stats", settings["stats"])
            add_violation(chat_id, user_id, reason, duration)
        await context.bot.send_message(
            chat_id,
            f"🚫 Пользователь {user_id} получил ограничение на {duration} сек.\nПричина: {reason}"
        )
    except Exception as e:
        logging.error(f"Не удалось ограничить пользователя {user_id} в чате {chat_id}: {e}")

# ---------- AI-МОДЕРАЦИЯ (ЗАГЛУШКИ) ----------
async def check_ai_content(text: str, user_id: int, context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> Tuple[bool, str]:
    """
    Заглушка для AI-модерации.
    Здесь можно подключить:
    - Google Perspective API для токсичности
    - NudeNet для NSFW
    - OpenAI GPT для анализа текста
    Возвращает (нужно_ли_наказать, причина)
    """
    # Пример: проверка на мат (можно расширить)
    profanity_list = ["мат", "хуй", "пизда", "бля", "сука", "нахуй"]
    text_lower = text.lower()
    for word in profanity_list:
        if word in text_lower:
            return True, f"Запрещённое слово: {word}"
    return False, ""

async def check_avatar(user_id: int, context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> Tuple[bool, str]:
    """
    Заглушка для проверки аватарки.
    Здесь можно:
    - Получить фото через context.bot.get_user_profile_photos(user_id)
    - Отправить на NSFW-детектор
    """
    try:
        photos = await context.bot.get_user_profile_photos(user_id, limit=1)
        if photos.total_count > 0:
            # Получаем file_id, скачиваем, проверяем
            # Здесь заглушка: всегда возвращаем False
            pass
    except:
        pass
    return False, ""

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

        # Статистика сообщений
        settings["stats"]["messages"] += 1
        update_group_setting(chat.id, "stats", settings["stats"])

        # Пропускаем администраторов
        if await is_group_admin(chat.id, user.id, context):
            return

        # Проверка прав бота
        try:
            bot_member = await chat.get_member(context.bot.id)
            if bot_member.status != "administrator" or not bot_member.can_restrict_members:
                return
        except:
            return

        # Получаем тариф владельца группы
        g = get_group_data(chat.id)
        owner_tariff = get_user_tariff(g["owner"]) if g else "free"
        features = TARIFF_FEATURES[owner_tariff]

        # 1. Базовый антифлуд
        if is_flooding(user.id, chat.id, settings["flood_limit"], settings["flood_window"]):
            await restrict_user(chat.id, user.id, settings["flood_mute"], "Флуд (базовый)", context)
            try:
                await message.delete()
            except:
                pass
            return

        # 2. Строгий антифлуд (9/3) для платных тарифов
        if features.get("strict_flood", False):
            if is_flooding(user.id, chat.id, settings["strict_flood_limit"], settings["strict_flood_window"]):
                await restrict_user(chat.id, user.id, settings["strict_flood_mute"], "Жёсткий флуд (9/3)", context)
                try:
                    await message.delete()
                except:
                    pass
                return

        # 3. Блокировка ссылок
        if settings.get("block_links", True) and message.text and contains_link(message.text):
            await restrict_user(chat.id, user.id, 60, "Запрещённые ссылки", context)
            try:
                await message.delete()
            except:
                pass
            return

        # 4. Инвайт-ссылки
        if settings.get("invite_links_block", True) and message.text and contains_invite_link(message.text):
            await restrict_user(chat.id, user.id, 60, "Запрещённые инвайт-ссылки", context)
            try:
                await message.delete()
            except:
                pass
            return

        # 5. CAPS
        if settings.get("caps_filter", False) and message.text:
            threshold = settings.get("caps_threshold", 70)
            if is_caps_abuse(message.text, threshold):
                await restrict_user(chat.id, user.id, 1800, f"CAPS (> {threshold}%)", context)
                try:
                    await message.delete()
                except:
                    pass
                return

        # 6. Блокировка медиа
        if settings.get("block_media", False) and any((message.photo, message.video, message.document,
                                                       message.voice, message.audio, message.animation, message.sticker)):
            await restrict_user(chat.id, user.id, 60, "Медиафайлы запрещены", context)
            try:
                await message.delete()
            except:
                pass
            return

        # 7. Отправка файлов на проверку (для Standard и Pro)
        if settings.get("check_files", False) and message.document:
            await message.reply_text("📁 Файл отправлен на проверку")
            await message.delete()

        # 8. AI-модерация текста (только для PRO)
        if features.get("ai_moderation", False) and message.text:
            need_ban, reason = await check_ai_content(message.text, user.id, context, chat.id)
            if need_ban:
                await restrict_user(chat.id, user.id, 3600, f"AI: {reason}", context)
                try:
                    await message.delete()
                except:
                    pass
                return

        # 9. Проверка аватарки при первом сообщении (только для PRO)
        if features.get("ai_moderation", False):
            # Проверяем, проверяли ли уже аватарку этого пользователя
            if str(user.id) not in context.user_data.get("avatar_checked", set()):
                need_ban, reason = await check_avatar(user.id, context, chat.id)
                if need_ban:
                    await context.bot.ban_chat_member(chat.id, user.id)
                    await context.bot.send_message(chat.id, f"🚫 Пользователь {user.id} забанен за неподобающую аватарку.")
                    try:
                        await message.delete()
                    except:
                        pass
                    return
                context.user_data.setdefault("avatar_checked", set()).add(str(user.id))

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
                "После этого вы сможете настроить защиту через /menu в личных сообщениях.",
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
        await update.message.reply_text("⛔ Только администраторы группы могут добавить бота.")
        return

    try:
        bot_member = await chat.get_member(context.bot.id)
        if bot_member.status != "administrator":
            await update.message.reply_text(
                "❌ Бот не является администратором.\n"
                "Назначьте его администратором в настройках группы (нужны права на удаление сообщений и блокировку пользователей)."
            )
            return
        if not bot_member.can_restrict_members:
            await update.message.reply_text(
                "⚠️ Бот не имеет права «Блокировка пользователей».\n"
                "В настройках администратора группы включите для бота это право."
            )
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

# ---------- МЕНЮ ----------
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

# ---------- СПИСОК ГРУПП ----------
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
    for cid, g in data["groups"].items():
        try:
            chat = await context.bot.get_chat(int(cid))
            name = chat.title or f"Группа {cid}"
        except:
            name = f"Группа {cid}"
        keyboard.append([InlineKeyboardButton(name, callback_data=f"group_{cid}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="main_menu")])
    await query.edit_message_text("📋 *Список групп:*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def show_group_settings(query, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    user_id = query.from_user.id

    # Проверка, что бот является администратором группы
    try:
        bot_member = await context.bot.get_chat_member(chat_id, context.bot.id)
        if bot_member.status != "administrator":
            await query.answer("⚠️ Бот не является администратором группы. Назначьте его администратором и повторите попытку.", show_alert=True)
            return
    except Exception as e:
        logging.error(f"Ошибка проверки прав бота в группе {chat_id}: {e}")
        await query.answer("❌ Не удалось проверить права бота.", show_alert=True)
        return

    # Проверка, что пользователь администратор группы
    if not await is_group_admin(chat_id, user_id, context):
        await query.answer("⛔ Только администраторы группы могут настраивать бота.", show_alert=True)
        return

    g = get_group_data(chat_id)
    if not g:
        await query.answer("Группа не найдена.", show_alert=True)
        return

    owner_id = g["owner"]
    owner_tariff = get_user_tariff(owner_id)
    allowed_features = TARIFF_FEATURES[owner_tariff]
    settings = g["settings"]

    text = (
        f"*Группа:* `{chat_id}`\n"
        f"*Владелец:* `{owner_id}`\n"
        f"*Тариф владельца:* {owner_tariff.upper()}\n"
        f"*Антиспам:* {settings['flood_limit']} сообщ. за {settings['flood_window']} сек → мут {settings['flood_mute']} сек\n"
    )
    if allowed_features.get("strict_flood", False):
        text += f"*Жёсткий антиспам:* {settings['strict_flood_limit']} сообщ. за {settings['strict_flood_window']} сек → мут {settings['strict_flood_mute']} сек\n"
    text += (
        f"*CAPS порог:* {settings.get('caps_threshold', 70)}% (мут 30 мин)\n"
        f"*Блокировка ссылок:* {'✅' if settings['block_links'] else '❌'}\n"
        f"*Блокировка инвайт-ссылок:* {'✅' if settings['invite_links_block'] else '❌'}\n"
        f"*Фильтр CAPS:* {'✅' if settings['caps_filter'] else '❌'}\n"
        f"*Блокировка медиа:* {'✅' if settings['block_media'] else '❌'}\n"
        f"*Кастомное приветствие:* {'✅' if settings['custom_welcome'] else '❌'}\n"
        f"*Проверка файлов:* {'✅' if settings['check_files'] else '❌'}\n"
        f"*Статистика:* сообщений {settings['stats']['messages']}, нарушений {settings['stats']['violations']}"
    )
    keyboard = []

    keyboard.append([InlineKeyboardButton("⚙️ Антиспам", callback_data=f"anti_spam_{chat_id}")])

    if allowed_features["caps_filter"]:
        keyboard.append([InlineKeyboardButton("🔠 CAPS порог", callback_data=f"caps_threshold_{chat_id}")])
        keyboard.append([InlineKeyboardButton("🔠 CAPS: Вкл/Выкл", callback_data=f"toggle_caps_{chat_id}")])
    else:
        keyboard.append([InlineKeyboardButton("🔒 CAPS (требуется платный тариф владельца)", callback_data="noop")])

    keyboard.append([InlineKeyboardButton("🔗 Ссылки: Вкл/Выкл", callback_data=f"toggle_links_{chat_id}")])
    keyboard.append([InlineKeyboardButton("🚫 Инвайт-ссылки: Вкл/Выкл", callback_data=f"toggle_invite_{chat_id}")])

    if allowed_features["block_media"]:
        keyboard.append([InlineKeyboardButton("📷 Медиа: Вкл/Выкл", callback_data=f"toggle_media_{chat_id}")])
    else:
        keyboard.append([InlineKeyboardButton("🔒 Медиа (требуется платный тариф владельца)", callback_data="noop")])

    if allowed_features["custom_welcome"]:
        keyboard.append([InlineKeyboardButton("✏️ Кастомное приветствие", callback_data=f"set_welcome_{chat_id}")])
    else:
        keyboard.append([InlineKeyboardButton("🔒 Приветствие (требуется платный тариф владельца)", callback_data="noop")])

    if allowed_features["check_files"]:
        keyboard.append([InlineKeyboardButton("📁 Проверка файлов: Вкл/Выкл", callback_data=f"toggle_files_{chat_id}")])
    else:
        keyboard.append([InlineKeyboardButton("🔒 Проверка файлов (требуется платный тариф владельца)", callback_data="noop")])

    # Расширенная статистика для платных тарифов
    if allowed_features.get("extended_stats", False):
        keyboard.append([InlineKeyboardButton("📊 Расширенная статистика", callback_data=f"extended_stats_{chat_id}")])

    if owner_tariff == "free":
        text += "\n\n⚠️ *Владелец группы имеет бесплатный тариф.* Для расширения функций владелец должен приобрести платный тариф в разделе «💰 Тарифы»."
    else:
        expiry = user_data.get(str(owner_id), {}).get("expiry")
        if expiry:
            exp_date = datetime.fromisoformat(expiry).strftime("%d.%m.%Y")
            text += f"\n\n💎 *Тариф владельца активен до {exp_date}*"

    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="groups")])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

# ---------- НАСТРОЙКИ АНТИСПАМА ----------
async def anti_spam_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    query = update.callback_query
    settings = get_group_settings(chat_id)
    if not settings:
        await query.answer("Группа не найдена", show_alert=True)
        return
    g = get_group_data(chat_id)
    owner_tariff = get_user_tariff(g["owner"])
    has_strict = TARIFF_FEATURES[owner_tariff].get("strict_flood", False)

    text = (
        f"*Настройка антиспама*\n"
        f"Лимит: {settings['flood_limit']} сообщений\n"
        f"Окно: {settings['flood_window']} сек\n"
        f"Длительность мута: {settings['flood_mute']} сек\n"
    )
    if has_strict:
        text += (
            f"\n*Жёсткий антиспам (9/3)*\n"
            f"Лимит: {settings['strict_flood_limit']} сообщений\n"
            f"Окно: {settings['strict_flood_window']} сек\n"
            f"Длительность мута: {settings['strict_flood_mute']} сек\n"
        )
    text += "\nИспользуйте кнопки для изменения."

    keyboard = [
        [InlineKeyboardButton("📈 +1", callback_data=f"limit_inc_{chat_id}"),
         InlineKeyboardButton("📉 -1", callback_data=f"limit_dec_{chat_id}")],
        [InlineKeyboardButton("⏱ +5 сек", callback_data=f"window_inc_{chat_id}"),
         InlineKeyboardButton("⏱ -5 сек", callback_data=f"window_dec_{chat_id}")],
        [InlineKeyboardButton("🔇 +30 сек мута", callback_data=f"mute_inc_{chat_id}"),
         InlineKeyboardButton("🔊 -30 сек мута", callback_data=f"mute_dec_{chat_id}")],
    ]
    if has_strict:
        keyboard.append([InlineKeyboardButton("📈 +1 (жёсткий)", callback_data=f"strict_limit_inc_{chat_id}"),
                         InlineKeyboardButton("📉 -1 (жёсткий)", callback_data=f"strict_limit_dec_{chat_id}")])
        keyboard.append([InlineKeyboardButton("⏱ +5 сек (жёсткий)", callback_data=f"strict_window_inc_{chat_id}"),
                         InlineKeyboardButton("⏱ -5 сек (жёсткий)", callback_data=f"strict_window_dec_{chat_id}")])
        keyboard.append([InlineKeyboardButton("🔇 +30 сек мута (жёсткий)", callback_data=f"strict_mute_inc_{chat_id}"),
                         InlineKeyboardButton("🔊 -30 сек мута (жёсткий)", callback_data=f"strict_mute_dec_{chat_id}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=f"group_{chat_id}")])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def change_flood_parameter(update: Update, context: ContextTypes.DEFAULT_TYPE, param: str, delta: int, chat_id: int):
    query = update.callback_query
    user_id = query.from_user.id
    if not await is_group_admin(chat_id, user_id, context):
        await query.answer("⛔ Только администраторы группы могут настраивать бота.", show_alert=True)
        return
    settings = get_group_settings(chat_id)
    if not settings:
        return
    current = settings[param]
    new_val = max(1, current + delta)
    update_group_setting(chat_id, param, new_val)
    await query.answer(f"{param} изменён на {new_val}")
    await anti_spam_menu(update, context, chat_id)

# ---------- НАСТРОЙКИ CAPS ----------
async def caps_threshold_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    query = update.callback_query
    settings = get_group_settings(chat_id)
    if not settings:
        await query.answer("Группа не найдена", show_alert=True)
        return
    current = settings.get("caps_threshold", 70)
    text = f"*Настройка порога CAPS*\nТекущий порог: {current}%\n\nВыберите новый порог:"
    thresholds = [10, 30, 50, 70, 100]
    keyboard = []
    for t in thresholds:
        keyboard.append([InlineKeyboardButton(f"{t}%", callback_data=f"select_caps_{t}_{chat_id}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=f"group_{chat_id}")])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def set_caps_threshold(update: Update, context: ContextTypes.DEFAULT_TYPE, threshold: int, chat_id: int):
    query = update.callback_query
    user_id = query.from_user.id
    if not await is_group_admin(chat_id, user_id, context):
        await query.answer("⛔ Только администраторы группы могут настраивать бота.", show_alert=True)
        return
    g = get_group_data(chat_id)
    if not g:
        await query.answer("Группа не найдена", show_alert=True)
        return
    owner_tariff = get_user_tariff(g["owner"])
    if not TARIFF_FEATURES[owner_tariff]["caps_filter"]:
        await query.answer("❌ Функция CAPS недоступна на тарифе владельца группы.", show_alert=True)
        return
    update_group_setting(chat_id, "caps_threshold", threshold)
    await query.answer(f"Порог CAPS установлен на {threshold}%")
    await show_group_settings(query, chat_id, context)

# ---------- ПЕРЕКЛЮЧЕНИЯ ----------
async def toggle_setting(update: Update, context: ContextTypes.DEFAULT_TYPE, setting: str, chat_id: int):
    query = update.callback_query
    user_id = query.from_user.id
    if not await is_group_admin(chat_id, user_id, context):
        await query.answer("⛔ Только администраторы группы могут настраивать бота.", show_alert=True)
        return
    g = get_group_data(chat_id)
    if not g:
        await query.answer("Группа не найдена", show_alert=True)
        return
    owner_tariff = get_user_tariff(g["owner"])
    if not TARIFF_FEATURES[owner_tariff].get(setting, False):
        await query.answer("❌ Эта функция недоступна на тарифе владельца группы.", show_alert=True)
        return
    settings = get_group_settings(chat_id)
    if not settings:
        return
    new_val = not settings[setting]
    update_group_setting(chat_id, setting, new_val)
    await query.answer(f"{setting.replace('_',' ').title()} {'включена' if new_val else 'выключена'}")
    await show_group_settings(query, chat_id, context)

# ---------- РАСШИРЕННАЯ СТАТИСТИКА ----------
async def extended_stats(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    query = update.callback_query
    user_id = query.from_user.id
    if not await is_group_admin(chat_id, user_id, context):
        await query.answer("⛔ Только администраторы группы могут просматривать статистику.", show_alert=True)
        return
    g = get_group_data(chat_id)
    if not g:
        await query.answer("Группа не найдена", show_alert=True)
        return
    owner_tariff = get_user_tariff(g["owner"])
    if not TARIFF_FEATURES[owner_tariff].get("extended_stats", False):
        await query.answer("❌ Расширенная статистика доступна только на платных тарифах.", show_alert=True)
        return

    settings = g["settings"]
    stats = settings["stats"]
    history = settings.get("violations_history", [])

    # Статистика за всё время
    total_messages = stats["messages"]
    total_violations = stats["violations"]

    # Статистика за последние сутки, неделю, месяц
    now = datetime.now()
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    messages_day = sum(1 for ts_str in settings.get("messages_timestamps", []) if datetime.fromisoformat(ts_str) > day_ago)
    violations_day = sum(1 for v in history if datetime.fromisoformat(v["timestamp"]) > day_ago)
    messages_week = sum(1 for ts_str in settings.get("messages_timestamps", []) if datetime.fromisoformat(ts_str) > week_ago)
    violations_week = sum(1 for v in history if datetime.fromisoformat(v["timestamp"]) > week_ago)
    messages_month = sum(1 for ts_str in settings.get("messages_timestamps", []) if datetime.fromisoformat(ts_str) > month_ago)
    violations_month = sum(1 for v in history if datetime.fromisoformat(v["timestamp"]) > month_ago)

    # Топ нарушителей
    violator_counts = defaultdict(int)
    for v in history:
        violator_counts[v["user_id"]] += 1
    top_violators = sorted(violator_counts.items(), key=lambda x: x[1], reverse=True)[:5]

    text = (
        f"📊 *Расширенная статистика группы*\n\n"
        f"*За всё время:*\n"
        f"Сообщений: {total_messages}\n"
        f"Нарушений: {total_violations}\n\n"
        f"*За последние 24 часа:*\n"
        f"Сообщений: {messages_day}\n"
        f"Нарушений: {violations_day}\n\n"
        f"*За последние 7 дней:*\n"
        f"Сообщений: {messages_week}\n"
        f"Нарушений: {violations_week}\n\n"
        f"*За последние 30 дней:*\n"
        f"Сообщений: {messages_month}\n"
        f"Нарушений: {violations_month}\n\n"
        f"*Топ-5 нарушителей:*\n"
    )
    if top_violators:
        for i, (uid, count) in enumerate(top_violators, 1):
            text += f"{i}. `{uid}` — {count} наруш.\n"
    else:
        text += "Нет нарушений.\n"

    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data=f"group_{chat_id}")]]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

# ---------- ПРОФИЛЬ ----------
async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    user = register_user(user_id)
    tariff = get_user_tariff(user_id)
    reg_date = datetime.fromisoformat(user["registered"]).strftime("%d.%m.%Y %H:%M")
    text = (
        f"👤 *Ваш профиль*\n"
        f"🆔 ID: `{user_id}`\n"
        f"📅 Дата регистрации: {reg_date}\n"
        f"💎 Тариф: {tariff.upper()}\n"
    )
    if tariff != "free" and user.get("expiry"):
        exp_date = datetime.fromisoformat(user["expiry"]).strftime("%d.%m.%Y")
        text += f"⏰ Действует до: {exp_date}\n"

    if user_id == ADMIN_ID:
        text += "\n👑 *Вы являетесь главным администратором бота.*"

    admin_groups = []
    for cid, g in data["groups"].items():
        try:
            chat = await context.bot.get_chat(int(cid))
            member = await context.bot.get_chat_member(int(cid), user_id)
            if member.status in ("administrator", "creator"):
                admin_groups.append(chat.title or f"Группа {cid}")
        except:
            pass
    if admin_groups:
        text += "\n\n*Вы администратор групп:*\n" + "\n".join(f"• {name}" for name in admin_groups[:10])
    else:
        text += "\n\n*Вы не являетесь администратором ни одной из добавленных групп.*"

    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

# ---------- ТАРИФЫ ----------
async def show_tariffs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    text = "*Доступные тарифы:*\n\n"
    for tariff in ["free", "standard", "pro"]:
        text += TARIFF_DESCRIPTIONS[tariff] + "\n\n"
    text += "⏰ *Все платные тарифы действуют 1 месяц.*\n\nДля покупки выберите тариф ниже."
    keyboard = [
        [InlineKeyboardButton("🆓 Бесплатный", callback_data="tariff_info_free")],
        [InlineKeyboardButton("⭐ Стандартный (99 руб)", callback_data="tariff_info_standard")],
        [InlineKeyboardButton("💎 Профессиональный (199 руб)", callback_data="tariff_info_pro")],
        [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def show_tariff_info(update: Update, context: ContextTypes.DEFAULT_TYPE, tariff: str):
    query = update.callback_query
    if tariff == "free":
        await query.edit_message_text(
            TARIFF_DESCRIPTIONS["free"] + "\n\n✅ Этот тариф уже активен по умолчанию.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="show_tariffs")]])
        )
        return
    price_rub = PRICES_RUB[tariff]
    price_usd = PRICES_USD[tariff]
    text = (
        TARIFF_DESCRIPTIONS[tariff] + f"\n\nСтоимость: {price_rub} руб. (≈{price_usd} USD)\n"
        "После оплаты тариф будет активирован на 30 дней.\n\n"
        "Выберите действие:"
    )
    keyboard = [
        [InlineKeyboardButton("💳 Купить", callback_data=f"buy_{tariff}")],
        [InlineKeyboardButton("🔙 Назад", callback_data="show_tariffs")]
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def buy_tariff(update: Update, context: ContextTypes.DEFAULT_TYPE, tariff: str):
    query = update.callback_query
    user_id = query.from_user.id
    price_usd = PRICES_USD[tariff]
    description = f"Активация тарифа {tariff.upper()} на 30 дней"
    invoice = create_crypto_invoice(price_usd, description)
    if not invoice:
        await query.edit_message_text(
            "❌ Ошибка создания счёта. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="show_tariffs")]])
        )
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
        f"Стоимость: {PRICES_RUB[tariff]} руб. (≈{price_usd} USD)\n"
        f"Тариф будет активирован на 30 дней.\n\n"
        f"После оплаты нажмите «Проверить оплату»."
    )
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def check_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, invoice_id: str):
    query = update.callback_query
    await query.answer()
    if invoice_id not in pending_payments:
        await query.edit_message_text(
            "❌ Запрос на оплату не найден или устарел.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="show_tariffs")]])
        )
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
            f"Теперь вы можете использовать расширенные функции в ваших группах (если вы владелец группы).",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 В меню", callback_data="main_menu")]])
        )
    else:
        await query.edit_message_text(
            "⏳ Оплата не обнаружена. Убедитесь, что вы завершили платёж, и нажмите снова.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="show_tariffs")]])
        )

def create_crypto_invoice(amount_usd: float, description: str) -> Optional[Dict]:
    url = "https://pay.crypt.bot/api/createInvoice"
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN, "Content-Type": "application/json"}
    payload = {
        "asset": "USDT",
        "amount": amount_usd,
        "description": description,
        "paid_btn_name": "callback",
        "paid_btn_url": "https://t.me/YourBotUsername"
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
    for cid, g in data["groups"].items():
        try:
            chat = await context.bot.get_chat(int(cid))
            name = chat.title or f"Группа {cid}"
        except:
            name = f"Группа {cid}"
        owner_tariff = get_user_tariff(g["owner"])
        text += f"• {name} (`{cid}`) – владелец `{g['owner']}` ({owner_tariff.upper()})\n"
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]]))

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
    await query.edit_message_text("Админ-панель ожидает ввод ID...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]]))

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

# ---------- ОБРАБОТЧИК ТЕКСТА ----------
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user_id = update.effective_user.id
    chat = update.effective_chat

    if user_id == ADMIN_ID and user_states.get(user_id) == "await_group_id":
        try:
            chat_id = int(message.text.strip())
            await show_group_info(chat_id, message, context)
        except Exception as e:
            await message.reply_text(f"❌ Ошибка: {e}")
        del user_states[user_id]
        return

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
        await show_group_settings_from_user(message, chat_id, context)
        return

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
    user_id = message.from_user.id
    g = get_group_data(chat_id)
    if not g:
        return
    owner_id = g["owner"]
    owner_tariff = get_user_tariff(owner_id)
    allowed_features = TARIFF_FEATURES[owner_tariff]
    settings = g["settings"]

    text = (
        f"*Группа:* `{chat_id}`\n"
        f"*Владелец:* `{owner_id}`\n"
        f"*Тариф владельца:* {owner_tariff.upper()}\n"
        f"*Антиспам:* {settings['flood_limit']} сообщ. за {settings['flood_window']} сек → мут {settings['flood_mute']} сек\n"
    )
    if allowed_features.get("strict_flood", False):
        text += f"*Жёсткий антиспам:* {settings['strict_flood_limit']} сообщ. за {settings['strict_flood_window']} сек → мут {settings['strict_flood_mute']} сек\n"
    text += (
        f"*CAPS порог:* {settings.get('caps_threshold', 70)}%\n"
        f"*Блокировка ссылок:* {'✅' if settings['block_links'] else '❌'}\n"
        f"*Блокировка инвайт-ссылок:* {'✅' if settings['invite_links_block'] else '❌'}\n"
        f"*Фильтр CAPS:* {'✅' if settings['caps_filter'] else '❌'}\n"
        f"*Блокировка медиа:* {'✅' if settings['block_media'] else '❌'}\n"
        f"*Кастомное приветствие:* {'✅' if settings['custom_welcome'] else '❌'}\n"
        f"*Проверка файлов:* {'✅' if settings['check_files'] else '❌'}\n"
        f"*Статистика:* сообщений {settings['stats']['messages']}, нарушений {settings['stats']['violations']}"
    )
    keyboard = []
    keyboard.append([InlineKeyboardButton("⚙️ Антиспам", callback_data=f"anti_spam_{chat_id}")])

    if allowed_features["caps_filter"]:
        keyboard.append([InlineKeyboardButton("🔠 CAPS порог", callback_data=f"caps_threshold_{chat_id}")])
        keyboard.append([InlineKeyboardButton("🔠 CAPS: Вкл/Выкл", callback_data=f"toggle_caps_{chat_id}")])
    else:
        keyboard.append([InlineKeyboardButton("🔒 CAPS (требуется платный тариф владельца)", callback_data="noop")])

    keyboard.append([InlineKeyboardButton("🔗 Ссылки: Вкл/Выкл", callback_data=f"toggle_links_{chat_id}")])
    keyboard.append([InlineKeyboardButton("🚫 Инвайт-ссылки: Вкл/Выкл", callback_data=f"toggle_invite_{chat_id}")])

    if allowed_features["block_media"]:
        keyboard.append([InlineKeyboardButton("📷 Медиа: Вкл/Выкл", callback_data=f"toggle_media_{chat_id}")])
    else:
        keyboard.append([InlineKeyboardButton("🔒 Медиа (требуется платный тариф владельца)", callback_data="noop")])

    if allowed_features["custom_welcome"]:
        keyboard.append([InlineKeyboardButton("✏️ Кастомное приветствие", callback_data=f"set_welcome_{chat_id}")])
    else:
        keyboard.append([InlineKeyboardButton("🔒 Приветствие (требуется платный тариф владельца)", callback_data="noop")])

    if allowed_features["check_files"]:
        keyboard.append([InlineKeyboardButton("📁 Проверка файлов: Вкл/Выкл", callback_data=f"toggle_files_{chat_id}")])
    else:
        keyboard.append([InlineKeyboardButton("🔒 Проверка файлов (требуется платный тариф владельца)", callback_data="noop")])

    if allowed_features.get("extended_stats", False):
        keyboard.append([InlineKeyboardButton("📊 Расширенная статистика", callback_data=f"extended_stats_{chat_id}")])

    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="groups")])
    await message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

# ---------- ОСНОВНОЙ CALLBACK ОБРАБОТЧИК ----------
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data_cb = query.data

    if data_cb == "main_menu":
        await show_main_menu(update, context, edit_message=True, chat_id=query.message.chat_id, message_id=query.message.message_id)
        return

    if data_cb == "groups":
        await show_groups(update, context)
        return
    if data_cb == "show_tariffs":
        await show_tariffs(update, context)
        return
    if data_cb == "profile":
        await show_profile(update, context)
        return
    if data_cb == "noop":
        await query.answer()
        return

    # Тарифы
    if data_cb.startswith("tariff_info_"):
        tariff = data_cb.split("_")[2]
        await show_tariff_info(update, context, tariff)
        return
    if data_cb.startswith("buy_"):
        tariff = data_cb.split("_")[1]
        await buy_tariff(update, context, tariff)
        return

    # Админ-панель
    if data_cb == "admin_panel":
        await admin_panel(update, context)
        return
    if data_cb == "admin_stats":
        await admin_stats(update, context)
        return
    if data_cb == "admin_group_info":
        await admin_group_info_request(update, context)
        return

    # Группы
    if data_cb.startswith("group_"):
        chat_id = int(data_cb.split("_")[1])
        await show_group_settings(query, chat_id, context)
        return

    # Антиспам
    if data_cb.startswith("anti_spam_"):
        chat_id = int(data_cb.split("_")[2])
        await anti_spam_menu(update, context, chat_id)
        return
    # Изменения базовых параметров антиспама
    if data_cb.startswith("limit_inc_"):
        chat_id = int(data_cb.split("_")[2])
        await change_flood_parameter(update, context, "flood_limit", 1, chat_id)
        return
    if data_cb.startswith("limit_dec_"):
        chat_id = int(data_cb.split("_")[2])
        await change_flood_parameter(update, context, "flood_limit", -1, chat_id)
        return
    if data_cb.startswith("window_inc_"):
        chat_id = int(data_cb.split("_")[2])
        await change_flood_parameter(update, context, "flood_window", 5, chat_id)
        return
    if data_cb.startswith("window_dec_"):
        chat_id = int(data_cb.split("_")[2])
        await change_flood_parameter(update, context, "flood_window", -5, chat_id)
        return
    if data_cb.startswith("mute_inc_"):
        chat_id = int(data_cb.split("_")[2])
        await change_flood_parameter(update, context, "flood_mute", 30, chat_id)
        return
    if data_cb.startswith("mute_dec_"):
        chat_id = int(data_cb.split("_")[2])
        await change_flood_parameter(update, context, "flood_mute", -30, chat_id)
        return
    # Изменения строгих параметров антиспама
    if data_cb.startswith("strict_limit_inc_"):
        chat_id = int(data_cb.split("_")[3])
        await change_flood_parameter(update, context, "strict_flood_limit", 1, chat_id)
        return
    if data_cb.startswith("strict_limit_dec_"):
        chat_id = int(data_cb.split("_")[3])
        await change_flood_parameter(update, context, "strict_flood_limit", -1, chat_id)
        return
    if data_cb.startswith("strict_window_inc_"):
        chat_id = int(data_cb.split("_")[3])
        await change_flood_parameter(update, context, "strict_flood_window", 5, chat_id)
        return
    if data_cb.startswith("strict_window_dec_"):
        chat_id = int(data_cb.split("_")[3])
        await change_flood_parameter(update, context, "strict_flood_window", -5, chat_id)
        return
    if data_cb.startswith("strict_mute_inc_"):
        chat_id = int(data_cb.split("_")[3])
        await change_flood_parameter(update, context, "strict_flood_mute", 30, chat_id)
        return
    if data_cb.startswith("strict_mute_dec_"):
        chat_id = int(data_cb.split("_")[3])
        await change_flood_parameter(update, context, "strict_flood_mute", -30, chat_id)
        return

    # CAPS
    if data_cb.startswith("caps_threshold_"):
        chat_id = int(data_cb.split("_")[2])
        await caps_threshold_menu(update, context, chat_id)
        return
    if data_cb.startswith("select_caps_"):
        parts = data_cb.split("_")
        threshold = int(parts[2])
        chat_id = int(parts[3])
        await set_caps_threshold(update, context, threshold, chat_id)
        return

    # Переключатели
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

    # Приветствие
    if data_cb.startswith("set_welcome_"):
        chat_id = int(data_cb.split("_")[2])
        context.user_data["welcome_chat"] = chat_id
        await query.edit_message_text("Введите текст приветствия (или отправьте пустое сообщение для отключения):")
        return

    # Расширенная статистика
    if data_cb.startswith("extended_stats_"):
        chat_id = int(data_cb.split("_")[2])
        await extended_stats(update, context, chat_id)
        return

    # Оплата
    if data_cb.startswith("check_payment_"):
        invoice_id = data_cb.split("_")[2]
        await check_payment(update, context, invoice_id)
        return

    await query.answer("Действие не распознано", show_alert=False)

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

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", start))
    application.add_handler(CommandHandler("addgroup", addgroup))

    application.add_handler(CallbackQueryHandler(button_callback, pattern="^(main_menu|groups|show_tariffs|profile|tariff_info_|buy_|admin_panel|admin_stats|admin_group_info|group_|anti_spam_|limit_inc_|limit_dec_|window_inc_|window_dec_|mute_inc_|mute_dec_|strict_limit_inc_|strict_limit_dec_|strict_window_inc_|strict_window_dec_|strict_mute_inc_|strict_mute_dec_|caps_threshold_|select_caps_|toggle_links_|toggle_invite_|toggle_caps_|toggle_media_|toggle_files_|set_welcome_|extended_stats_|check_payment_|noop)"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logging.info("✅ Бот запущен")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
