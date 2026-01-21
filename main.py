import os
import re
import sqlite3
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.enums import ParseMode

import gspread
from google.oauth2.service_account import Credentials


# --------- ENV ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
WORKSHEET_NAME = os.getenv("GOOGLE_WORKSHEET", "Operations")
TZ = os.getenv("TIMEZONE", "Europe/Moscow")

ALLOWED_USER_IDS_RAW = os.getenv("ALLOWED_USER_IDS", "")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON", "")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан")
if not SHEET_ID:
    raise RuntimeError("GOOGLE_SHEET_ID не задан")
if not GOOGLE_CREDS_JSON:
    raise RuntimeError("GOOGLE_CREDS_JSON не задан")

ALLOWED_USER_IDS = set()
for part in [p.strip() for p in ALLOWED_USER_IDS_RAW.split(",") if p.strip()]:
    ALLOWED_USER_IDS.add(int(part))


# --------- Google Sheets ----------
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

creds_dict = json.loads(GOOGLE_CREDS_JSON)
creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SHEET_ID)
ws = sh.worksheet(WORKSHEET_NAME)


# --------- Anti-duplicate ----------
db = sqlite3.connect("dedupe.db")
db.execute("""
CREATE TABLE IF NOT EXISTS processed (
    chat_id INTEGER,
    request_msg_id INTEGER,
    op_type TEXT,
    PRIMARY KEY (chat_id, request_msg_id, op_type)
)
""")
db.commit()


# --------- Telegram ----------
bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()

TAGS = {"#расход": "expense", "#приход": "income"}


def now():
    return datetime.now(ZoneInfo(TZ)).strftime("%Y-%m-%d %H:%M:%S")


def parse(text):
    first = text.split()[0].lower()
    if first not in TAGS:
        return None, None, ""

    rest = text[len(first):].strip()
    m = re.search(r"\d+", rest)
    if not m:
        return None, None, rest

    amount = int(m.group())
    comment = rest.replace(m.group(), "").strip()
    return TAGS[first], amount, comment


@dp.message(F.text)
async def handler(message: Message):
    if message.from_user.id not in ALLOWED_USER_IDS:
        return

    text = message.text.strip()
    op_type, amount, comment = parse(text)
    if not op_type:
        return

    if not message.reply_to_message:
        await message.reply("Ответь реплаем на заявку")
        return

    req = message.reply_to_message
    chat_id = message.chat.id
    req_id = req.message_id

    cur = db.execute(
        "SELECT 1 FROM processed WHERE chat_id=? AND request_msg_id=? AND op_type=?",
        (chat_id, req_id, op_type)
    )
    if cur.fetchone():
        await message.reply("Уже записано")
        return

    db.execute(
        "INSERT INTO processed VALUES (?,?,?)",
        (chat_id, req_id, op_type)
    )
    db.commit()

    ws.append_row([
        now(),
        message.chat.title,
        op_type,
        amount,
        comment,
        message.from_user.full_name,
        req.from_user.full_name,
        req.text,
        chat_id,
        req_id,
        message.message_id,
        ""
    ])

    await message.reply("Записал ✅")


async def main():
    await dp.start_polling(bot)


if name == "__main__":
    import asyncio
    asyncio.run(main())
