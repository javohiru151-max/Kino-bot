import asyncio
import logging
import os
import sqlite3

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

logging.basicConfig(level=logging.INFO)

# ---------- Sozlamalar (Railway'da Variables bo'limiga yoziladi) ----------
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = {
    int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x
}
DB_PATH = os.getenv("DB_PATH", "kino.db")

router = Router()

# ---------- Ma'lumotlar bazasi ----------
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row
db.executescript(
    """
    CREATE TABLE IF NOT EXISTS movies (
        code TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        file_id TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS channels (
        chat_ref TEXT PRIMARY KEY,
        title TEXT,
        link TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY
    );
    """
)
db.commit()


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def save_user(user_id: int) -> None:
    db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    db.commit()


async def missing_channels(bot: Bot, user_id: int) -> list:
    """Foydalanuvchi obuna bo'lmagan kanallar ro'yxatini qaytaradi."""
    missing = []
    for row in db.execute("SELECT * FROM channels").fetchall():
        try:
            member = await bot.get_chat_member(row["chat_ref"], user_id)
            if member.status in ("left", "kicked"):
                missing.append(row)
        except TelegramAPIError as e:
            # Bot kanalda admin bo'lmasa shu yerga tushadi
            logging.warning("Kanalni tekshirib bo'lmadi %s: %s", row["chat_ref"], e)
            missing.append(row)
    return missing


def subscribe_keyboard(missing) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"➕ {r['title'] or r['chat_ref']}", url=r["link"])]
        for r in missing
    ]
    rows.append([InlineKeyboardButton(text="✅ Obuna bo'ldim", callback_data="check_sub")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


SUB_TEXT = (
    "🎬 Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling.\n"
    "Obuna bo'lgach, \"✅ Obuna bo'ldim\" tugmasini bosing."
)


# ---------- Foydalanuvchi buyruqlari ----------
@router.message(CommandStart())
async def start(message: Message, bot: Bot):
    save_user(message.from_user.id)
    missing = await missing_channels(bot, message.from_user.id)
    if missing:
        await message.answer(SUB_TEXT, reply_markup=subscribe_keyboard(missing))
        return
    await message.answer(
        "Xush kelibsiz! 🍿\n\nKino kodini yuboring (masalan: 101)\n"
        "yoki kino nomining bir qismini yozing."
    )


@router.callback_query(F.data == "check_sub")
async def check_sub(call: CallbackQuery, bot: Bot):
    missing = await missing_channels(bot, call.from_user.id)
    if missing:
        await call.answer("Hali hamma kanallarga obuna bo'lmadingiz ❗", show_alert=True)
        return
    await call.answer("Rahmat! ✅")
    await call.message.delete()
    await call.message.answer("Endi kino kodini yuboring 🎬")


# ---------- Admin: kino qo'shish (video + caption) ----------
# Format: video yuboring, izohiga yozing: /addmovie 101 Kino nomi
@router.message(F.video & F.caption.startswith("/addmovie"))
async def add_movie(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.caption.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Format: video izohiga yozing → /addmovie KOD Kino nomi")
        return
    _, code, title = parts
    db.execute(
        "INSERT OR REPLACE INTO movies (code, title, file_id) VALUES (?, ?, ?)",
        (code, title, message.video.file_id),
    )
    db.commit()
    await message.answer(f"✅ Saqlandi: {code} — {title}")


@router.message(Command("delmovie"))
async def del_movie(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Format: /delmovie KOD")
        return
    db.execute("DELETE FROM movies WHERE code = ?", (parts[1],))
    db.commit()
    await message.answer(f"🗑 O'chirildi: {parts[1]}")


# ---------- Admin: majburiy kanallar ----------
# Format: /addchannel @kanal_username https://t.me/kanal_username
@router.message(Command("addchannel"))
async def add_channel(message: Message, bot: Bot):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Format: /addchannel @kanal https://t.me/kanal")
        return
    _, ref, link = parts
    try:
        chat = await bot.get_chat(ref)
        title = chat.title or ref
    except TelegramAPIError as e:
        await message.answer(f"❌ Kanal topilmadi yoki bot admin emas: {e}")
        return
    db.execute(
        "INSERT OR REPLACE INTO channels (chat_ref, title, link) VALUES (?, ?, ?)",
        (ref, title, link),
    )
    db.commit()
    await message.answer(f"✅ Majburiy kanal qo'shildi: {title}")


@router.message(Command("delchannel"))
async def del_channel(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Format: /delchannel @kanal")
        return
    db.execute("DELETE FROM channels WHERE chat_ref = ?", (parts[1],))
    db.commit()
    await message.answer(f"🗑 Kanal o'chirildi: {parts[1]}")


# ---------- Admin: statistika va xabar tarqatish ----------
@router.message(Command("stats"))
async def stats(message: Message):
    if not is_admin(message.from_user.id):
        return
    users = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    movies = db.execute("SELECT COUNT(*) FROM movies").fetchone()[0]
    channels = db.execute("SELECT COUNT(*) FROM channels").fetchone()[0]
    await message.answer(
        f"📊 Foydalanuvchilar: {users}\n🎬 Kinolar: {movies}\n📢 Majburiy kanallar: {channels}"
    )


@router.message(Command("broadcast"))
async def broadcast(message: Message, bot: Bot):
    if not is_admin(message.from_user.id):
        return
    text = message.text.partition(" ")[2].strip()
    if not text:
        await message.answer("Format: /broadcast Xabar matni")
        return
    sent = failed = 0
    for row in db.execute("SELECT user_id FROM users").fetchall():
        try:
            await bot.send_message(row["user_id"], text)
            sent += 1
        except TelegramAPIError:
            failed += 1
        await asyncio.sleep(0.05)  # Telegram limitiga tushmaslik uchun
    await message.answer(f"📨 Yuborildi: {sent}\n❌ Xato: {failed}")


# ---------- Foydalanuvchi: kino kodi yoki nomi ----------
@router.message(F.text)
async def find_movie(message: Message, bot: Bot):
    save_user(message.from_user.id)
    missing = await missing_channels(bot, message.from_user.id)
    if missing:
        await message.answer(SUB_TEXT, reply_markup=subscribe_keyboard(missing))
        return

    query = message.text.strip()
    row = db.execute("SELECT * FROM movies WHERE code = ?", (query,)).fetchone()
    if row is None:
        rows = db.execute(
            "SELECT * FROM movies WHERE title LIKE ? LIMIT 5", (f"%{query}%",)
        ).fetchall()
        if not rows:
            await message.answer("Afsuski, bunday kino topilmadi 😔\nKodni tekshirib qayta yuboring.")
            return
        if len(rows) > 1:
            lines = "\n".join(f"{r['code']} — {r['title']}" for r in rows)
            await message.answer(f"Bir nechta natija topildi, kodini yuboring:\n\n{lines}")
            return
        row = rows[0]

    await bot.send_video(
        message.chat.id,
        row["file_id"],
        caption=f"🎬 {row['title']}\n🔢 Kod: {row['code']}",
        protect_content=True,  # ulashishni (forward) cheklaydi
    )


async def main():
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    logging.info("Bot ishga tushdi")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
