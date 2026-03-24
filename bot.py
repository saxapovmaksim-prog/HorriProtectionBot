import json
import logging
import os
import re
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

TOKEN = "8768850938:AAGXlxCENVXIqUXAJMBG2bl2xgUwNAJOc4Q"
ADMIN_ID = 2032012311
DATA_FILE = "bot_data.json"

data: Dict = {"groups": {}}
user_messages: Dict[int, List[datetime]] = defaultdict(list)
user_states: Dict[int, str] = {}  # для ожидания ввода ID

# ---------- ЗАГРУЗКА / СОХРАНЕНИЕ ----------
def load_data():
    global data
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except:
            data = {"groups": {}}
    else:
        data = {"groups": {}}
    # Гарантируем структуру
    for cid in data["groups"]:
        g = data["groups"][cid]
        g.setdefault("flood_limit", 5)
        g.setdefault("flood_window", 10)
        g.setdefault("links", True)
        g.setdefault("files", False)

def save_data():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def get_group(chat_id: int) -> dict:
    cid = str(chat_id)
    if cid not in data["groups"]:
        data["groups"][cid] = {
            "flood_limit": 5,
            "flood_window": 10,
            "links": True,
            "files": False
        }
        save_data()
    return data["groups"][cid]

# ---------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ----------
def contains_link(text: str) -> bool:
    return bool(re.search(r'(https?://|t\.me|www\.)\S+', text, re.IGNORECASE))

def is_flood(user_id: int, chat_id: int) -> bool:
    settings = get_group(chat_id)
    now = datetime.now()
    msgs = user_messages[user_id]
    msgs = [m for m in msgs if m > now - timedelta(seconds=settings["flood_window"])]
    msgs.append(now)
    user_messages[user_id] = msgs
    return len(msgs) > settings["flood_limit"]

async def is_admin(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except:
        return False

# ---------- ЗАЩИТА ----------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    msg = update.effective_message

    if not chat or not user or user.is_bot:
        return

    # Обработка ожидания ввода ID группы
    if user.id in user_states:
        state = user_states[user.id]
        if state == "await_group_id":
            try:
                chat_id = int(msg.text.strip())
                await show_group_info(chat_id, msg, context)
            except Exception as e:
                await msg.reply_text(f"❌ Ошибка: {e}")
            del user_states[user.id]
        return

    # Если это группа, создаём настройки по умолчанию при необходимости
    if chat.type in ("group", "supergroup"):
        get_group(chat.id)

    settings = get_group(chat.id)

    # Антифлуд
    if is_flood(user.id, chat.id):
        await msg.delete()
        return

    # Блокировка ссылок
    if msg.text and contains_link(msg.text) and settings["links"]:
        await msg.delete()
        await context.bot.send_message(chat.id, "🔍 Ссылка отправлена на проверку")
        return

    # Блокировка файлов
    if settings["files"] and (msg.document or msg.video or msg.photo):
        await msg.delete()
        await context.bot.send_message(chat.id, "📁 Файл отправлен на проверку")
        return

# ---------- ВЫВОД ИНФОРМАЦИИ О ГРУППЕ ----------
async def show_group_info(chat_id: int, message, context: ContextTypes.DEFAULT_TYPE):
    """Формирует и отправляет информацию о группе."""
    try:
        chat_obj: Chat = await context.bot.get_chat(chat_id)
        admins = await context.bot.get_chat_administrators(chat_id)

        owner = None
        admin_ids = []
        for a in admins:
            if a.status == "creator":
                owner = a.user.id
            else:
                admin_ids.append(a.user.id)

        # Получаем общее количество участников
        try:
            member_count = await context.bot.get_chat_member_count(chat_id)
        except:
            member_count = "неизвестно"

        # Формируем текст
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

        # Если нужно, можно добавить имена, но это требует дополнительных запросов
        await message.reply_text(text, parse_mode="Markdown")

    except Exception as e:
        await message.reply_text(f"❌ Не удалось получить информацию о группе:\n{e}")

# ---------- МЕНЮ ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📋 Мои группы", callback_data="groups")],
        [InlineKeyboardButton("⚙️ Настройки", callback_data="settings")],
    ]
    # Админ-панель только для главного администратора
    if update.effective_user.id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("👑 Админ панель", callback_data="admin")])

    await update.message.reply_text(
        "👋 *Главное меню*\nВыберите действие:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

# ---------- ОБРАБОТЧИК КНОПОК ----------
async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data_cb = query.data
    user = query.from_user

    # ========== СПИСОК ГРУПП ==========
    if data_cb == "groups":
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
        return

    # ========== НАСТРОЙКИ ==========
    if data_cb == "settings":
        keyboard = [
            [InlineKeyboardButton("📋 Группы", callback_data="groups")],
            [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
        ]
        await query.edit_message_text(
            "⚙️ *Настройки*\nВыберите группу для изменения параметров:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return

    # ========== МЕНЮ ГРУППЫ ==========
    if data_cb.startswith("group_"):
        chat_id = int(data_cb.split("_")[1])
        settings = get_group(chat_id)
        text = (
            f"*Группа:* `{chat_id}`\n"
            f"*Антиспам:* {settings['flood_limit']} сообщений за {settings['flood_window']} сек\n"
            f"*Блокировка ссылок:* {'✅' if settings['links'] else '❌'}\n"
            f"*Блокировка файлов:* {'✅' if settings['files'] else '❌'}"
        )
        keyboard = [
            [InlineKeyboardButton("➕ Лимит (+1)", callback_data=f"limit_inc_{chat_id}"),
             InlineKeyboardButton("➖ Лимит (-1)", callback_data=f"limit_dec_{chat_id}")],
            [InlineKeyboardButton("➕ Окно (+5 сек)", callback_data=f"window_inc_{chat_id}"),
             InlineKeyboardButton("➖ Окно (-5 сек)", callback_data=f"window_dec_{chat_id}")],
            [InlineKeyboardButton("🔗 Ссылки: Вкл/Выкл", callback_data=f"toggle_links_{chat_id}")],
            [InlineKeyboardButton("📁 Файлы: Вкл/Выкл", callback_data=f"toggle_files_{chat_id}")],
            [InlineKeyboardButton("🔙 Назад", callback_data="groups")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return

    # ========== ИЗМЕНЕНИЕ НАСТРОЕК ==========
    if data_cb.startswith("limit_inc_"):
        chat_id = int(data_cb.split("_")[2])
        g = get_group(chat_id)
        g["flood_limit"] += 1
        save_data()
        await query.answer(f"Лимит увеличен до {g['flood_limit']}")
        # Обновляем меню
        await buttons(update, context)  # рекурсивно обновим текущее сообщение (пересоздаст кнопки)
        return

    if data_cb.startswith("limit_dec_"):
        chat_id = int(data_cb.split("_")[2])
        g = get_group(chat_id)
        g["flood_limit"] = max(1, g["flood_limit"] - 1)
        save_data()
        await query.answer(f"Лимит уменьшен до {g['flood_limit']}")
        await buttons(update, context)
        return

    if data_cb.startswith("window_inc_"):
        chat_id = int(data_cb.split("_")[2])
        g = get_group(chat_id)
        g["flood_window"] += 5
        save_data()
        await query.answer(f"Окно увеличено до {g['flood_window']} сек")
        await buttons(update, context)
        return

    if data_cb.startswith("window_dec_"):
        chat_id = int(data_cb.split("_")[2])
        g = get_group(chat_id)
        g["flood_window"] = max(1, g["flood_window"] - 5)
        save_data()
        await query.answer(f"Окно уменьшено до {g['flood_window']} сек")
        await buttons(update, context)
        return

    if data_cb.startswith("toggle_links_"):
        chat_id = int(data_cb.split("_")[2])
        g = get_group(chat_id)
        g["links"] = not g["links"]
        save_data()
        await query.answer(f"Ссылки {'включены' if g['links'] else 'выключены'}")
        await buttons(update, context)
        return

    if data_cb.startswith("toggle_files_"):
        chat_id = int(data_cb.split("_")[2])
        g = get_group(chat_id)
        g["files"] = not g["files"]
        save_data()
        await query.answer(f"Файлы {'включены' if g['files'] else 'выключены'}")
        await buttons(update, context)
        return

    # ========== АДМИН-ПАНЕЛЬ ==========
    if data_cb == "admin":
        if user.id != ADMIN_ID:
            await query.edit_message_text("⛔ У вас нет доступа.")
            return
        keyboard = [
            [InlineKeyboardButton("📊 Статистика групп", callback_data="admin_stats")],
            [InlineKeyboardButton("ℹ️ Информация о группе", callback_data="admin_group_info")],
            [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
        ]
        await query.edit_message_text("👑 *Админ-панель*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return

    if data_cb == "admin_stats":
        if user.id != ADMIN_ID:
            return
        text = "*📊 Статистика*\n"
        for cid in data["groups"]:
            try:
                chat = await context.bot.get_chat(int(cid))
                name = chat.title or f"Группа {cid}"
            except:
                name = f"Группа {cid}"
            text += f"• {name} (`{cid}`)\n"
        await query.edit_message_text(text, parse_mode="Markdown")
        return

    if data_cb == "admin_group_info":
        if user.id != ADMIN_ID:
            return
        user_states[user.id] = "await_group_id"
        await query.message.reply_text(
            "🔍 Введите ID группы (например, -1001234567890):\n"
            "*(можно скопировать из списка групп)*"
        )
        await query.edit_message_text("Админ-панель ожидает ввод ID...")
        return

    if data_cb == "main_menu":
        await start(update, context)  # перезапускаем стартовое меню
        return

# ---------- ЗАПУСК ----------
def main():
    logging.basicConfig(level=logging.INFO)
    load_data()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.ALL, handle_message))

    print("✅ Бот запущен")
    app.run_polling()

if __name__ == "__main__":
    main()
