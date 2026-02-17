import asyncio
import os
import re
import csv
import io
from datetime import datetime
from zoneinfo import ZoneInfo

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile

# ===== НАСТРОЙКИ =====
BOT_TOKEN = os.getenv("BOT_TOKEN")

ADMIN_IDS = {587241291}  # ← ВСТАВЬТЕ СЮДА СВОЙ TELEGRAM ID

INSTAGRAM_URL = "https://instagram.com/shkola_phuket"
POSTER_URL = "https://www.instagram.com/p/DUsGiyBku2s/?igsh=YnMxdmFhaTVudGQy"

DB_PATH = "russvet_lottery.db"

TZ = ZoneInfo("Asia/Bangkok")

OPEN_START = datetime(2026, 2, 17, 0, 0, tzinfo=TZ)
OPEN_END   = datetime(2026, 2, 21, 19, 0, tzinfo=TZ)
# =====================


def now():
    return datetime.now(TZ)


def is_open():
    return OPEN_START <= now() <= OPEN_END


def main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подписаться на Instagram", url=INSTAGRAM_URL)],
        [InlineKeyboardButton(text="📍 Афиша мероприятия", url=POSTER_URL)],
        [InlineKeyboardButton(text="▶️ Продолжить", callback_data="go")],
        [InlineKeyboardButton(text="👥 Количество участников", callback_data="count")]
    ])


def consent_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Согласен", callback_data="yes")],
        [InlineKeyboardButton(text="❌ Не согласен", callback_data="no")]
    ])


def email_valid(email):
    return re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email)


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users(
        tg_id INTEGER PRIMARY KEY,
        name TEXT,
        surname TEXT,
        email TEXT,
        email_norm TEXT UNIQUE,
        number INTEGER
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS state(
        tg_id INTEGER PRIMARY KEY,
        step TEXT,
        name TEXT,
        surname TEXT
        )
        """)
        await db.commit()


async def get_number(db):
    cursor = await db.execute("SELECT MAX(number) FROM users")
    row = await cursor.fetchone()
    return (row[0] or 0) + 1


async def run():
    await init_db()

    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()

    @dp.message(Command("start"))
    async def start(msg: Message):

        if not is_open():
            await msg.answer(
                "⛔️ Регистрация закрыта\n\n📍 Программа мероприятия:",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text="Афиша", url=POSTER_URL)]]
                )
            )
            return

        async with aiosqlite.connect(DB_PATH) as db:
            user = await db.execute("SELECT number FROM users WHERE tg_id=?", (msg.from_user.id,))
            user = await user.fetchone()

        if user:
            await msg.answer(f"Ваш номер: {user[0]}")
            return

        await msg.answer(
            "Добро пожаловать в розыгрыш Масленицы от школы «Рассвет» ☀️\n\n"
            "Подпишитесь на Instagram и нажмите «Продолжить».",
            reply_markup=main_kb()
        )

    @dp.callback_query(F.data == "count")
    async def count(call: CallbackQuery):
        async with aiosqlite.connect(DB_PATH) as db:
            c = await db.execute("SELECT COUNT(*) FROM users")
            c = await c.fetchone()
        await call.message.answer(f"Участников: {c[0]}")

    @dp.callback_query(F.data == "go")
    async def go(call: CallbackQuery):
        await call.message.answer(
            "Подтвердите согласие на обработку персональных данных",
            reply_markup=consent_kb()
        )

    @dp.callback_query(F.data == "no")
    async def no(call: CallbackQuery):
        await call.message.answer("Без согласия регистрация невозможна")

    @dp.callback_query(F.data == "yes")
    async def yes(call: CallbackQuery):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("REPLACE INTO state VALUES(?,?,?,?)",
                             (call.from_user.id, "name", None, None))
            await db.commit()
        await call.message.answer("Введите имя")

    @dp.message()
    async def form(msg: Message):

        async with aiosqlite.connect(DB_PATH) as db:
            st = await db.execute("SELECT * FROM state WHERE tg_id=?", (msg.from_user.id,))
            st = await st.fetchone()

            if not st:
                return

            step = st[1]

            if step == "name":
                await db.execute("UPDATE state SET step=?, name=? WHERE tg_id=?",
                                 ("surname", msg.text, msg.from_user.id))
                await db.commit()
                await msg.answer("Введите фамилию")
                return

            if step == "surname":
                await db.execute("UPDATE state SET step=?, surname=? WHERE tg_id=?",
                                 ("email", msg.text, msg.from_user.id))
                await db.commit()
                await msg.answer("Введите email")
                return

            if step == "email":

                if not email_valid(msg.text):
                    await msg.answer("Введите корректный email")
                    return

                email_norm = msg.text.lower()

                check = await db.execute("SELECT * FROM users WHERE email_norm=?", (email_norm,))
                if await check.fetchone():
                    await msg.answer("Этот email уже зарегистрирован")
                    return

                number = await get_number(db)

                await db.execute("""
                INSERT INTO users VALUES(?,?,?,?,?,?)
                """, (
                    msg.from_user.id,
                    st[2],
                    st[3],
                    msg.text,
                    email_norm,
                    number
                ))

                await db.execute("DELETE FROM state WHERE tg_id=?", (msg.from_user.id,))
                await db.commit()

                await msg.answer(f"Ваш номер: {number}")

    @dp.message(Command("stats"))
    async def stats(msg: Message):
        if msg.from_user.id not in ADMIN_IDS:
            return

        async with aiosqlite.connect(DB_PATH) as db:
            c = await db.execute("SELECT COUNT(*) FROM users")
            c = await c.fetchone()

        await msg.answer(f"Участников: {c[0]}")

    @dp.message(Command("export"))
    async def export(msg: Message):
        if msg.from_user.id not in ADMIN_IDS:
            return

        async with aiosqlite.connect(DB_PATH) as db:
            rows = await db.execute("SELECT number,name,surname,email FROM users ORDER BY number")
            rows = await rows.fetchall()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["number", "name", "surname", "email"])
        writer.writerows(rows)

        await msg.answer_document(
            BufferedInputFile(output.getvalue().encode(), filename="russvet_export.csv")
        )

    await dp.start_polling(bot)


asyncio.run(run())
