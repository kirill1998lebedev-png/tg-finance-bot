import os
import re
import sqlite3
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

import gspread
from google.oauth2.service_account import Credentials


# --------- ENV ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
WORKSHEET_NAME = os.getenv("GOOGLE_WORKSHEET", "Operations")
TZ = os.getenv("TIMEZONE", "Europe/Moscow")

# wallet | chat
BOT_MODE = os.getenv("BOT_MODE", "wallet").strip().lower()

# Пример: "123456789,987654321"
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
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

TAGS = {"#расход": "расход", "#приход": "приход"}

# Сумма: optional +/- затем цифры с разделителями пробел . , _
# Примеры: 3650 | 3 650 | 3.650 | 3,650 | -3 650 | +3_650
AMOUNT_RE = re.compile(r"(?P<num>[+-]?\d[\d\s\.,_]*\d|[+-]?\d)")


def now():
    return datetime.now(ZoneInfo(TZ)).strftime("%Y-%m-%d %H:%M:%S")


def parse_amount(rest: str):
    m = AMOUNT_RE.search(rest)
    if not m:
        return None, rest.strip()

    raw = m.group("num").strip()
    sign = -1 if raw.startswith("-") else 1

    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None, rest.strip()

    value = int(digits) * sign

    comment = (rest[:m.start()] + rest[m.end():]).strip()
    comment = re.sub(r"\s{2,}", " ", comment)
    return value, comment


def parse(text: str):
    text = (text or "").strip()
    if not text:
        return None, None, ""

    first = text.split()[0].lower()
    if first not in TAGS:
        return None, None, ""

    rest = text[len(first):].strip()
    amount, comment = parse_amount(rest)
    return TAGS[first], amount, comment


def sign_amount(op_type: str, amount: int) -> int:
    # - расход всегда минус
    # - приход всегда плюс
    if op_type == "расход":
        return -abs(amount)
    if op_type == "приход":
        return abs(amount)
    return amount


def dedupe_key(message: Message, op_type: str):
    """
    В chat-режиме ключ — id сообщения-заявки (reply_to_message.message_id)
    В wallet-режиме ключ — само сообщение пользователя (message.message_id)
    """
    chat_id = message.chat.id
    if BOT_MODE == "chat":
        req = message.reply_to_message
        if not req:
            return None
        return (chat_id, req.message_id, op_type)
    else:
        return (chat_id, message.message_id, op_type)


@dp.message(F.text)
async def handler(message: Message):
    # доступ
    if ALLOWED_USER_IDS and (message.from_user.id not in ALLOWED_USER_IDS):
        return

    op_type, amount, comment = parse(message.text)
    if not op_type:
        return

    if amount is None:
        await message.reply("Не нашёл сумму. Пример: <code>#расход 3 650 такси</code>")
        return

    amount = sign_amount(op_type, amount)

    # chat mode: требуем реплай на заявку
    if BOT_MODE == "chat" and not message.reply_to_message:
        await message.reply("Ответь реплаем на заявку")
        return

    key = dedupe_key(message, op_type)
    if not key:
        await message.reply("Ответь реплаем на заявку")
        return

    chat_id, req_id, op_type_key = key

    cur = db.execute(
        "SELECT 1 FROM processed WHERE chat_id=? AND request_msg_id=? AND op_type=?",
        (chat_id, req_id, op_type_key)
    )
    if cur.fetchone():
        await message.reply("Уже записано")
        return

    db.execute("INSERT INTO processed VALUES (?,?,?)", (chat_id, req_id, op_type_key))
    db.commit()

    # поля для таблицы
    if BOT_MODE == "chat":
        req = message.reply_to_message
        req_from = req.from_user.full_name if req and req.from_user else ""
        req_text = req.text or ""
    else:
        req_from = ""
        req_text = ""

    ws.append_row([
        now(),
        message.chat.title or "private",
        op_type,          # "расход" / "приход"
        amount,           # со знаком
        comment,
        message.from_user.full_name,
        req_from,
        req_text,
        message.chat.id,
        req_id,
        message.message_id,
        BOT_MODE
    ])

    await message.reply("Записал ✅")


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())