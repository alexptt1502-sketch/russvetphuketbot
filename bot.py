import asyncio
import os
import re
import csv
import io
import random
from datetime import datetime
from zoneinfo import ZoneInfo

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile
)

# ========= НАСТРОЙКИ =========
BOT_TOKEN = os.getenv("BOT_TOKEN")  # токен хранится в Railway Variables

# ВАЖНО: укажите свой Telegram ID (узнать через @userinfobot)
# Можно несколько: {111, 222}
ADMIN_IDS = {587241291}

INSTAGRAM_URL = "https://instagram.com/shkola_phuket"
POSTER_URL = "https://www.instagram.com/p/DUsGiyBku2s/?igsh=YnMxdmFhaTVudGQy"

DB_PATH = "russvet_lottery.db"

TZ = ZoneInfo("Asia/Bangkok")
OPEN_START = datetime(2026, 2, 17, 0, 0, tzinfo=TZ)
OPEN_END   = datetime(2026, 2, 21, 19, 0, tzinfo=TZ)
# ==============================


def now() -> datetime:
    return datetime.now(TZ)


def email_valid(email: str) -> bool:
    return re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email or "") is not None


def main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подписаться на Instagram", url=INSTAGRAM_URL)],
        [InlineKeyboardButton(text="📍 Афиша мероприятия", url=POSTER_URL)],
        [InlineKeyboardButton(text="▶️ Продолжить", callback_data="go")],
        [InlineKeyboardButton(text="👥 Количество участников", callback_data="count")]
    ])


def consent_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Согласен", callback_data="yes")],
        [InlineKeyboardButton(text="❌ Не согласен", callback_data="no")]
    ])


def admin_panel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="adm:stats")],
        [InlineKeyboardButton(text="📁 Выгрузить CSV", callback_data="adm:export")],
        [InlineKeyboardButton(text="🎲 Победитель", callback_data="adm:winner:1"),
         InlineKeyboardButton(text="🎲 x3", callback_data="adm:winner:3")],
        [InlineKeyboardButton(text="⛔ Закрыть регистрацию", callback_data="adm:close"),
         InlineKeyboardButton(text="✅ Открыть регистрацию", callback_data="adm:open")],
    ])


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users(
            tg_id INTEGER PRIMARY KEY,
            name TEXT,
            surname TEXT,
            email TEXT,
            email_norm TEXT UNIQUE,
            number INTEGER,
            created_at TEXT
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
        await db.execute("""
        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """)
        # значение по умолчанию: регистрация включена
        await db.execute("""
        INSERT OR IGNORE INTO settings(key, value) VALUES('registration_override', 'auto')
        """)
        await db.commit()


async def get_setting(db, key: str, default: str = "") -> str:
    cur = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = await cur.fetchone()
    return row[0] if row else default


async def set_setting(db, key: str, value: str):
    await db.execute("INSERT INTO settings(key, value) VALUES(?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    await db.commit()


async def is_registration_open(db) -> bool:
    """
    registration_override:
      - 'auto'  -> по датам OPEN_START..OPEN_END
      - 'open'  -> принудительно открыто
      - 'closed'-> принудительно закрыто
    """
    override = await get_setting(db, "registration_override", "auto")
    if override == "open":
        return True
    if override == "closed":
        return False
    return OPEN_START <= now() <= OPEN_END


async def get_next_number(db) -> int:
    cur = await db.execute("SELECT MAX(number) FROM users")
    row = await cur.fetchone()
    return (row[0] or 0) + 1


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def users_count(db) -> int:
    cur = await db.execute("SELECT COUNT(*) FROM users")
    row = await cur.fetchone()
    return int(row[0])


async def pick_winners(db, n: int):
    cur = await db.execute("SELECT number, name, surname, email, tg_id FROM users ORDER BY number")
    rows = await cur.fetchall()
    if not rows:
        return []
    n = max(1, min(n, len(rows)))
    return random.sample(rows, n)


async def clear_user_state(db, tg_id: int):
    await db.execute("DELETE FROM state WHERE tg_id=?", (tg_id,))
    await db.commit()


async def run():
    await init_db()

    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()

    # ====== КОМАНДЫ, КОТОРЫЕ ДОЛЖНЫ РАБОТАТЬ ВСЕГДА ======

    @dp.message(Command("cancel"))
    async def cancel(msg: Message):
        async with aiosqlite.connect(DB_PATH) as db:
            await clear_user_state(db, msg.from_user.id)
        await msg.answer("❌ Анкета сброшена. Нажмите /start чтобы начать заново.")

    @dp.message(Command("panel"))
    async def panel(msg: Message):
        if not is_admin(msg.from_user.id):
            return
        await msg.answer("👑 Админ-панель", reply_markup=admin_panel_kb())

    @dp.message(Command("stats"))
    async def stats(msg: Message):
        if not is_admin(msg.from_user.id):
            return
        async with aiosqlite.connect(DB_PATH) as db:
            c = await users_count(db)
            override = await get_setting(db, "registration_override", "auto")
        await msg.answer(f"📊 Участников: {c}\nРежим регистрации: {override}")

    @dp.message(Command("export"))
    async def export(msg: Message):
        if not is_admin(msg.from_user.id):
            return
        async with aiosqlite.connect(DB_PATH) as db:
            rows_cur = await db.execute("""
                SELECT number, name, surname, email, created_at, tg_id
                FROM users ORDER BY number
            """)
            rows = await rows_cur.fetchall()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["number", "name", "surname", "email", "created_at", "tg_id"])
        writer.writerows(rows)

        await msg.answer_document(
            BufferedInputFile(output.getvalue().encode("utf-8"), filename="russvet_export.csv")
        )

    @dp.message(Command("winner"))
    async def winner(msg: Message):
        if not is_admin(msg.from_user.id):
            return
        async with aiosqlite.connect(DB_PATH) as db:
            winners = await pick_winners(db, 1)
        if not winners:
            await msg.answer("Пока нет участников.")
            return
        number, name, surname, email, tg_id = winners[0]
        await msg.answer(f"🏆 Победитель:\n№{number} — {name} {surname}\nEmail: {email}")

    @dp.message(Command("winners"))
    async def winners(msg: Message):
        if not is_admin(msg.from_user.id):
            return
        # /winners 3
        parts = (msg.text or "").split()
        n = 3
        if len(parts) >= 2:
            try:
                n = int(parts[1])
            except:
                n = 3

        async with aiosqlite.connect(DB_PATH) as db:
            winners_list = await pick_winners(db, n)

        if not winners_list:
            await msg.answer("Пока нет участников.")
            return

        lines = []
        for i, (number, name, surname, email, tg_id) in enumerate(winners_list, start=1):
            lines.append(f"{i}) №{number} — {name} {surname} ({email})")
        await msg.answer("🎉 Победители:\n" + "\n".join(lines))

    @dp.message(Command("close"))
    async def close_reg(msg: Message):
        if not is_admin(msg.from_user.id):
            return
        async with aiosqlite.connect(DB_PATH) as db:
            await set_setting(db, "registration_override", "closed")
        await msg.answer("⛔ Регистрация принудительно закрыта.")

    @dp.message(Command("open"))
    async def open_reg(msg: Message):
        if not is_admin(msg.from_user.id):
            return
        async with aiosqlite.connect(DB_PATH) as db:
            await set_setting(db, "registration_override", "open")
        await msg.answer("✅ Регистрация принудительно открыта.")

    @dp.message(Command("auto"))
    async def auto_reg(msg: Message):
        if not is_admin(msg.from_user.id):
            return
        async with aiosqlite.connect(DB_PATH) as db:
            await set_setting(db, "registration_override", "auto")
        await msg.answer("🕒 Регистрация по расписанию (auto).")

    # ====== CALLBACKS АДМИН-ПАНЕЛИ ======

    @dp.callback_query(F.data == "adm:stats")
    async def adm_stats(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            return
        async with aiosqlite.connect(DB_PATH) as db:
            c = await users_count(db)
            override = await get_setting(db, "registration_override", "auto")
        await call.message.answer(f"📊 Участников: {c}\nРежим регистрации: {override}")

    @dp.callback_query(F.data == "adm:export")
    async def adm_export(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            return
        async with aiosqlite.connect(DB_PATH) as db:
            rows_cur = await db.execute("""
                SELECT number, name, surname, email, created_at, tg_id
                FROM users ORDER BY number
            """)
            rows = await rows_cur.fetchall()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["number", "name", "surname", "email", "created_at", "tg_id"])
        writer.writerows(rows)

        await call.message.answer_document(
            BufferedInputFile(output.getvalue().encode("utf-8"), filename="russvet_export.csv")
        )

    @dp.callback_query(F.data.startswith("adm:winner:"))
    async def adm_winner(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            return
        try:
            n = int(call.data.split(":")[-1])
        except:
            n = 1
        async with aiosqlite.connect(DB_PATH) as db:
            winners_list = await pick_winners(db, n)

        if not winners_list:
            await call.message.answer("Пока нет участников.")
            return

        if n == 1:
            number, name, surname, email, tg_id = winners_list[0]
            await call.message.answer(f"🏆 Победитель:\n№{number} — {name} {surname}\nEmail: {email}")
        else:
            lines = []
            for i, (number, name, surname, email, tg_id) in enumerate(winners_list, start=1):
                lines.append(f"{i}) №{number} — {name} {surname} ({email})")
            await call.message.answer("🎉 Победители:\n" + "\n".join(lines))

    @dp.callback_query(F.data == "adm:close")
    async def adm_close(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            return
        async with aiosqlite.connect(DB_PATH) as db:
            await set_setting(db, "registration_override", "closed")
        await call.message.answer("⛔ Регистрация принудительно закрыта.")

    @dp.callback_query(F.data == "adm:open")
    async def adm_open(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            return
        async with aiosqlite.connect(DB_PATH) as db:
            await set_setting(db, "registration_override", "open")
        await call.message.answer("✅ Регистрация принудительно открыта.")

    # ====== ПОЛЬЗОВАТЕЛЬСКИЙ ПОТОК ======

    @dp.message(Command("start"))
    async def start(msg: Message):
        # /start сбрасывает анкету, чтобы не "залипать" на email
        async with aiosqlite.connect(DB_PATH) as db:
            await clear_user_state(db, msg.from_user.id)

            if not await is_registration_open(db):
                await msg.answer(
                    "⛔️ Регистрация закрыта.\n\n📍 Программа мероприятия:",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[[InlineKeyboardButton(text="Афиша", url=POSTER_URL)]]
                    )
                )
                return

            cur = await db.execute("SELECT number FROM users WHERE tg_id=?", (msg.from_user.id,))
            row = await cur.fetchone()

        if row:
            await msg.answer(f"Ваш номер: {row[0]}")
            return

        await msg.answer(
            "Добро пожаловать в розыгрыш Масленицы от школы «Рассвет» ☀️\n\n"
            "Подпишитесь на Instagram и нажмите «Продолжить».",
            reply_markup=main_kb()
        )

    @dp.callback_query(F.data == "count")
    async def count(call: CallbackQuery):
        async with aiosqlite.connect(DB_PATH) as db:
            c = await users_count(db)
        await call.message.answer(f"Участников: {c}")

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
            # проверим, вдруг регистрация уже закрыта
            if not await is_registration_open(db):
                await call.message.answer("⛔️ Регистрация закрыта.")
                return

            await db.execute(
                "REPLACE INTO state(tg_id, step, name, surname) VALUES(?,?,?,?)",
                (call.from_user.id, "name", None, None)
            )
            await db.commit()

        await call.message.answer("Введите имя")

    @dp.message()
    async def form(msg: Message):
        text = (msg.text or "").strip()

        # ВАЖНО: если это команда (/panel, /export, /start и т.п.) — НЕ обрабатываем как форму
        if text.startswith("/"):
            return

        async with aiosqlite.connect(DB_PATH) as db:
            st_cur = await db.execute("SELECT tg_id, step, name, surname FROM state WHERE tg_id=?",
                                      (msg.from_user.id,))
            st = await st_cur.fetchone()

            if not st:
                return

            # регистрация могла быть закрыта админом уже в процессе
            if not await is_registration_open(db):
                await clear_user_state(db, msg.from_user.id)
                await msg.answer("⛔️ Регистрация закрыта.")
                return

            step = st[1]

            if step == "name":
                await db.execute("UPDATE state SET step=?, name=? WHERE tg_id=?",
                                 ("surname", text, msg.from_user.id))
                await db.commit()
                await msg.answer("Введите фамилию")
                return

            if step == "surname":
                await db.execute("UPDATE state SET step=?, surname=? WHERE tg_id=?",
                                 ("email", text, msg.from_user.id))
                await db.commit()
                await msg.answer("Введите email")
                return

            if step == "email":
                if not email_valid(text):
                    await msg.answer("Введите корректный email")
                    return

                email_norm = text.lower()

                check_cur = await db.execute("SELECT 1 FROM users WHERE email_norm=?", (email_norm,))
                if await check_cur.fetchone():
                    await msg.answer("Этот email уже зарегистрирован")
                    return

                number = await get_next_number(db)
                created_at = now().isoformat()

                await db.execute("""
                    INSERT INTO users(tg_id, name, surname, email, email_norm, number, created_at)
                    VALUES(?,?,?,?,?,?,?)
                """, (msg.from_user.id, st[2], st[3], text, email_norm, number, created_at))

                await clear_user_state(db, msg.from_user.id)

                await msg.answer(f"Ваш номер: {number}")

    print("Bot is starting...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(run())
