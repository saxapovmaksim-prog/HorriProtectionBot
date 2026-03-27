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
TOKEN = "8637803848:AAHTK2zzOOtSUV2tsWJLckGYuNWV6tRCJRE"
ADMIN_ID = 2032012311
DATA_FILE = "bot_data.json"
USER_DATA_FILE = "user_data.json"
YOOMONEY_TOKEN = "4100119421936909.5708C060A9413FE2D03525B0F3C2FFD2780FF9A7B979527712BB947C16BEE28B2728CF9A6B66BC8FF64D030553F5C8BF8310097F3919ED9EF1B53F022E427E95DFC03B407B1F6A7EC8F778E0864DAA392E7D0F9C5DD43C7B1A4EB78EC63D61A8FA21ED6ECA5689326A9FD99951C97F10D998D5F2AA6099DC16E2B87142300ACC"
YOOMONEY_WALLET = "4100119421936909"
# --- НАСТРОЙКИ YOOMONEY ---
YOOMONEY_TOKEN = "ТУТ_БУДЕТ_ТОКЕН_ЮМОНИ"
YOOMONEY_WALLET = "ТУТ_НОМЕР_ТВОЕГО_КОШЕЛЬКА" # Например: 410011234567890

PRICES_RUB = {"standard": 99, "pro": 199, "vip": 50}

TARIFF_FEATURES = {
    "free": {"block_links": True, "block_media": False, "custom_welcome": False, "check_files": False, "check_content": False, "caps_filter": False, "invite_links_block": True, "strict_flood": False, "captcha": False},
    "standard": {"block_links": True, "block_media": True, "custom_welcome": True, "check_files": True, "check_content": False, "caps_filter": True, "invite_links_block": True, "strict_flood": True, "captcha": True},
    "pro": {"block_links": True, "block_media": True, "custom_welcome": True, "check_files": True, "check_content": True, "caps_filter": True, "invite_links_block": True, "strict_flood": True, "captcha": True}
}

TARIFF_DESCRIPTIONS = {
    "free": ("🛡 *Базовый (Free)* — 0 руб.\n\nОтличный старт. Включает:\n🔹 Антиспам и антифлуд\n🔹 Удаление ссылок и инвайтов"),
    "vip": ("🌟 *VIP-статус пользователя* — 50 руб./мес\n\nДает персональные привилегии во всех чатах бота:\n🔸 Полный обход капчи при входе (сразу в чат)\n🔸 Увеличенные лимиты на сообщения (х2)\n🔸 Отличительная отметка в профиле"),
    "standard": ("⭐ *Стандартный (Standard)* — 99 руб./мес\n\nПродвинутый контроль:\n🔸 *Всё из Базового тарифа*\n🔸 Капча при входе\n🔸 Запрет на медиа и автоответчик (триггеры)\n🔸 Кастомное приветствие и фильтр CAPS"),
    "pro": ("💎 *Профессиональный (PRO)* — 199 руб./мес\n\nМаксимальная защита:\n🚀 *Всё из Стандартного*\n🚀 Проверка ссылок соцсетей админами\n🚀 AI-модерация контента")
}

DEFAULT_SETTINGS = {
    "flood_limit": 5, "flood_window": 10, "flood_mute": 60,
    "strict_flood_limit": 9, "strict_flood_window": 3, "strict_flood_mute": 600,
    "block_links": True, "block_media": False, "custom_welcome": None,
    "check_files": False, "check_content": False, "caps_filter": False, "caps_threshold": 70,
    "invite_links_block": True, "captcha_enabled": False,
    "link_review": {"tg": False, "yt": False, "tt": False, "ig": False, "vk": False},
    "whitelisted_links": {}, "triggers": {}, "seen_users": [],
    "ai_enabled": False, "ai_prompt": "Ты модератор чата. Анализируй сообщения на токсичность и спам.", "ai_strictness": 50,
    "stats": {"messages": 0, "violations": 0, "history": []}, "warnings": {}
}

# ---------- ГЛОБАЛЬНЫЕ ДАННЫЕ ----------
data: Dict = {"groups": {}}
user_data: Dict = {}
user_messages: Dict[int, List[datetime]] = defaultdict(list)
pending_payments: Dict[str, dict] = {}
user_states: Dict[int, str] = {}
pending_reviews: Dict[str, dict] = {}
pending_captchas: Dict[str, Dict[int, int]] = defaultdict(dict)

# ---------- ЗАГРУЗКА / СОХРАНЕНИЕ ----------
def load_data():
    global data, user_data
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f: data = json.load(f)
            for cid in list(data.get("groups", {}).keys()):
                g = data["groups"][cid]
                g.setdefault("owner", None)
                g.setdefault("settings", DEFAULT_SETTINGS.copy())
                for key, val in DEFAULT_SETTINGS.items(): g["settings"].setdefault(key, val)
        except Exception as e: logging.error(f"Ошибка загрузки групп: {e}"); data = {"groups": {}}
    else: data = {"groups": {}}

    if os.path.exists(USER_DATA_FILE):
        try:
            with open(USER_DATA_FILE, "r", encoding="utf-8") as f: user_data = json.load(f)
            for uid in user_data:
                user_data[uid].setdefault("is_vip", False)
                user_data[uid].setdefault("vip_expiry", None)
        except Exception as e: logging.error(f"Ошибка загрузки юзеров: {e}"); user_data = {}
    else: user_data = {}

def save_data():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f: json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception: pass

def save_user_data():
    try:
        with open(USER_DATA_FILE, "w", encoding="utf-8") as f: json.dump(user_data, f, indent=2, ensure_ascii=False)
    except Exception: pass

def get_group_data(chat_id: int) -> Dict: return data["groups"].get(str(chat_id))
def get_group_settings(chat_id: int) -> Dict:
    g = get_group_data(chat_id)
    return g["settings"] if g else None
def update_group_setting(chat_id: int, key: str, value):
    g = get_group_data(chat_id)
    if g: g["settings"][key] = value; save_data()

def register_user(user_id: int) -> Dict:
    uid = str(user_id)
    if uid not in user_data:
        user_data[uid] = {
            "registered": datetime.now().isoformat(),
            "tariff": "pro" if user_id == ADMIN_ID else "free",
            "expiry": None,
            "is_vip": True if user_id == ADMIN_ID else False,
            "vip_expiry": None
        }
        save_user_data()
    return user_data[uid]

def get_user_tariff(user_id: int) -> str:
    uid = str(user_id)
    if uid not in user_data: register_user(user_id)
    user = user_data[uid]
    if user["tariff"] != "free" and user["expiry"]:
        if datetime.now() > datetime.fromisoformat(user["expiry"]):
            user["tariff"] = "free"; user["expiry"] = None; save_user_data()
    return user["tariff"]

def check_user_vip(user_id: int) -> bool:
    uid = str(user_id)
    if uid not in user_data: register_user(user_id)
    user = user_data[uid]
    if user.get("is_vip") and user.get("vip_expiry"):
        if datetime.now() > datetime.fromisoformat(user["vip_expiry"]):
            user["is_vip"] = False; user["vip_expiry"] = None; save_user_data()
    return user.get("is_vip", False)

def create_group(chat_id: int, owner_id: int) -> Dict:
    data["groups"][str(chat_id)] = {"owner": owner_id, "settings": DEFAULT_SETTINGS.copy()}
    save_data()
    return data["groups"][str(chat_id)]

# ---------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ----------
def mask_id(uid) -> str:
    return "[Скрыто]" if str(uid) == str(ADMIN_ID) else str(uid)

def extract_urls(text: str) -> List[str]:
    return re.findall(r'(https?://[^\s]+|www\.[^\s]+)', text)

def get_platform(url: str) -> Optional[str]:
    u = url.lower()
    if 't.me' in u or 'telegram.me' in u: return 'tg'
    if 'youtube.com' in u or 'youtu.be' in u: return 'yt'
    if 'tiktok.com' in u: return 'tt'
    if 'instagram.com' in u: return 'ig'
    if 'vk.com' in u or 'vk.cc' in u: return 'vk'
    return None

def contains_link(text: str) -> bool: return bool(re.search(r'(https?://|www\.)\S+', text, re.IGNORECASE))
def contains_invite_link(text: str) -> bool:
    patterns = [r'(?:https?://)?t\.me/joinchat/\S+', r'(?:https?://)?t\.me/\+[\w-]+', r'(?:https?://)?t\.me/c/\d+/\d+', r'(?:https?://)?t\.me/join\b']
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)

def is_caps_abuse(text: str, threshold: int = 70) -> bool:
    letters = [c for c in text if c.isalpha()]
    if not letters: return False
    return (sum(1 for c in letters if c.isupper()) / len(letters)) * 100 > threshold

def is_flooding(user_id: int, chat_id: int, strict: bool = False, is_vip: bool = False) -> bool:
    settings = get_group_settings(chat_id)
    if not settings: return False
    limit, window = (settings.get("strict_flood_limit", 9), settings.get("strict_flood_window", 3)) if strict else (settings["flood_limit"], settings["flood_window"])
    if is_vip: limit *= 2
    timestamps = [ts for ts in user_messages[user_id] if ts > datetime.now() - timedelta(seconds=window)]
    user_messages[user_id] = timestamps
    timestamps.append(datetime.now())
    return len(timestamps) > limit

async def is_group_admin(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except: return False

async def get_group_owner(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
    try:
        for a in await context.bot.get_chat_administrators(chat_id):
            if a.status == "creator": return a.user.id
    except: return None

def parse_duration(text: str) -> int:
    if not text: return 3600
    match = re.match(r'(\d+)\s*([smhdсмчд])?', text.strip().lower())
    if not match: return 3600
    num, unit = int(match.group(1)), match.group(2)
    if unit in ('s', 'с'): return num
    elif unit in ('m', 'м'): return num * 60
    elif unit in ('h', 'ч'): return num * 3600
    elif unit in ('d', 'д'): return num * 86400
    return num * 60

# ---------- НАКАЗАНИЯ ----------
async def restrict_user(chat_id: int, user_id: int, duration: int, reason: str, context: ContextTypes.DEFAULT_TYPE):
    await mute_user(chat_id, user_id, duration, reason, context)

async def mute_user(chat_id: int, user_id: int, duration: int, reason: str, context: ContextTypes.DEFAULT_TYPE):
    try:
        until = datetime.now() + timedelta(seconds=duration)
        await context.bot.restrict_chat_member(chat_id, user_id, permissions=ChatPermissions(can_send_messages=False), until_date=until)
        s = get_group_settings(chat_id)
        if s:
            s["stats"]["violations"] += 1
            s["stats"]["history"].append({"user": user_id, "time": datetime.now().isoformat(), "reason": reason, "duration": duration})
            s["stats"]["history"] = s["stats"]["history"][-100:]
            update_group_setting(chat_id, "stats", s["stats"])

        d_str = f"{duration//86400} дн." if duration >= 86400 else f"{duration//3600} ч." if duration >= 3600 else f"{duration//60} мин." if duration >= 60 else f"{duration} сек."
        await context.bot.send_message(chat_id, f"🔇 Пользователь `{mask_id(user_id)}` получил мут на {d_str}\nПричина: {reason}", parse_mode="Markdown")
        return True
    except Exception: return False

async def unmute_user(chat_id, user_id, context):
    try:
        await context.bot.restrict_chat_member(chat_id, user_id, permissions=ChatPermissions(can_send_messages=True, can_send_media_messages=True, can_send_other_messages=True, can_add_web_page_previews=True))
    except Exception: pass

async def ban_user(chat_id: int, user_id: int, reason: str, context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.bot.ban_chat_member(chat_id, user_id)
        await context.bot.send_message(chat_id, f"⛔ Пользователь `{mask_id(user_id)}` забанен.\nПричина: {reason}", parse_mode="Markdown")
        s = get_group_settings(chat_id)
        if s:
            s["stats"]["violations"] += 1
            s["stats"]["history"].append({"user": user_id, "time": datetime.now().isoformat(), "reason": reason, "duration": 0})
            update_group_setting(chat_id, "stats", s["stats"])
    except Exception: pass

async def unban_user(chat_id, user_id, context):
    try:
        await context.bot.unban_chat_member(chat_id, user_id)
        await context.bot.send_message(chat_id, f"✅ Пользователь `{mask_id(user_id)}` разбанен.", parse_mode="Markdown")
    except Exception: pass

async def add_warning(chat_id: int, user_id: int, reason: str, context: ContextTypes.DEFAULT_TYPE):
    s = get_group_settings(chat_id)
    if not s: return 0
    warns = s.setdefault("warnings", {}).setdefault(str(user_id), [])
    warns = [w for w in warns if datetime.fromisoformat(w["time"]) > datetime.now() - timedelta(days=7)]
    warns.append({"time": datetime.now().isoformat(), "reason": reason})
    s["warnings"][str(user_id)] = warns[-10:]
    update_group_setting(chat_id, "warnings", s["warnings"])
    await context.bot.send_message(chat_id, f"⚠️ Пользователь `{mask_id(user_id)}` получил предупреждение.\nПричина: {reason}\nВсего: {len(warns)}", parse_mode="Markdown")
    if len(warns) >= 3:
        await mute_user(chat_id, user_id, 3600, "3 предупреждения (автомут)", context)
        s["warnings"][str(user_id)] = []
        update_group_setting(chat_id, "warnings", s["warnings"])
    return len(warns)

async def get_warnings(chat_id, user_id) -> int:
    s = get_group_settings(chat_id)
    if not s: return 0
    warns = s.get("warnings", {}).get(str(user_id), [])
    return len([w for w in warns if datetime.fromisoformat(w["time"]) > datetime.now() - timedelta(days=7)])
    # ---------- ТАЙМЕР КАПЧИ ----------
async def captcha_timer(chat_id: int, user_id: int, message_id: int, context: ContextTypes.DEFAULT_TYPE):
    await asyncio.sleep(120)
    cid_str = str(chat_id)
    if cid_str in pending_captchas and user_id in pending_captchas[cid_str]:
        try:
            del pending_captchas[cid_str][user_id]
            await context.bot.ban_chat_member(chat_id, user_id) 
            await context.bot.unban_chat_member(chat_id, user_id) 
            await context.bot.delete_message(chat_id, message_id)
        except Exception: pass

# ---------- ТЕКСТОВЫЕ КОМАНДЫ АДМИНА (*мут, *бан) ----------
async def process_admin_text_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, chat_id = update.effective_message, update.effective_chat.id
    target = msg.reply_to_message.from_user
    if target.is_bot: return
    parts = msg.text.lower().split()
    cmd = parts[0]
    try:
        if cmd == '*мут':
            dur = parse_duration(parts[1]) if len(parts) > 1 and parts[1][0].isdigit() else 3600
            rsn = " ".join(parts[2:]) if (len(parts) > 1 and parts[1][0].isdigit()) else " ".join(parts[1:])
            await mute_user(chat_id, target.id, dur, rsn or "Нарушение правил", context)
            await msg.delete()
        elif cmd == '*бан':
            await ban_user(chat_id, target.id, " ".join(parts[1:]) or "Нарушение правил", context)
            await msg.delete()
        elif cmd == '*размут':
            await unmute_user(chat_id, target.id, context)
            await context.bot.send_message(chat_id, f"🔊 Пользователь `{mask_id(target.id)}` размучен администратором.", parse_mode="Markdown")
            await msg.delete()
        elif cmd == '*разбан':
            await unban_user(chat_id, target.id, context)
            await msg.delete()
    except Exception: pass

# ---------- ЗАЩИТА СООБЩЕНИЙ ----------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not update.effective_user: return
    chat, user, msg = update.effective_chat, update.effective_user, update.effective_message
    if user.is_bot: return

    if chat.type in ("group", "supergroup"):
        if str(chat.id) not in data["groups"]:
            owner_id = await get_group_owner(chat.id, context)
            if owner_id: create_group(chat.id, owner_id)
            else: return
            
        settings = get_group_settings(chat.id)
        if not settings: return

        if user.id not in settings.setdefault("seen_users", []):
            settings["seen_users"].append(user.id)
            update_group_setting(chat.id, "seen_users", settings["seen_users"])

        settings["stats"]["messages"] += 1
        update_group_setting(chat.id, "stats", settings["stats"])

        # Защита от обхода мута капчи
        if str(chat.id) in pending_captchas and user.id in pending_captchas[str(chat.id)]:
            try: await msg.delete()
            except: pass
            return

        # Проверка на текстовые команды (*мут)
        if msg.reply_to_message and msg.text and msg.text.lower().startswith(('*мут', '*бан', '*размут', '*разбан')):
            if await is_group_admin(chat.id, user.id, context):
                await process_admin_text_command(update, context)
                return

        if await is_group_admin(chat.id, user.id, context): return

        g = get_group_data(chat.id)
        owner_tariff = get_user_tariff(g["owner"])
        is_vip = check_user_vip(user.id)
        
        # 1. ТРИГГЕРЫ (Автоответчик)
        if msg.text and owner_tariff in ["standard", "pro"]:
            msg_lower = msg.text.lower()
            for trigger_word, trigger_reply in settings.get("triggers", {}).items():
                if trigger_word.lower() in msg_lower:
                    await msg.reply_text(trigger_reply)
                    break 

        # 2. Антифлуд (С учетом VIP)
        if TARIFF_FEATURES[owner_tariff].get("strict_flood", False) and is_flooding(user.id, chat.id, True, is_vip):
            await mute_user(chat.id, user.id, settings.get("strict_flood_mute", 600), "Строгий флуд", context)
            try: await msg.delete()
            except: pass
            return
        elif is_flooding(user.id, chat.id, False, is_vip):
            await mute_user(chat.id, user.id, settings["flood_mute"], "Флуд", context)
            try: await msg.delete()
            except: pass
            return

        # 3. Проверка Ссылок (ПРО)
        if msg.text and owner_tariff == "pro":
            urls = extract_urls(msg.text)
            review_triggered = None
            for url in urls:
                plat = get_platform(url)
                if plat and settings.get("link_review", {}).get(plat):
                    wl = settings.get("whitelisted_links", {})
                    now = datetime.now()
                    wl = {k: v for k, v in wl.items() if datetime.fromisoformat(v) > now}
                    update_group_setting(chat.id, "whitelisted_links", wl)
                    if url not in wl:
                        review_triggered = url
                        break
            if review_triggered:
                try: await msg.delete()
                except: pass
                lid = str(uuid.uuid4())[:8]
                pending_reviews[lid] = {"url": review_triggered, "user_id": user.id, "chat_id": chat.id, "text": msg.text}
                kb = [[InlineKeyboardButton("✅ Одобрить", callback_data=f"aprv_{lid}"), InlineKeyboardButton("❌ Отказать", callback_data=f"rjct_{lid}")]]
                await context.bot.send_message(chat.id, f"🚨 *Ссылка на проверке*\nОт: {user.full_name} (`{mask_id(user.id)}`)\nТекст: {msg.text}\nСсылка: {review_triggered}", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
                return

        # 4. Обычные ссылки и фильтры
        if settings.get("block_links", True) and msg.text and contains_link(msg.text):
            await mute_user(chat.id, user.id, 60, "Запрещённые ссылки", context)
            try: await msg.delete()
            except: pass
            return
        if settings.get("invite_links_block", True) and msg.text and contains_invite_link(msg.text):
            await mute_user(chat.id, user.id, 3600, "Инвайт-ссылка", context)
            try: await msg.delete()
            except: pass
            return
        if settings.get("caps_filter", False) and msg.text and is_caps_abuse(msg.text, settings.get("caps_threshold", 70)):
            await mute_user(chat.id, user.id, 1800, "CAPS", context)
            try: await msg.delete()
            except: pass
            return
        if settings.get("block_media", False) and any((msg.photo, msg.video, msg.document, msg.voice, msg.audio, msg.animation, msg.sticker)):
            await mute_user(chat.id, user.id, 60, "Медиафайлы запрещены", context)
            try: await msg.delete()
            except: pass
            return

# ---------- НОВЫЕ УЧАСТНИКИ И КАПЧА ----------
async def handle_new_chat_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    
    if str(chat.id) not in data["groups"]:
        owner_id = await get_group_owner(chat.id, context)
        if owner_id: create_group(chat.id, owner_id)
        
    settings = get_group_settings(chat.id)
    if not settings: return

    for member in update.message.new_chat_members:
        if member.id == context.bot.id:
            await update.message.reply_text("🤖 *Бот-защитник активирован!*\nДайте права админа и введите /addgroup", parse_mode="Markdown")
            return
            
        g = get_group_data(chat.id)
        owner_tariff = get_user_tariff(g["owner"]) if g else "free"
        
        # VIP-юзеры проходят без капчи!
        if check_user_vip(member.id):
            if settings.get("custom_welcome"):
                await update.message.reply_text(f"🌟 VIP Пользователь зашел в чат!\n" + settings["custom_welcome"].replace("{name}", member.full_name))
            else:
                await update.message.reply_text(f"🌟 VIP Пользователь [{member.full_name}](tg://user?id={member.id}) присоединился!", parse_mode="Markdown")
            continue

        if settings.get("captcha_enabled", False) and owner_tariff in ["standard", "pro"]:
            try:
                await context.bot.restrict_chat_member(chat.id, member.id, permissions=ChatPermissions(can_send_messages=False))
                kb = [[InlineKeyboardButton("🤖 Я человек", callback_data=f"captcha_{chat.id}_{member.id}")]]
                sent_msg = await update.message.reply_text(
                    f"👋 Добро пожаловать, [{member.full_name}](tg://user?id={member.id})!\n\n⚠️ Чтобы писать в чат, подтвердите, что вы не бот.\n⏱ У вас есть *2 минуты*.",
                    reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
                )
                pending_captchas[str(chat.id)][member.id] = sent_msg.message_id
                asyncio.create_task(captcha_timer(chat.id, member.id, sent_msg.message_id, context))
            except Exception: pass
        else:
            if settings.get("custom_welcome"):
                await update.message.reply_text(settings["custom_welcome"].replace("{name}", member.full_name))

# ---------- СТАНДАРТНЫЕ КОМАНДЫ МОДЕРАЦИИ ----------
async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat, user = update.effective_chat, update.effective_user
    if chat.type not in ("group", "supergroup") or not await is_group_admin(chat.id, user.id, context): return

    if not context.args: return await update.message.reply_text("Использование: /mute @пользователь [время]")
    
    target, duration_str = None, None
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
    chat, user = update.effective_chat, update.effective_user
    if chat.type not in ("group", "supergroup") or not await is_group_admin(chat.id, user.id, context): return
    if not context.args: return await update.message.reply_text("Использование: /unmute @пользователь")
    try: target = (await chat.get_member(context.args[0][1:])).user.id if context.args[0].startswith('@') else int(context.args[0])
    except: return
    await unmute_user(chat.id, target, context)
    await update.message.reply_text(f"🔊 Пользователь `{mask_id(target)}` размучен.", parse_mode="Markdown")

async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat, user = update.effective_chat, update.effective_user
    if chat.type not in ("group", "supergroup") or not await is_group_admin(chat.id, user.id, context): return
    if not context.args: return await update.message.reply_text("Использование: /ban @пользователь")
    try: target = (await chat.get_member(context.args[0][1:])).user.id if context.args[0].startswith('@') else int(context.args[0])
    except: return
    await ban_user(chat.id, target, f"Команда /ban от {mask_id(user.id)}", context)

async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat, user = update.effective_chat, update.effective_user
    if chat.type not in ("group", "supergroup") or not await is_group_admin(chat.id, user.id, context): return
    if not context.args: return await update.message.reply_text("Использование: /unban ID_пользователя")
    try: target = int(context.args[0])
    except: return await update.message.reply_text("Укажите числовой ID пользователя.")
    await unban_user(chat.id, target, context)

async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat, user = update.effective_chat, update.effective_user
    if chat.type not in ("group", "supergroup") or not await is_group_admin(chat.id, user.id, context): return
    if not context.args: return await update.message.reply_text("Использование: /warn @пользователь [причина]")
    try: target = (await chat.get_member(context.args[0][1:])).user.id if context.args[0].startswith('@') else int(context.args[0])
    except: return
    reason = " ".join(context.args[1:]) if len(context.args) > 1 else "Без указания причины"
    await add_warning(chat.id, target, reason, context)

async def cmd_warns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat, user = update.effective_chat, update.effective_user
    if chat.type not in ("group", "supergroup") or not await is_group_admin(chat.id, user.id, context): return
    if not context.args: return await update.message.reply_text("Использование: /warns @пользователь")
    try: target = (await chat.get_member(context.args[0][1:])).user.id if context.args[0].startswith('@') else int(context.args[0])
    except: return
    count = await get_warnings(chat.id, target)
    await update.message.reply_text(f"📊 У пользователя `{mask_id(target)}` {count} предупреждений.", parse_mode="Markdown")
async def addgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat, user = update.effective_chat, update.effective_user
    if chat.type not in ("group", "supergroup"): return
    if not await is_group_admin(chat.id, user.id, context): return
    if get_group_data(chat.id):
        await update.message.reply_text("✅ Группа уже добавлена.")
        return
    owner_id = await get_group_owner(chat.id, context) or user.id
    create_group(chat.id, owner_id)
    await update.message.reply_text(f"✅ Группа добавлена! Владелец: `{mask_id(owner_id)}`", parse_mode="Markdown")
        return
    owner_id = await get_group_owner(chat.id, context) or user.id
    create_group(chat.id, owner_id)
    await update.message.reply_text(f"✅ Группа добавлена! Владелец: `{mask_id(owner_id)}`", parse_mode="Markdown")
    owner_id = await get_group_owner(chat.id, context) or user.id
    create_group(chat.id, owner_id)
    await update.message.reply_text(f"✅ Группа добавлена! Владелец: `{mask_id(owner_id)}`", parse_mode="Markdown")
    owner_id = await get_group_owner(chat.id, context) or user.id
    create_group(chat.id, owner_id)
    await update.message.reply_text(f"✅ Группа добавлена! Владелец: `{mask_id(owner_id)}`", parse_mode="Markdown")
    owner_id = await get_group_owner(chat.id, context) or user.id
    create_group(chat.id, owner_id)
    await update.message.reply_text(f"✅ Группа добавлена! Владелец: `{mask_id(owner_id)}`", parse_mode="Markdown")

# ---------- МЕНЮ ЛИЧНЫХ СООБЩЕНИЙ ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    register_user(user_id)
    await show_main_menu(update, context)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, edit_message=False, chat_id=None, message_id=None):
    user_id = update.effective_user.id
    keyboard = [
        [InlineKeyboardButton("👤 Профиль", callback_data="profile"),
         InlineKeyboardButton("📋 Мои группы", callback_data="groups")],
        [InlineKeyboardButton("💰 Тарифы и VIP", callback_data="show_tariffs")]
    ]
    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("👑 Админ панель", callback_data="admin_panel")])

    text = "👋 *Главное меню*\nВыберите действие:"
    if edit_message and chat_id and message_id:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    user = register_user(user_id)
    tariff = get_user_tariff(user_id)
    is_vip = check_user_vip(user_id)
    reg_date = datetime.fromisoformat(user["registered"]).strftime("%d.%m.%Y %H:%M")
    
    text = (
        f"👤 *Ваш профиль*\n\n"
        f"🆔 ID: `{mask_id(user_id)}`\n"
        f"📅 Регистрация: {reg_date}\n\n"
    )
    
    if is_vip:
        text += f"🌟 *VIP-СТАТУС: АКТИВЕН*\n"
        if user.get("vip_expiry"): text += f"⏱ До: {datetime.fromisoformat(user['vip_expiry']).strftime('%d.%m.%Y')}\n\n"
    else:
        text += f"👤 *VIP-СТАТУС: НЕТ* (Купите для обхода капчи и x2 лимитов)\n\n"
        
    text += f"💎 Тариф (Для ваших групп): *{tariff.upper()}*\n"
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
            "Нет добавленных групп.\n\nДобавьте бота в группу, дайте ему права администратора и отправьте /addgroup",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]])
        )
        return
    keyboard = []
    for cid in data["groups"].keys():
        try: name = (await context.bot.get_chat(int(cid))).title or f"Группа {cid}"
        except: name = f"Группа {cid}"
        keyboard.append([InlineKeyboardButton(name, callback_data=f"group_main_{cid}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="main_menu")])
    await query.edit_message_text("📋 *Список групп:*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

# ---------- МЕНЮ ГРУППЫ ----------
async def group_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, query=None, override_chat_id=None):
    chat_id = override_chat_id or update.effective_chat.id
    user_id = query.from_user.id if query else update.effective_user.id
    
    if not await is_group_admin(chat_id, user_id, context):
        if not query: await update.message.reply_text("⛔ Только администраторы группы могут настраивать бота.")
        return

    g = get_group_data(chat_id)
    if not g:
        if not query: await update.message.reply_text("⚠️ Группа не добавлена. Введите /addgroup.")
        return

    owner_id = g["owner"]
    tariff = get_user_tariff(owner_id)

    text = f"🛡 *Настройки группы*\nID: `{chat_id}`\nВладелец: `{mask_id(owner_id)}` ({tariff.upper()})\n\nВыберите раздел:"
    keyboard = [
        [InlineKeyboardButton("⚙️ Антиспам", callback_data=f"group_anti_spam_{chat_id}"),
         InlineKeyboardButton("🔗 Ссылки", callback_data=f"group_links_menu_{chat_id}")],
        [InlineKeyboardButton("🔠 CAPS и Медиа", callback_data=f"group_media_menu_{chat_id}"),
         InlineKeyboardButton("🤖 ИИ", callback_data=f"group_ai_menu_{chat_id}")],
        [InlineKeyboardButton("🚪 Вход и Капча", callback_data=f"group_entrance_menu_{chat_id}"),
         InlineKeyboardButton("🗣 Триггеры", callback_data=f"group_triggers_menu_{chat_id}")],
        [InlineKeyboardButton("📊 Статистика", callback_data=f"group_stats_{chat_id}")]
    ]
    
    if query and query.message.chat.type == "private":
        keyboard.append([InlineKeyboardButton("🔙 К списку групп", callback_data="groups")])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    elif query:
        keyboard.append([InlineKeyboardButton("❌ Закрыть", callback_data="close_menu")])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        keyboard.append([InlineKeyboardButton("❌ Закрыть", callback_data="close_menu")])
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

# Подменюшки
async def group_anti_spam_menu(query, chat_id, context):
    s = get_group_settings(chat_id)
    is_adv = TARIFF_FEATURES[get_user_tariff(get_group_data(chat_id)["owner"])].get("strict_flood", False)
    text = (f"*Настройка антиспама*\nЛимит: {s['flood_limit']} за {s['flood_window']} сек\nМут: {s['flood_mute']} сек\n")
    if is_adv: text += (f"\n*Строгий (9/3):*\nЛимит: {s.get('strict_flood_limit',9)} за {s.get('strict_flood_window',3)} сек\nМут: {s.get('strict_flood_mute',600)} сек\n")
    
    keyboard = [
        [InlineKeyboardButton("📈 +1", callback_data=f"limit_inc_{chat_id}"), InlineKeyboardButton("📉 -1", callback_data=f"limit_dec_{chat_id}")],
        [InlineKeyboardButton("⏱ +5с", callback_data=f"window_inc_{chat_id}"), InlineKeyboardButton("⏱ -5с", callback_data=f"window_dec_{chat_id}")],
        [InlineKeyboardButton("🔇 +30с мут", callback_data=f"mute_inc_{chat_id}"), InlineKeyboardButton("🔊 -30с мут", callback_data=f"mute_dec_{chat_id}")],
    ]
    if is_adv: keyboard.append([InlineKeyboardButton("🔧 Настроить строгий режим", callback_data=f"group_strict_anti_spam_{chat_id}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=f"group_main_{chat_id}")])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def group_strict_anti_spam_menu(query, chat_id, context):
    s = get_group_settings(chat_id)
    text = (f"*Строгий антиспам*\nЛимит: {s.get('strict_flood_limit',9)}\nОкно: {s.get('strict_flood_window',3)} сек\nМут: {s.get('strict_flood_mute',600)} сек")
    keyboard = [
        [InlineKeyboardButton("📈 +1", callback_data=f"s_limit_inc_{chat_id}"), InlineKeyboardButton("📉 -1", callback_data=f"s_limit_dec_{chat_id}")],
        [InlineKeyboardButton("⏱ +5с", callback_data=f"s_window_inc_{chat_id}"), InlineKeyboardButton("⏱ -5с", callback_data=f"s_window_dec_{chat_id}")],
        [InlineKeyboardButton("🔇 +60с мут", callback_data=f"s_mute_inc_{chat_id}"), InlineKeyboardButton("🔊 -60с мут", callback_data=f"s_mute_dec_{chat_id}")],
        [InlineKeyboardButton("🔙 Назад", callback_data=f"group_anti_spam_{chat_id}")]
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def group_links_menu(query, chat_id, context):
    s = get_group_settings(chat_id)
    tariff = get_user_tariff(get_group_data(chat_id)["owner"])
    text = (f"🔗 *Настройка ссылок*\n\nОбычные: {'✅' if s['block_links'] else '❌'}\nИнвайты: {'✅' if s['invite_links_block'] else '❌'}")
    keyboard = [
        [InlineKeyboardButton(f"Обычные: {'ВКЛ' if s['block_links'] else 'ВЫКЛ'}", callback_data=f"toggle_block_links_{chat_id}")],
        [InlineKeyboardButton(f"Инвайты: {'ВКЛ' if s['invite_links_block'] else 'ВЫКЛ'}", callback_data=f"toggle_invite_links_block_{chat_id}")]
    ]
    if tariff == "pro": keyboard.append([InlineKeyboardButton("🛡 Умная проверка соцсетей", callback_data=f"group_link_review_{chat_id}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=f"group_main_{chat_id}")])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def group_link_review_menu(query, chat_id, context):
    s = get_group_settings(chat_id).setdefault("link_review", {"tg": False, "yt": False, "tt": False, "ig": False, "vk": False})
    text = ("🛡 *Отправка ссылок на проверку админам*\nВыберите платформы для перехвата:")
    keyboard = [
        [InlineKeyboardButton(f"Telegram {'✅' if s['tg'] else '❌'}", callback_data=f"tog_rev_tg_{chat_id}"), InlineKeyboardButton(f"YouTube {'✅' if s['yt'] else '❌'}", callback_data=f"tog_rev_yt_{chat_id}")],
        [InlineKeyboardButton(f"TikTok {'✅' if s['tt'] else '❌'}", callback_data=f"tog_rev_tt_{chat_id}"), InlineKeyboardButton(f"Insta {'✅' if s['ig'] else '❌'}", callback_data=f"tog_rev_ig_{chat_id}")],
        [InlineKeyboardButton(f"VK {'✅' if s['vk'] else '❌'}", callback_data=f"tog_rev_vk_{chat_id}")],
        [InlineKeyboardButton("🔙 Назад", callback_data=f"group_links_menu_{chat_id}")]
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def group_media_menu(query, chat_id, context):
    s = get_group_settings(chat_id)
    tariff = get_user_tariff(get_group_data(chat_id)["owner"])
    keyboard = []
    if TARIFF_FEATURES[tariff]["caps_filter"]:
        keyboard.append([InlineKeyboardButton(f"CAPS фильтр: {'ВКЛ' if s['caps_filter'] else 'ВЫКЛ'}", callback_data=f"toggle_caps_filter_{chat_id}")])
        keyboard.append([InlineKeyboardButton(f"Порог CAPS: {s.get('caps_threshold',70)}%", callback_data=f"group_caps_threshold_{chat_id}")])
    if TARIFF_FEATURES[tariff]["block_media"]:
        keyboard.append([InlineKeyboardButton(f"Блок медиа: {'ВКЛ' if s['block_media'] else 'ВЫКЛ'}", callback_data=f"toggle_block_media_{chat_id}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=f"group_main_{chat_id}")])
    await query.edit_message_text("🔠 *CAPS и Медиафайлы*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def group_caps_threshold_menu(query, chat_id, context):
    s = get_group_settings(chat_id)
    keyboard = [[InlineKeyboardButton(f"{t}%", callback_data=f"set_caps_{t}_{chat_id}")] for t in [10, 30, 50, 70, 100]]
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=f"group_media_menu_{chat_id}")])
    await query.edit_message_text(f"*Порог CAPS:* {s.get('caps_threshold', 70)}%\nВыберите новый:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def group_entrance_menu(query, chat_id, context):
    s = get_group_settings(chat_id)
    tariff = get_user_tariff(get_group_data(chat_id)["owner"])
    text = f"🚪 *Настройки Входа*\n\n🤖 Капча: *{'✅ Вкл' if s.get('captcha_enabled') else '❌ Выкл'}*\n👋 Приветствие: *{'✅ Установлено' if s.get('custom_welcome') else '❌ Отключено'}*"
    keyboard = []
    if tariff in ["standard", "pro"]:
        keyboard.append([InlineKeyboardButton(f"Капча: {'ВЫКЛЮЧИТЬ' if s.get('captcha_enabled') else 'ВКЛЮЧИТЬ'}", callback_data=f"toggle_captcha_enabled_{chat_id}")])
    else: keyboard.append([InlineKeyboardButton("🔒 Капча (Только Standard/PRO)", callback_data="noop")])
    keyboard.append([InlineKeyboardButton("✏️ Изменить приветствие", callback_data=f"group_set_welcome_{chat_id}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=f"group_main_{chat_id}")])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def group_ai_menu(query, chat_id, context):
    s = get_group_settings(chat_id)
    if get_user_tariff(get_group_data(chat_id)["owner"]) != "pro": return await query.answer("🤖 ИИ модерация доступна только на PRO!", show_alert=True)
    text = (f"🤖 *ИИ Модератор*\n\nСостояние: *{'Включен' if s.get('ai_enabled') else 'Выключен'}*\nПромпт: _{s.get('ai_prompt', 'Стандартный')}_")
    keyboard = [
        [InlineKeyboardButton(f"ИИ: {'Выключить' if s.get('ai_enabled') else 'Включить'}", callback_data=f"toggle_ai_enabled_{chat_id}")],
        [InlineKeyboardButton("✏️ Изменить промпт", callback_data=f"set_ai_prompt_{chat_id}")],
        [InlineKeyboardButton("🔙 Назад", callback_data=f"group_main_{chat_id}")]
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

# МЕНЮ ТРИГГЕРОВ (АВТООТВЕТЧИК)
async def group_triggers_menu(query, chat_id, context):
    s = get_group_settings(chat_id)
    if get_user_tariff(get_group_data(chat_id)["owner"]) not in ["standard", "pro"]: 
        return await query.answer("🗣 Доступно только на Standard и PRO!", show_alert=True)
    
    triggers = s.get("triggers", {})
    text = "🗣 *Автоответчик (Триггеры)*\n\nБот будет отвечать на заданные слова.\n*Текущие триггеры:*\n"
    for word, reply in list(triggers.items())[:10]:
        text += f"• `{word}` ➡️ {reply[:15]}...\n"
    if not triggers: text += "_Нет добавленных триггеров_"
    
    keyboard = [
        [InlineKeyboardButton("➕ Добавить триггер", callback_data=f"add_trigger_{chat_id}")],
        [InlineKeyboardButton("🗑 Очистить все", callback_data=f"clear_triggers_{chat_id}")],
        [InlineKeyboardButton("🔙 Назад", callback_data=f"group_main_{chat_id}")]
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def group_show_stats(query, chat_id, context):
    g = get_group_data(chat_id)
    if get_user_tariff(g["owner"]) != "pro": return await query.answer("📊 Доступно только на PRO", show_alert=True)
    stats = g["settings"]["stats"]
    text = f"*📊 Статистика группы*\n\nСообщений: {stats['messages']}\nНарушений: {stats['violations']}\n\n*Последние нарушения:*\n"
    for e in stats.get("history", [])[-10:]: text += f"• {datetime.fromisoformat(e['time']).strftime('%d.%m %H:%M')} – {e['reason']}\n"
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data=f"group_main_{chat_id}")]]))

# ---------- ТАРИФЫ И ОПЛАТА ЮMONEY ----------
async def show_tariffs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    text = "*Доступные тарифы и услуги:*\n\n" + "\n\n".join(TARIFF_DESCRIPTIONS.values())
    keyboard = [
        [InlineKeyboardButton("🆓 Бесплатный", callback_data="tariff_info_free")],
        [InlineKeyboardButton("🌟 VIP для пользователя (50 руб)", callback_data="tariff_info_vip")],
        [InlineKeyboardButton("⭐ Стандартный (99 руб)", callback_data="tariff_info_standard")],
        [InlineKeyboardButton("💎 Профессиональный (199 руб)", callback_data="tariff_info_pro")],
        [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def show_tariff_info(query, tariff: str, context):
    if tariff == "free": return await query.edit_message_text(TARIFF_DESCRIPTIONS["free"] + "\n\n✅ Уже активен по умолчанию.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="show_tariffs")]]))
    text = TARIFF_DESCRIPTIONS[tariff] + f"\n\nСтоимость: {PRICES_RUB[tariff]} руб.\nДействует 30 дней."
    keyboard = [[InlineKeyboardButton("💳 Купить", callback_data=f"buy_{tariff}")], [InlineKeyboardButton("🔙 Назад", callback_data="show_tariffs")]]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

def generate_yoomoney_link(amount: float, label: str, description: str) -> str:
    """Генерирует ссылку на прямую оплату картой или кошельком"""
    return f"https://yoomoney.ru/quickpay/confirm.xml?receiver={YOOMONEY_WALLET}&quickpay-form=shop&targets={description}&paymentType=AC&sum={amount}&label={label}"

def check_yoomoney_payment(label: str) -> bool:
    """Проверяет историю кошелька на наличие оплаты с нужным label"""
    try:
        url = "https://yoomoney.ru/api/operation-history"
        headers = {
            "Authorization": f"Bearer {YOOMONEY_TOKEN}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        data = {"label": label}
        res = requests.post(url, headers=headers, data=data, timeout=10).json()
        if res.get("operations"): 
            return True 
    except Exception as e:
        logging.error(f"Ошибка проверки ЮMoney: {e}")
    return False

async def buy_tariff(query, tariff: str, context):
    price_rub = PRICES_RUB[tariff]
    name = "VIP Статус" if tariff == "vip" else f"Тариф {tariff.upper()}"
    invoice_id = str(uuid.uuid4())[:8] 
    
    pay_url = generate_yoomoney_link(price_rub, invoice_id, f"Активация {name}")
    pending_payments[invoice_id] = {"user_id": query.from_user.id, "tariff": tariff}
    
    keyboard = [
        [InlineKeyboardButton("💳 Оплатить (Карта / СБП)", url=pay_url)],
        [InlineKeyboardButton("✅ Проверить оплату", callback_data=f"check_payment_{invoice_id}")],
        [InlineKeyboardButton("❌ Отмена", callback_data="show_tariffs" if tariff != "vip" else "main_menu")]
    ]
    await query.edit_message_text(
        f"💸 *Оплата: {name}*\n"
        f"Стоимость: {price_rub} руб.\n\n"
        f"_Оплата доступна с любой РФ карты, СБП или кошелька ЮMoney._\n"
        f"После оплаты нажмите кнопку проверки.", 
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def check_payment(query, invoice_id: str, context):
    if invoice_id not in pending_payments: 
        return await query.edit_message_text("❌ Счёт не найден или устарел.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]]))
    info = pending_payments[invoice_id]
    
    if check_yoomoney_payment(invoice_id):
        if info["tariff"] == "vip":
            user = register_user(info["user_id"])
            user["is_vip"] = True
            user["vip_expiry"] = (datetime.now() + timedelta(days=30)).isoformat()
            save_user_data()
            name = "VIP-Статус"
        else:
            set_user_tariff(info["user_id"], info["tariff"], 30)
            name = f"Тариф {info['tariff'].upper()}"
            
        del pending_payments[invoice_id]
        await query.edit_message_text(f"✅ *Оплата подтверждена!*\n{name} успешно активирован на 30 дней.\n", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 В меню", callback_data="main_menu")]]))
    else: 
        await query.answer("⏳ Оплата пока не обнаружена. Подождите 1-2 минуты и нажмите снова.", show_alert=True)

# ---------- АДМИН-ПАНЕЛЬ ----------
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != ADMIN_ID: return
    keyboard = [
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"), InlineKeyboardButton("👥 Пользователи", callback_data="admin_users")],
        [InlineKeyboardButton("🔍 Найти группу", callback_data="admin_find_group"), InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
    ]
    await query.edit_message_text("👑 *Админ-панель владельца*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def admin_stats(query, context):
    text = "*📊 Список групп:*\n"
    for cid, g in data["groups"].items():
        try: name = (await context.bot.get_chat(int(cid))).title
        except: name = f"Группа {cid}"
        text += f"• {name} (`{cid}`) – владелец `{mask_id(g['owner'])}`\n"
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]]))

async def admin_users(query, context):
    text = "*👥 Пользователи:*\n"
    for uid, u in user_data.items():
        vip = "🌟 VIP" if u.get("is_vip") else u['tariff'].upper()
        text += f"• `{mask_id(uid)}` – {vip}, рег: {datetime.fromisoformat(u['registered']).strftime('%d.%m')}\n"
        if len(text) > 3500: text += "..."; break
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]]))

async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    text = update.message.text.replace('/broadcast', '').strip()
    if not text: return await update.message.reply_text("Использование: /broadcast Текст")
    await update.message.reply_text("⏳ Рассылка начата...")
    count = 0
    for uid in list(user_data.keys()):
        try: await context.bot.send_message(chat_id=int(uid), text=text); count += 1; await asyncio.sleep(0.05)
        except: pass
    await update.message.reply_text(f"✅ Доставлено: {count} пользователям.")

# ---------- ОБРАБОТЧИКИ ТЕКСТА (ВВОД) ----------
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, user_id = update.message, update.effective_user.id
    if not msg or not msg.text: return
    state = user_states.get(user_id)
    
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
            for a in admins: text += f"- {a.user.full_name} (`{mask_id(a.user.id)}`)\n"
            text += f"\n👥 *Участников в базе: {len(seen)}*\n"
            for u in seen[:30]: text += f"- `{mask_id(u)}`\n" 
            if len(seen) > 30: text += f"...и еще {len(seen)-30}"
            await msg.reply_text(text, parse_mode="Markdown")
        except Exception as e: await msg.reply_text(f"❌ Ошибка поиска: {e}")
        return

    if msg.chat.type == "private" and state and state.startswith("await_trigger_"):
        chat_id = int(state.split("_")[2])
        if "::" not in msg.text:
            return await msg.reply_text("❌ Неверный формат. Напишите: Слово :: Ваш ответ")
        word, reply = msg.text.split("::", 1)
        word, reply = word.strip().lower(), reply.strip()
        
        settings = get_group_settings(chat_id)
        if settings is not None:
            triggers = settings.setdefault("triggers", {})
            triggers[word] = reply
            update_group_setting(chat_id, "triggers", triggers)
            del user_states[user_id]
            await msg.reply_text(f"✅ Триггер добавлен!\nЕсли напишут `{word}`, бот ответит: `{reply}`")
        return

    if state and state.startswith("await_ai_prompt_"):
        chat_id = int(state.split("_")[3])
        del user_states[user_id]
        update_group_setting(chat_id, "ai_prompt", msg.text[:500])
        await msg.reply_text("✅ Промпт ИИ обновлен!")
        return
        
    if msg.chat.type == "private" and state and state.startswith("welcome_chat_"):
        chat_id = int(state.split("_")[2])
        del user_states[user_id]
        if msg.text.strip(): update_group_setting(chat_id, "custom_welcome", msg.text.strip())
        else: update_group_setting(chat_id, "custom_welcome", None)
        await msg.reply_text("✅ Приветствие сохранено.")
        return

# ---------- ВСПОМОГАТЕЛЬНЫЕ ДЛЯ КНОПОК ----------
async def change_flood_parameter(update: Update, context: ContextTypes.DEFAULT_TYPE, param: str, delta: int, chat_id: int):
    settings = get_group_settings(chat_id)
    if settings: update_group_setting(chat_id, param, max(1, settings[param] + delta))

async def change_strict_parameter(update: Update, context: ContextTypes.DEFAULT_TYPE, param: str, delta: int, chat_id: int):
    settings = get_group_settings(chat_id)
    if settings: update_group_setting(chat_id, param, max(1, settings.get(param, 0) + delta))

# ---------- ROUTER КНОПОК ----------
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    d = q.data
    uid = q.from_user.id

    if d == "close_menu":
        try: await q.message.delete()
        except: pass
        return

    if d.startswith("captcha_"):
        parts = d.split("_")
        chat_id, target_uid = int(parts[1]), int(parts[2])
        if uid != target_uid: return await q.answer("Эта кнопка не для вас! 🤖", show_alert=True)
        cid_str = str(chat_id)
        if cid_str in pending_captchas and target_uid in pending_captchas[cid_str]:
            del pending_captchas[cid_str][target_uid]
            await context.bot.restrict_chat_member(chat_id, target_uid, permissions=ChatPermissions(can_send_messages=True, can_send_media_messages=True, can_send_other_messages=True, can_add_web_page_previews=True))
            settings = get_group_settings(chat_id)
            if settings and settings.get("custom_welcome"):
                await q.edit_message_text(settings["custom_welcome"].replace("{name}", q.from_user.full_name))
            else:
                await q.edit_message_text(f"✅ [{q.from_user.full_name}](tg://user?id={uid}) успешно прошел проверку!", parse_mode="Markdown")
        else: await q.answer("Время вышло или проверка уже пройдена.", show_alert=True)
        return

    if d == "main_menu": await show_main_menu(update, context, True, q.message.chat_id, q.message.message_id); return
    if d == "profile": await show_profile(update, context); return
    if d == "groups": await show_groups(update, context); return
    if d == "show_tariffs": await show_tariffs(update, context); return
    if d == "buy_vip": await buy_tariff(q, "vip", context); return
    if d.startswith("tariff_info_"): await show_tariff_info(q, d.split("_")[2], context); return
    if d.startswith("buy_"): await buy_tariff(q, d.split("_")[1], context); return
    if d.startswith("check_payment_"): await check_payment(q, d.split("_")[2], context); return

    if d == "admin_panel": await admin_panel(update, context); return
    if d == "admin_stats": await admin_stats(q, context); return
    if d == "admin_users": await admin_users(q, context); return
    if d == "admin_find_group" and uid == ADMIN_ID:
        user_states[uid] = "await_find_group"; await q.message.reply_text("🔍 Введите ID группы:"); await q.answer(); return
    if d == "admin_broadcast" and uid == ADMIN_ID:
        return await q.edit_message_text("📢 Напишите `/broadcast Ваш текст`", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data="admin_panel")]]))

    if d.startswith("aprv_"):
        lid = d.split("_")[1]
        if lid in pending_reviews:
            if not await is_group_admin(pending_reviews[lid]["chat_id"], uid, context): return await q.answer("Только для админов!", show_alert=True)
            info = pending_reviews.pop(lid)
            wl = get_group_settings(info["chat_id"]).get("whitelisted_links", {})
            wl[info["url"]] = (datetime.now() + timedelta(hours=1)).isoformat()
            update_group_setting(info["chat_id"], "whitelisted_links", wl)
            await q.edit_message_text(f"✅ Ссылка одобрена.")
        return
    if d.startswith("rjct_"):
        lid = d.split("_")[1]
        if lid in pending_reviews:
            if not await is_group_admin(pending_reviews[lid]["chat_id"], uid, context): return await q.answer("Только для админов!", show_alert=True)
            kb = [[InlineKeyboardButton("Мут 1 ч", callback_data=f"pnsh_m1_{lid}"), InlineKeyboardButton("Мут 24 ч", callback_data=f"pnsh_m24_{lid}")], [InlineKeyboardButton("Бан", callback_data=f"pnsh_b_{lid}"), InlineKeyboardButton("Удалить", callback_data=f"pnsh_d_{lid}")]]
            await q.edit_message_text("❌ Выберите наказание:", reply_markup=InlineKeyboardMarkup(kb))
        return
    if d.startswith("pnsh_"):
        action, lid = d.split("_")[1], d.split("_")[2]
        if lid in pending_reviews:
            info = pending_reviews.pop(lid)
            if action == "m1": await mute_user(info["chat_id"], info["user_id"], 3600, "Запрещенная ссылка", context)
            elif action == "m24": await mute_user(info["chat_id"], info["user_id"], 86400, "Запрещенная ссылка", context)
            elif action == "b": await ban_user(info["chat_id"], info["user_id"], "Запрещенная ссылка", context)
            await q.edit_message_text(f"✅ Наказано.")
        return

    if d.startswith("group_main_"): await group_menu(update, context, q, int(d.split("_")[2])); return
    if d.startswith("group_anti_spam_"): await group_anti_spam_menu(q, int(d.split("_")[3]), context); return
    if d.startswith("group_strict_anti_spam_"): await group_strict_anti_spam_menu(q, int(d.split("_")[4]), context); return
    if d.startswith("group_links_menu_"): await group_links_menu(q, int(d.split("_")[3]), context); return
    if d.startswith("group_link_review_"): await group_link_review_menu(q, int(d.split("_")[3]), context); return
    if d.startswith("group_media_menu_"): await group_media_menu(q, int(d.split("_")[3]), context); return
    if d.startswith("group_caps_threshold_"): await group_caps_threshold_menu(q, int(d.split("_")[3]), context); return
    if d.startswith("group_ai_menu_"): await group_ai_menu(q, int(d.split("_")[3]), context); return
    if d.startswith("group_entrance_menu_"): await group_entrance_menu(q, int(d.split("_")[3]), context); return
    if d.startswith("group_triggers_menu_"): await group_triggers_menu(q, int(d.split("_")[3]), context); return
    if d.startswith("group_stats_"): await group_show_stats(q, int(d.split("_")[2]), context); return
    
    if d.startswith("add_trigger_"):
        chat_id = d.split("_")[2]
        user_states[uid] = f"await_trigger_{chat_id}"
        await q.message.reply_text("✏️ Отправьте триггер в формате:\n`Слово :: Ответ`\nНапример: `цена :: Прайс в закрепе`", parse_mode="Markdown")
        await q.answer(); return
    if d.startswith("clear_triggers_"):
        update_group_setting(int(d.split("_")[2]), "triggers", {})
        await group_triggers_menu(q, int(d.split("_")[2]), context); return

    if d.startswith("toggle_"):
        key, chat_id = "_".join(d.split("_")[1:-1]), int(d.split("_")[-1])
        s = get_group_settings(chat_id)
        if key in s:
            update_group_setting(chat_id, key, not s[key])
            if key in ["block_links", "invite_links_block"]: await group_links_menu(q, chat_id, context)
            elif key in ["caps_filter", "block_media"]: await group_media_menu(q, chat_id, context)
            elif key == "ai_enabled": await group_ai_menu(q, chat_id, context)
            elif key == "captcha_enabled": await group_entrance_menu(q, chat_id, context)
        return

    if d.startswith("tog_rev_"):
        plat, chat_id = d.split("_")[2], int(d.split("_")[3])
        rev = get_group_settings(chat_id).setdefault("link_review", {})
        rev[plat] = not rev.get(plat, False)
        update_group_setting(chat_id, "link_review", rev)
        await group_link_review_menu(q, chat_id, context)
        return

    if d.startswith("limit_inc_"): await change_flood_parameter(update, context, "flood_limit", 1, int(d.split("_")[2])); await group_anti_spam_menu(q, int(d.split("_")[2]), context); return
    if d.startswith("limit_dec_"): await change_flood_parameter(update, context, "flood_limit", -1, int(d.split("_")[2])); await group_anti_spam_menu(q, int(d.split("_")[2]), context); return
    if d.startswith("window_inc_"): await change_flood_parameter(update, context, "flood_window", 5, int(d.split("_")[2])); await group_anti_spam_menu(q, int(d.split("_")[2]), context); return
    if d.startswith("window_dec_"): await change_flood_parameter(update, context, "flood_window", -5, int(d.split("_")[2])); await group_anti_spam_menu(q, int(d.split("_")[2]), context); return
    if d.startswith("mute_inc_"): await change_flood_parameter(update, context, "flood_mute", 30, int(d.split("_")[2])); await group_anti_spam_menu(q, int(d.split("_")[2]), context); return
    if d.startswith("mute_dec_"): await change_flood_parameter(update, context, "flood_mute", -30, int(d.split("_")[2])); await group_anti_spam_menu(q, int(d.split("_")[2]), context); return
    if d.startswith("s_limit_inc_"): await change_strict_parameter(update, context, "strict_flood_limit", 1, int(d.split("_")[3])); await group_strict_anti_spam_menu(q, int(d.split("_")[3]), context); return
    if d.startswith("s_limit_dec_"): await change_strict_parameter(update, context, "strict_flood_limit", -1, int(d.split("_")[3])); await group_strict_anti_spam_menu(q, int(d.split("_")[3]), context); return
    if d.startswith("s_window_inc_"): await change_strict_parameter(update, context, "strict_flood_window", 5, int(d.split("_")[3])); await group_strict_anti_spam_menu(q, int(d.split("_")[3]), context); return
    if d.startswith("s_window_dec_"): await change_strict_parameter(update, context, "strict_flood_window", -5, int(d.split("_")[3])); await group_strict_anti_spam_menu(q, int(d.split("_")[3]), context); return
    if d.startswith("s_mute_inc_"): await change_strict_parameter(update, context, "strict_flood_mute", 60, int(d.split("_")[3])); await group_strict_anti_spam_menu(q, int(d.split("_")[3]), context); return
    if d.startswith("s_mute_dec_"): await change_strict_parameter(update, context, "strict_flood_mute", -60, int(d.split("_")[3])); await group_strict_anti_spam_menu(q, int(d.split("_")[3]), context); return

    if d.startswith("set_caps_"):
        update_group_setting(int(d.split("_")[3]), "caps_threshold", int(d.split("_")[2]))
        await group_media_menu(q, int(d.split("_")[3]), context)
        return

    if d.startswith("set_ai_prompt_"):
        user_states[uid] = f"await_ai_prompt_{d.split('_')[3]}"
        await q.message.reply_text("✏️ Отправьте инструкцию для ИИ-модератора:")
        await q.answer(); return

    if d.startswith("group_set_welcome_"):
        user_states[uid] = f"welcome_chat_{d.split('_')[3]}"
        await q.message.reply_text("✏️ Введите текст приветствия.\nИспользуйте `{name}` для подстановки имени:")
        await q.answer(); return

    await q.answer()

# ---------- ЗАПУСК ----------
def main():
    logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
    load_data()

    application = Application.builder().token(TOKEN).build()

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, handle_message))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_chat_members))

    application.add_handler(CommandHandler(["start", "menu"], start))
    application.add_handler(CommandHandler("addgroup", addgroup))
    application.add_handler(CommandHandler("group_menu", group_menu))

    application.add_handler(CommandHandler("mute", cmd_mute))
    application.add_handler(CommandHandler("unmute", cmd_unmute))
    application.add_handler(CommandHandler("ban", cmd_ban))
    application.add_handler(CommandHandler("unban", cmd_unban))
    application.add_handler(CommandHandler("warn", cmd_warn))
    application.add_handler(CommandHandler("warns", cmd_warns))

    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logging.info("✅ Бот запущен и готов к работе (ЮMoney Версия)!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
