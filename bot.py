import asyncio
import json
import logging
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set

from telegram import Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
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

# ---------- ЗАГРУЗКА / СОХРАНЕНИЕ ДАННЫХ ----------
def load_data():
    global data
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Убедимся, что админ в списке
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

# Получить настройки группы
def get_group_settings(chat_id: int) -> Dict:
    chat_id = str(chat_id)
    if chat_id not in data["groups"]:
        # Инициализация настроек по умолчанию
        settings = DEFAULT_SETTINGS.copy()
        data["groups"][chat_id] = settings
        save_data()
        return settings
    return data["groups"][chat_id]

# Сохранить настройки группы
def set_group_settings(chat_id: int, settings: Dict):
    data["groups"][str(chat_id)] = settings
    save_data()

# Обновить отдельные параметры
def update_group_setting(chat_id: int, key: str, value):
    settings = get_group_settings(chat_id)
    settings[key] = value
    set_group_settings(chat_id, settings)

# ---------- ЗАГРУЗКА ЗАПРЕЩЁННЫХ СЛОВ (пока не используется в этом варианте, оставим заглушку) ----------
def load_bad_words():
    # Позже добавим
    pass

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
        # Уведомление в чате
        await context.bot.send_message(
            chat_id,
            f"🚫 Пользователь {user_id} получил ограничение на {duration} сек.\nПричина: {reason}"
        )
        # Увеличить статистику нарушений
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

    # Игнорируем ботов
    if user.is_bot:
        return

    # Получаем настройки для этого чата
    settings = get_group_settings(chat.id)
    tariff = settings["tariff"]

    # Увеличиваем счётчик сообщений
    settings["stats"]["messages"] += 1
    set_group_settings(chat.id, settings)

    # Проверка прав бота
    try:
        bot_member = await chat.get_member(context.bot.id)
        if not bot_member.can_restrict_members:
            await message.reply_text("❌ У бота нет прав на ограничение пользователей.")
            return
    except:
        return

    # 1. Антифлуд
    if is_flooding(user.id, chat.id):
        mute_duration = settings.get("flood_mute", 60)
        await restrict_user(chat.id, user.id, mute_duration, "Флуд", context)
        try:
            await message.delete()
        except:
            pass
        return

    # 2. Блокировка ссылок (если включена)
    if settings.get("block_links", True) and message.text and contains_link(message.text):
        await restrict_user(chat.id, user.id, 60, "Запрещённые ссылки", context)
        try:
            await message.delete()
        except:
            pass
        return

    # 3. Блокировка медиа (если включена)
    if settings.get("block_media", True):
        media_types = (message.photo, message.video, message.document, message.voice, message.audio, message.animation, message.sticker)
        if any(media_types):
            await restrict_user(chat.id, user.id, 60, "Медиафайлы запрещены", context)
            try:
                await message.delete()
            except:
                pass
            return

    # 4. Блокировка запрещённых слов (если включена)
    if settings.get("block_bad_words", False) and message.text and contains_bad_words(message.text):
        await restrict_user(chat.id, user.id, 60, "Запрещённое слово", context)
        try:
            await message.delete()
        except:
            pass
        return

    # 5. Отправка на проверку ссылок/файлов (заглушка)
    if settings.get("check_links", False) and message.text and contains_link(message.text):
        # TODO: отправить ссылку на проверку модератору
        pass

    if settings.get("check_files", False) and message.document:
        # TODO: отправить файл на проверку
        pass

    if settings.get("check_content", False):
        # TODO: расширенная проверка контента
        pass

def contains_bad_words(text: str) -> bool:
    # Временно заглушка
    return False

# ---------- НОВЫЕ УЧАСТНИКИ (ПРИВЕТСТВИЕ) ----------
async def handle_new_chat_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    for member in update.message.new_chat_members:
        if member.id == context.bot.id:
            # Бот добавлен, инициализируем настройки
            get_group_settings(chat.id)  # создаст запись, если нет
            await update.message.reply_text(
                "🤖 *Бот-защитник активирован!*\n\n"
                "Для настройки напишите мне в личные сообщения /menu и выберите эту группу.\n"
                "По умолчанию активен бесплатный тариф.",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            # Приветствие нового участника (если включено)
            settings = get_group_settings(chat.id)
            if settings.get("custom_welcome"):
                welcome_text = settings["custom_welcome"] or f"Добро пожаловать, {member.full_name}!"
                await update.message.reply_text(welcome_text)
            else:
                await update.message.reply_text(f"Добро пожаловать, {member.full_name}!")

# ---------- КОМАНДЫ В ЛИЧНЫХ СООБЩЕНИЯХ ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветствие и главное меню."""
    await update.message.reply_text(
        "👋 Привет! Я бот-защитник чатов.\n"
        "Добавьте меня в группу и назначьте администратором.\n\n"
        "Используйте /menu для управления моими группами и настройками."
    )

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает список групп, где бот добавлен, и меню управления."""
    user_id = update.effective_user.id
    # Получаем все группы, где бот есть (из данных)
    groups = data["groups"]
    if not groups:
        await update.message.reply_text("Вы ещё не добавили меня ни в одну группу.\nДобавьте бота в группу и назначьте администратором.")
        return

    # Создаём кнопки для каждой группы
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

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки."""
    query = update.callback_query
    await query.answer()
    data_cb = query.data

    if data_cb == "close":
        await query.edit_message_text("Меню закрыто.")
        return

    if data_cb.startswith("group_"):
        chat_id = data_cb.split("_")[1]
        context.user_data["current_group"] = chat_id
        await show_group_menu(query, chat_id)
        return

    # Обработка других действий (настройки, выбор тарифа и т.д.)
    if data_cb.startswith("tariff_"):
        tariff = data_cb.split("_")[1]
        chat_id = context.user_data.get("current_group")
        if chat_id:
            # Обновить тариф и применить настройки из TARIFF_FEATURES
            new_settings = get_group_settings(int(chat_id))
            new_settings["tariff"] = tariff
            # Применяем настройки по умолчанию для тарифа
            for key, value in TARIFF_FEATURES[tariff].items():
                new_settings[key] = value
            set_group_settings(int(chat_id), new_settings)
            await query.edit_message_text(f"✅ Тариф изменён на {tariff.upper()}")
            await asyncio.sleep(2)
            await show_group_menu(query, chat_id)
        return

    if data_cb.startswith("setflood_"):
        # Запрос на ввод лимита и окна
        chat_id = context.user_data.get("current_group")
        if chat_id:
            context.user_data["flood_setting"] = chat_id
            await query.edit_message_text(
                "Введите лимит сообщений и окно в секундах через пробел.\nПример: `5 10`",
                parse_mode=ParseMode.MARKDOWN
            )
            return ConversationHandler.END  # мы не используем ConversationHandler, просто сохраняем состояние
    # Добавим простую реализацию через ожидание следующего сообщения (используем следующий обработчик)

async def show_group_menu(query, chat_id):
    """Отображает меню настройки группы."""
    settings = get_group_settings(int(chat_id))
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
        [InlineKeyboardButton("Сменить тариф", callback_data="choose_tariff")],
        [InlineKeyboardButton("Настроить антиспам", callback_data="configure_flood")],
        [InlineKeyboardButton("Вкл/Выкл ссылки", callback_data="toggle_links")],
        [InlineKeyboardButton("Вкл/Выкл медиа", callback_data="toggle_media")],
        [InlineKeyboardButton("Кастомное приветствие", callback_data="set_welcome")],
        [InlineKeyboardButton("Назад", callback_data="back_to_groups")],
    ]
    # Добавляем кнопки для выбора тарифа, если нажата choose_tariff
    if query.data == "choose_tariff":
        tariff_buttons = [
            [InlineKeyboardButton("Бесплатный", callback_data="tariff_free")],
            [InlineKeyboardButton("Стандартный", callback_data="tariff_standard")],
            [InlineKeyboardButton("Профессиональный", callback_data="tariff_pro")],
            [InlineKeyboardButton("Назад", callback_data="group_" + chat_id)]
        ]
        await query.edit_message_text("Выберите тариф:", reply_markup=InlineKeyboardMarkup(tariff_buttons))
        return

    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))

# ---------- АДМИН-ПАНЕЛЬ ----------
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in data.get("admins", []):
        await update.message.reply_text("⛔ У вас нет доступа к админ-панели.")
        return

    # Показать меню админа
    text = "🔧 *Админ-панель*\n\n"
    text += f"Всего групп: {len(data['groups'])}\n"
    text += f"Администраторов бота: {len(data['admins'])}"
    keyboard = [
        [InlineKeyboardButton("Список групп", callback_data="admin_groups")],
        [InlineKeyboardButton("Управление админами", callback_data="admin_admins")],
        [InlineKeyboardButton("Статистика", callback_data="admin_stats")],
    ]
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    if user_id not in data.get("admins", []):
        await query.edit_message_text("⛔ Доступ запрещён.")
        return

    if query.data == "admin_groups":
        # Список всех групп
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
    elif query.data == "admin_admins":
        # Управление админами: показать список и кнопку добавить
        admins = data["admins"]
        text = "*Администраторы бота:*\n"
        for aid in admins:
            text += f"- {aid}\n"
        text += "\nДля добавления введите /addadmin <id>\nДля удаления /deladmin <id>"
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
    elif query.data == "admin_stats":
        total_messages = sum(s["stats"]["messages"] for s in data["groups"].values())
        total_violations = sum(s["stats"]["violations"] for s in data["groups"].values())
        text = f"*Общая статистика*\nСообщений: {total_messages}\nНарушений: {total_violations}"
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)

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
    load_bad_words()

    application = Application.builder().token(TOKEN).build()

    # Обработчики сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, handle_message))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_chat_members))

    # Команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", menu))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("addadmin", add_admin))
    application.add_handler(CommandHandler("deladmin", del_admin))

    # Обработчики кнопок
    application.add_handler(CallbackQueryHandler(button_callback, pattern="^(group_|tariff_|choose_tariff|configure_flood|toggle_links|toggle_media|set_welcome|back_to_groups|close)"))
    application.add_handler(CallbackQueryHandler(admin_callback, pattern="^(admin_groups|admin_admins|admin_stats)"))

    application.run_polling()

if __name__ == "__main__":
    main()
