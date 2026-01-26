import os
import re
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

import gspread
from google.oauth2.service_account import Credentials


# ================= ENV =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
WORKSHEET_NAME = os.getenv("GOOGLE_WORKSHEET", "WALLET_AG")
TZ = os.getenv("TIMEZONE", "Europe/Moscow")

ALLOWED_USER_IDS_RAW = os.getenv("ALLOWED_USER_IDS", "")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON", "")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан")
if not SHEET_ID:
    raise RuntimeError("GOOGLE_SHEET_ID не задан")
if not GOOGLE_CREDS_JSON:
    raise RuntimeError("GOOGLE_CREDS_JSON не задан")

ALLOWED_USER_IDS = {
    int(x.strip()) for x in ALLOWED_USER_IDS_RAW.split(",") if x.strip()
}


# ================= Google Sheets =================
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

creds = Credentials.from_service_account_info(
    json.loads(GOOGLE_CREDS_JSON), scopes=SCOPES
)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SHEET_ID)

try:
    ws = sh.worksheet(WORKSHEET_NAME)
except gspread.WorksheetNotFound:
    ws = sh.add_worksheet(title=WORKSHEET_NAME, rows=2000, cols=20)

HEADERS = [
    "timestamp",
    "chat",
    "type",
    "amount",
    "comment",
    "from",
    "chat_id",
    "message_id",
]

if not ws.row_values(1):
    ws.update("A1:H1", [HEADERS])


# ================= Telegram =================
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

TAGS = {"#расход": "расход", "#приход": "приход"}

AMOUNT_RE = re.compile(r"(?P<num>[+-]?\d[\d\s\.,_]*)")


def now():
    return datetime.now(ZoneInfo(TZ)).strftime("%Y-%m-%d %H:%M:%S")


def parse_line(line: str):
    line = line.strip()
    if not line:
        return None

    first = line.split()[0].lower()
    if first not in TAGS:
        return None

    rest = line[len(first):].strip()
    m = AMOUNT_RE.search(rest)
    if not m:
        return None

    raw = m.group("num")
    sign = -1 if raw.startswith("-") else 1
    digits = re.sub(r"\D", "", raw)

    if not digits:
        return None

    amount = int(digits) * sign

    if TAGS[first] == "расход":
        amount = -abs(amount)
    else:
        amount = abs(amount)

    comment = (rest[:m.start()] + rest[m.end():]).strip()
    return TAGS[first], amount, comment


def append_row(row):
    ws.append_row(
        row,
        table_range="A1",
        insert_data_option="INSERT_ROWS",
        value_input_option="USER_ENTERED",
    )


@dp.message(F.text)
async def handler(message: Message):
    if ALLOWED_USER_IDS and message.from_user.id not in ALLOWED_USER_IDS:
        return

    lines = message.text.splitlines()
    added = 0

    for line in lines:
        parsed = parse_line(line)
        if not parsed:
            continue

        op_type, amount, comment = parsed

        append_row([
            now(),
            message.chat.title or "private",
            op_type,
            amount,
            comment,
            message.from_user.full_name,
            message.chat.id,
            message.message_id,
        ])
        added += 1

    if added:
        await message.reply(f"Записал строк: {added} ✅")


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())