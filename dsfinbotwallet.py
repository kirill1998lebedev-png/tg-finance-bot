import os
import re
import json
import gspread
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from google.oauth2.service_account import Credentials

# ========= ENV =========
BOT_TOKEN = os.getenv("BOT_TOKEN")
SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
WORKSHEET_NAME = os.getenv("GOOGLE_WORKSHEET", "WALLET_AG")
ALLOWED_USER_IDS = {
    int(x) for x in os.getenv("ALLOWED_USER_IDS", "").split(",") if x.strip()
}
TZ = os.getenv("TIMEZONE", "Europe/Moscow")
CREDS_JSON = json.loads(os.getenv("GOOGLE_CREDS_JSON"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing")

# ========= GOOGLE =========
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

creds = Credentials.from_service_account_info(CREDS_JSON, scopes=SCOPES)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SHEET_ID)

try:
    ws = sh.worksheet(WORKSHEET_NAME)
except:
    ws = sh.add_worksheet(WORKSHEET_NAME, rows=1000, cols=10)

HEADERS = [
    "timestamp", "type", "amount", "comment",
    "from", "chat_id", "message_id"
]

if not ws.row_values(1):
    ws.update("A1:G1", [HEADERS])

# ========= BOT =========
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Приход"), KeyboardButton(text="➖ Расход")],
        [KeyboardButton(text="📊 Баланс"), KeyboardButton(text="🕘 Последние")],
        [KeyboardButton(text="❌ Удалить последнюю")]
    ],
    resize_keyboard=True
)

AMOUNT_RE = re.compile(r"[+-]?\d[\d\s.,_]*")

def now():
    return datetime.now(ZoneInfo(TZ)).strftime("%Y-%m-%d %H:%M:%S")

def parse_lines(text: str):
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        if line.startswith("#приход"):
            op = "приход"
        elif line.startswith("#расход"):
            op = "расход"
        else:
            continue

        rest = line[len(op)+1:].strip()
        m = AMOUNT_RE.search(rest)
        if not m:
            continue

        raw = m.group()
        sign = -1 if raw.startswith("-") else 1
        amount = int(re.sub(r"\D", "", raw)) * sign
        if op == "расход":
            amount = -abs(amount)
        else:
            amount = abs(amount)

        comment = rest.replace(raw, "").strip()
        rows.append((op, amount, comment))

    return rows

# ========= COMMANDS =========
@dp.message(F.text == "/start")
async def start(m: Message):
    await m.answer("💼 Wallet-бот готов к работе", reply_markup=keyboard)

@dp.message(F.text == "/balance")
@dp.message(F.text == "📊 Баланс")
async def balance(m: Message):
    values = ws.col_values(3)[1:]
    total = sum(int(v) for v in values if v.strip())
    await m.answer(f"📊 Баланс: <b>{total} ₽</b>")

@dp.message(F.text == "/last")
@dp.message(F.text == "🕘 Последние")
async def last(m: Message):
    rows = ws.get_all_values()[1:][-5:]
    if not rows:
        await m.answer("Нет операций")
        return

    text = "🕘 Последние операции:\n\n"
    for r in rows:
        text += f"{r[0]} | {r[1]} | {r[2]} ₽ | {r[3]}\n"

    await m.answer(text)

@dp.message(F.text == "/undo")
@dp.message(F.text == "❌ Удалить последнюю")
async def undo(m: Message):
    rows = ws.get_all_values()
    if len(rows) <= 1:
        await m.answer("Нечего удалять")
        return

    ws.delete_rows(len(rows))
    await m.answer("❌ Последняя операция удалена")
@dp.message(F.text.startswith("/edit"))
async def edit(m: Message):
    if m.from_user.id not in ALLOWED_USER_IDS:
        return

    parts = m.text.split(maxsplit=3)
    if len(parts) < 3:
        await m.answer(
            "Формат:\n"
            "<code>/edit НОМЕР НОВАЯ_СУММА комментарий</code>\n\n"
            "Пример:\n"
            "<code>/edit 1 1500 такси ночное</code>"
        )
        return

    try:
        index = int(parts[1])
    except ValueError:
        await m.answer("Номер операции должен быть числом")
        return

    rest = parts[2:]
    text = " ".join(rest)

    m_amount = AMOUNT_RE.search(text)
    if not m_amount:
        await m.answer("Не нашёл сумму")
        return

    raw = m_amount.group()
    new_amount = int(re.sub(r"\D", "", raw))
    new_comment = text.replace(raw, "").strip()

    rows = ws.get_all_values()
    data_rows = rows[1:]  # без заголовков

    if index < 1 or index > len(data_rows):
        await m.answer("Такой операции нет")
        return

    # Берём строку с конца (как /last)
    row_number = len(rows) - (len(data_rows) - index)

    op_type = rows[row_number - 1][1]  # приход / расход

    if op_type == "расход":
        new_amount = -abs(new_amount)
    else:
        new_amount = abs(new_amount)

    ws.update(f"C{row_number}", new_amount)   # amount
    ws.update(f"D{row_number}", new_comment)  # comment

    await m.answer("✏️ Операция обновлена")
# ========= HANDLER =========
@dp.message(F.text)
async def handler(m: Message):
    if m.from_user.id not in ALLOWED_USER_IDS:
        return

    if m.text in ["➕ Приход", "➖ Расход"]:
        await m.answer("Напиши:\n#приход 5000 комментарий\n#расход 1200 комментарий")
        return

    rows = parse_lines(m.text)
    if not rows:
        return

    for op, amount, comment in rows:
        ws.append_row(
            [
                now(),
                op,
                amount,
                comment,
                m.from_user.full_name,
                m.chat.id,
                m.message_id
            ],
            table_range="A1",
            insert_data_option="INSERT_ROWS",
            value_input_option="USER_ENTERED"
        )

    await m.answer("✅ Записано")

# ========= START =========
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())