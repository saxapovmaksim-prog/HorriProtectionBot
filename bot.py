import asyncio
import json
import logging
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Set, Optional

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
DATA_FILE = "bot_data.json"
ADMIN_ID = 2032012311  # Главный админ

# Настройки по умолчанию для групп
DEFAULT_SETTINGS = {
    "tariff": "free",           # free, standard, pro
    "flood_limit": 5,           # сообщений
    "flood_window": 10,         # секунд
    "flood_mute": 60,           # секунд мута
    "block_links": True,        # блокировать ссылки
    "block_media": True,        # блокировать медиа
    "block_bad_words": False,   # блокировать запрещённые слова
    "custom_welcome": None,     # кастомное приветствие (для standard/pro)
    "check_links": True,        # отправлять ссылки на проверку (флаг)
    "check_files": False,       # отправлять файлы на проверку
    "check_content": False,     # проверка контента (pro)
    "stats": {
        "messages": 0,
        "violations": 0
    }
}

# Тарифы и их возможности
TARIFF_FEATURES = {
    "free": {
        "block_links": True,
        "block_media": False,
        "block_bad_words": False,
        "custom_welcome": False,
        "check_links": True,
        "check_files": False,
        "check_content": False
    },
    "standard": {
        "block_links": True,
        "block_media": True,
        "block_bad_words": False,
        "custom_welcome": True,
        "check_links": True,
        "check_files": True,
        "check_content": False
    },
    "pro": {
        "block_links": True,
        "block_media": True,
        "block_bad_words": True,
        "custom_welcome": True,
        "check_links": True,
        "check_files": True,
        "check_content": True
    }
}

# ---------- ГЛОБАЛЬНЫЕ ДАННЫЕ ----------
data: Dict = {"groups": {}, "admins": [ADMIN_ID]}
user_messages: Dict[int, List[datetime]] = defaultdict(list)  # для антифлуда
bad_words: Set[str] = set()

# Для временного хранения состояния настройки антиспама
flood_setting_users: Dict[int, Dict] = {}  # {user_id: {"chat_id": int, "step": str}}

# ---------- ЗАГРУЗКА / СОХРАНЕНИЕ ДАННЫХ ----------
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
        return settings
    return data["groups"][chat_id_str]

def set_group_settings(chat_id: int, settings: Dict):
    data["groups"][str(chat_id)] = settings
    save_data()

def update_group_setting(chat_id: int, key: str, value):
    settings = get_group_settings(chat_id)
    settings[key] = value
    set_group_settings(chat_id, settings)

# ---------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ ПРОВЕРОК ----------
def contains_link(text: str) -> bool:
    url_pattern = re.compile(r'(https?://|www\.)\S+', re.IGNORECASE)
    return bool(url_pattern.search(text))

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

async def restrict_user(chat_id: int, user_id: int, duration: int, reason: str, context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.bot.restrict_chat_member(
            chat_id,
            user_id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=datetime.now() + timedelta(seconds=duration)
        )
        await context.bot.send_message(
            chat_id,
            f"🚫 Пользователь {user_id} получил ограничение на {duration} сек.\nПричина: {reason}"
        )
        settings = get_group_settings(chat_id)
        settings["stats"]["violations"] += 1
        set_group_settings(chat_id, settings)
    except Exception as e:
        logging.error(f"Не удалось ограничить пользователя {user_id} в чате {chat_id}: {e}")

# ---------- ОБРАБОТЧИК СООБЩЕНИЙ (ЗАЩИТА) ----------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not update.effective_user:
        return
    chat = update.effective_chat
    user = update.effective_user
    message = update.effective_message

    if user.is_bot:
        return

    settings = get_group_settings(chat.id)
    tariff = settings["tariff"]

    # Обновляем статистику сообщений
    settings["stats"]["messages"] += 1
    set_group_settings(chat.id, settings)

    try:
        bot_member = await chat.get_member(context.bot.id)
        if not bot_member.can_restrict_members:
            await message.reply_text("❌ У бота нет прав на ограничение пользователей.")
            return
    except:
        return

    # Антифлуд
    if is_flooding(user.id, chat.id):
        mute_duration = settings.get("flood_mute", 60)
        await restrict_user(chat.id, user.id, mute_duration, "Флуд", context)
        try:
            await message.delete()
        except:
            pass
        return

    # Блокировка ссылок
    if settings.get("block_links", True) and message.text and contains_link(message.text):
        await restrict_user(chat.id, user.id, 60, "Запрещённые ссылки", context)
        try:
            await message.delete()
        except:
            pass
        return

    # Блокировка медиа
    if settings.get("block_media", True):
        if any((message.photo, message.video, message.document, message.voice,
                message.audio, message.animation, message.sticker)):
            await restrict_user(chat.id, user.id, 60, "Медиафайлы запрещены", context)
            try:
                await message.delete()
            except:
                pass
            return

    # Блокировка запрещённых слов (заглушка)
    if settings.get("block_bad_words", False) and message.text:
        # TODO: реализовать проверку слов
        pass

    # Проверка ссылок/файлов (заглушки)
    if settings.get("check_links", False) and message.text and contains_link(message.text):
        # Отправить модератору
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
            get_group_settings(chat.id)  # инициализация
            await update.message.reply_text(
                "🤖 *Бот-защитник активирован!*\n\n"
                "Для настройки напишите мне в личные сообщения /menu и выберите эту группу.\n"
                "По умолчанию активен бесплатный тариф.",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            settings = get_group_settings(chat.id)
            if settings.get("custom_welcome"):
                welcome = settings["custom_welcome"] or f"Добро пожаловать, {member.full_name}!"
                await update.message.reply_text(welcome)
            else:
                await update.message.reply_text(f"Добро пожаловать, {member.full_name}!")

# ---------- КОМАНДЫ В ЛИЧНЫХ СООБЩЕНИЯХ ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот-защитник чатов.\n"
        "Добавьте меня в группу и назначьте администратором.\n\n"
        "Используйте /menu для управления моими группами и настройками."
    )

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    groups = data["groups"]
    if not groups:
        await update.message.reply_text("Вы ещё не добавили меня ни в одну группу.\nДобавьте бота в группу и назначьте администратором.")
        return

    keyboard = []
    for chat_id_str, settings in groups.items():
        try:
            chat = await context.bot.get_chat(int(chat_id_str))
            title = chat.title or f"Группа {chat_id_str}"
        except:
            title = f"Группа {chat_id_str}"
        keyboard.append([InlineKeyboardButton(title, callback_data=f"group_{chat_id_str}")])
    keyboard.append([InlineKeyboardButton("❌ Закрыть", callback_data="close")])
    await update.message.reply_text(
        "Выберите группу для настройки:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_group_menu(query, chat_id_int: int):
    """Отображает меню настройки конкретной группы."""
    settings = get_group_settings(chat_id_int)
    tariff = settings["tariff"]
    text = (
        f"*Группа:* {chat_id_int}\n"
        f"*Тариф:* {tariff.upper()}\n"
        f"*Антиспам:* {settings['flood_limit']} сообщ. за {settings['flood_window']} сек → мут {settings['flood_mute']} сек\n"
        f"*Блокировка ссылок:* {'✅' if settings['block_links'] else '❌'}\n"
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
        [InlineKeyboardButton("Вкл/Выкл медиа", callback_data=f"toggle_media_{chat_id_int}")],
        [InlineKeyboardButton("Кастомное приветствие", callback_data=f"set_welcome_{chat_id_int}")],
        [InlineKeyboardButton("Назад", callback_data="back_to_groups")],
    ]
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик всех инлайн-кнопок (не админских)."""
    query = update.callback_query
    await query.answer()
    data_cb = query.data
    user_id = update.effective_user.id

    # Закрыть меню
    if data_cb == "close":
        await query.edit_message_text("Меню закрыто.")
        return

    # Назад к списку групп
    if data_cb == "back_to_groups":
        groups = data["groups"]
        if not groups:
            await query.edit_message_text("Нет доступных групп.")
            return
        keyboard = []
        for chat_id_str, settings in groups.items():
            try:
                chat = await context.bot.get_chat(int(chat_id_str))
                title = chat.title or f"Группа {chat_id_str}"
            except:
                title = f"Группа {chat_id_str}"
            keyboard.append([InlineKeyboardButton(title, callback_data=f"group_{chat_id_str}")])
        keyboard.append([InlineKeyboardButton("❌ Закрыть", callback_data="close")])
        await query.edit_message_text("Выберите группу:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # Выбор группы
    if data_cb.startswith("group_"):
        chat_id_str = data_cb.split("_")[1]
        context.user_data["current_group"] = chat_id_str
        await show_group_menu(query, int(chat_id_str))
        return

    # Смена тарифа
    if data_cb.startswith("choose_tariff_"):
        chat_id = int(data_cb.split("_")[2])
        tariff_buttons = [
            [InlineKeyboardButton("Бесплатный", callback_data=f"tariff_free_{chat_id}")],
            [InlineKeyboardButton("Стандартный", callback_data=f"tariff_standard_{chat_id}")],
            [InlineKeyboardButton("Профессиональный", callback_data=f"tariff_pro_{chat_id}")],
            [InlineKeyboardButton("Назад", callback_data=f"group_{chat_id}")],
        ]
        await query.edit_message_text("Выберите тариф:", reply_markup=InlineKeyboardMarkup(tariff_buttons))
        return

    if data_cb.startswith("tariff_"):
        parts = data_cb.split("_")
        tariff = parts[1]
        chat_id = int(parts[2])
        # Применяем тариф и его настройки
        new_settings = get_group_settings(chat_id)
        new_settings["tariff"] = tariff
        for key, value in TARIFF_FEATURES[tariff].items():
            new_settings[key] = value
        set_group_settings(chat_id, new_settings)
        await query.edit_message_text(f"✅ Тариф изменён на {tariff.upper()}")
        await asyncio.sleep(1)
        await show_group_menu(query, chat_id)
        return

    # Переключение блокировки ссылок
    if data_cb.startswith("toggle_links_"):
        chat_id = int(data_cb.split("_")[2])
        settings = get_group_settings(chat_id)
        new_val = not settings["block_links"]
        update_group_setting(chat_id, "block_links", new_val)
        await show_group_menu(query, chat_id)
        return

    # Переключение блокировки медиа
    if data_cb.startswith("toggle_media_"):
        chat_id = int(data_cb.split("_")[2])
        settings = get_group_settings(chat_id)
        new_val = not settings["block_media"]
        update_group_setting(chat_id, "block_media", new_val)
        await show_group_menu(query, chat_id)
        return

    # Кастомное приветствие
    if data_cb.startswith("set_welcome_"):
        chat_id = int(data_cb.split("_")[2])
        context.user_data["welcome_chat"] = chat_id
        await query.edit_message_text("Введите текст приветствия (или отправьте пустое сообщение для отключения):")
        # Переходим в режим ожидания ввода текста
        return

    # Настройка антиспама
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

    # Ввод параметров антиспама обрабатывается в handle_text

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений (не команд) для диалогов."""
    user_id = update.effective_user.id
    message = update.message

    # Если ожидаем ввод приветствия
    if "welcome_chat" in context.user_data:
        chat_id = context.user_data.pop("welcome_chat")
        text = message.text.strip()
        if text:
            update_group_setting(chat_id, "custom_welcome", text)
            await message.reply_text("✅ Кастомное приветствие сохранено.")
        else:
            update_group_setting(chat_id, "custom_welcome", None)
            await message.reply_text("✅ Кастомное приветствие отключено.")
        # Показываем меню для этой группы
        await show_group_menu_from_user(update, chat_id, context)
        return

    # Если ожидаем ввод параметров антиспама
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
            limit = int(parts[0])
            window = int(parts[1])
            mute = int(parts[2])
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
    """Показывает меню группы в ответ на сообщение пользователя (не через callback)."""
    settings = get_group_settings(chat_id)
    tariff = settings["tariff"]
    text = (
        f"*Группа:* {chat_id}\n"
        f"*Тариф:* {tariff.upper()}\n"
        f"*Антиспам:* {settings['flood_limit']} сообщ. за {settings['flood_window']} сек → мут {settings['flood_mute']} сек\n"
        f"*Блокировка ссылок:* {'✅' if settings['block_links'] else '❌'}\n"
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
        [InlineKeyboardButton("Вкл/Выкл медиа", callback_data=f"toggle_media_{chat_id}")],
        [InlineKeyboardButton("Кастомное приветствие", callback_data=f"set_welcome_{chat_id}")],
        [InlineKeyboardButton("Назад", callback_data="back_to_groups")],
    ]
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))

# ---------- АДМИН-ПАНЕЛЬ ----------
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in data.get("admins", []):
        await update.message.reply_text("⛔ У вас нет доступа к админ-панели.")
        return

    text = "🔧 *Админ-панель*\n\n"
    text += f"Всего групп: {len(data['groups'])}\n"
    text += f"Администраторов бота: {len(data['admins'])}"
    keyboard = [
        [InlineKeyboardButton("Список групп", callback_data="admin_groups")],
        [InlineKeyboardButton("Управление админами", callback_data="admin_admins")],
        [InlineKeyboardButton("Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton("❌ Закрыть", callback_data="close_admin")],
    ]
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    if user_id not in data.get("admins", []):
        await query.edit_message_text("⛔ Доступ запрещён.")
        return

    if query.data == "close_admin":
        await query.edit_message_text("Админ-панель закрыта.")
        return

    if query.data == "admin_groups":
        groups = data["groups"]
        if not groups:
            await query.edit_message_text("Нет групп.")
            return
        text = "*Список групп:*\n"
        for chat_id_str, s in groups.items():
            try:
                chat = await context.bot.get_chat(int(chat_id_str))
                title = chat.title or "Без названия"
            except:
                title = "Недоступно"
            text += f"{title} (ID: {chat_id_str}) – тариф {s['tariff']}\n"
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        return

    if query.data == "admin_admins":
        admins = data["admins"]
        text = "*Администраторы бота:*\n"
        for aid in admins:
            text += f"- {aid}\n"
        text += "\nДля добавления введите /addadmin <id>\nДля удаления /deladmin <id>"
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        return

    if query.data == "admin_stats":
        total_messages = sum(s["stats"]["messages"] for s in data["groups"].values())
        total_violations = sum(s["stats"]["violations"] for s in data["groups"].values())
        text = f"*Общая статистика*\nСообщений: {total_messages}\nНарушений: {total_violations}"
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        return

async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
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
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
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

# ---------- ЗАПУСК БОТА ----------
def main():
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO
    )
    load_data()

    application = Application.builder().token(TOKEN).build()

    # Обработчики сообщений (защита)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, handle_message))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_chat_members))

    # Обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", menu))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("addadmin", add_admin))
    application.add_handler(CommandHandler("deladmin", del_admin))

    # Обработчики кнопок и текстовых диалогов
    application.add_handler(CallbackQueryHandler(button_callback, pattern="^(group_|choose_tariff_|tariff_|toggle_links_|toggle_media_|set_welcome_|configure_flood_|back_to_groups|close)"))
    application.add_handler(CallbackQueryHandler(admin_callback, pattern="^(admin_groups|admin_admins|admin_stats|close_admin)"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    application.run_polling()

if __name__ == "__main__":
    main()
