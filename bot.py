import asyncio
import sqlite3
import random
import string
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart

TOKEN = "ТВОЙ_ТОКЕН"

ADMINS = [5135000311, 2032012311]

bot = Bot(token=TOKEN)
dp = Dispatcher()

conn = sqlite3.connect("db.db")
cursor = conn.cursor()

# --- БАЗА ---
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS promocodes (
    code TEXT PRIMARY KEY,
    days INTEGER
)
""")

conn.commit()

# --- КНОПКИ ---

def main_menu(user_id):
    buttons = [
        [InlineKeyboardButton(text="💳 Выбрать тариф", callback_data="tariffs")],
        [InlineKeyboardButton(text="🎁 Ввести промокод", callback_data="promo")]
    ]

    if user_id in ADMINS:
        buttons.append([InlineKeyboardButton(text="⚙️ Админ панель", callback_data="admin")])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def tariffs_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 месяц — 99₽", callback_data="t1")],
        [InlineKeyboardButton(text="3 месяца — 299₽", callback_data="t2")],
        [InlineKeyboardButton(text="12 месяцев — 600₽", callback_data="t3")]
    ])


def admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать промокод", callback_data="create_promo")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")]
    ])


def stats_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="users")],
        [InlineKeyboardButton(text="🎁 Промокоды", callback_data="promos")]
    ])


# --- START ---

@dp.message(CommandStart())
async def start(message: Message):
    cursor.execute("INSERT OR IGNORE INTO users VALUES (?)", (message.from_user.id,))
    conn.commit()

    await message.answer("👋 Добро пожаловать!", reply_markup=main_menu(message.from_user.id))


# --- ТАРИФЫ ---

@dp.callback_query(F.data == "tariffs")
async def tariffs(call: CallbackQuery):
    await call.message.edit_text("💳 Выбери тариф:", reply_markup=tariffs_menu())


@dp.callback_query(F.data.in_(["t1", "t2", "t3"]))
async def buy(call: CallbackQuery):
    texts = {
        "t1": "1 месяц за 99₽",
        "t2": "3 месяца за 299₽",
        "t3": "12 месяцев за 600₽"
    }
    await call.message.answer(f"Вы выбрали тариф: {texts[call.data]}\n(здесь будет оплата)")


# --- ПРОМОКОД ВВОД ---

user_state = {}

@dp.callback_query(F.data == "promo")
async def enter_promo(call: CallbackQuery):
    user_state[call.from_user.id] = "enter_promo"
    await call.message.answer("Введи промокод:")


@dp.message()
async def handle_text(message: Message):
    state = user_state.get(message.from_user.id)

    # ввод промокода
    if state == "enter_promo":
        code = message.text.strip()

        cursor.execute("SELECT days FROM promocodes WHERE code=?", (code,))
        res = cursor.fetchone()

        if res:
            await message.answer(f"✅ Промокод активирован! Дней: {res[0]}")
        else:
            await message.answer("❌ Неверный промокод")

        user_state.pop(message.from_user.id, None)

    # создание промокода
    elif state == "create_promo":
        try:
            days = int(message.text)

            # генерация уникального кода
            while True:
                code = ''.join(random.choices(string.ascii_letters + string.digits, k=10))
                cursor.execute("SELECT * FROM promocodes WHERE code=?", (code,))
                if not cursor.fetchone():
                    break

            cursor.execute("INSERT INTO promocodes VALUES (?, ?)", (code, days))
            conn.commit()

            await message.answer(f"✅ Промокод создан:\n{code}\nДней: {days}")
            user_state.pop(message.from_user.id, None)

        except:
            await message.answer("❌ Введи число (дни)")


# --- АДМИНКА ---

@dp.callback_query(F.data == "admin")
async def admin(call: CallbackQuery):
    if call.from_user.id not in ADMINS:
        return
    await call.message.edit_text("⚙️ Админ панель", reply_markup=admin_menu())


@dp.callback_query(F.data == "create_promo")
async def create_promo(call: CallbackQuery):
    if call.from_user.id not in ADMINS:
        return

    user_state[call.from_user.id] = "create_promo"
    await call.message.answer("Введи количество дней для промокода:")


# --- СТАТИСТИКА ---

@dp.callback_query(F.data == "stats")
async def stats(call: CallbackQuery):
    await call.message.edit_text("📊 Статистика", reply_markup=stats_menu())


@dp.callback_query(F.data == "users")
async def users(call: CallbackQuery):
    cursor.execute("SELECT COUNT(*) FROM users")
    count = cursor.fetchone()[0]

    await call.message.answer(f"👥 Всего пользователей: {count}")


@dp.callback_query(F.data == "promos")
async def promos(call: CallbackQuery):
    cursor.execute("SELECT code, days FROM promocodes")
    data = cursor.fetchall()

    if not data:
        await call.message.answer("Нет промокодов")
        return

    text = "🎁 Промокоды:\n\n"
    for code, days in data:
        text += f"{code} — {days} дней\n"

    await call.message.answer(text)


# --- ЗАПУСК ---

async def main():
    print("BOT STARTED")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())