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


# =========================
# ENV
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
WORKSHEET_NAME = os.getenv("GOOGLE_WORKSHEET", "Wallet")
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


# =========================
# Google Sheets
# =========================
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
creds_dict = json.loads(GOOGLE_CREDS_JSON)
creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SHEET_ID)

# Если листа нет — создадим
try:
    ws = sh.worksheet(WORKSHEET_NAME)
except Exception:
    ws = sh.add_worksheet(title=WORKSHEET_NAME, rows=2000, cols=20)

HEADERS = ["OpID", "DateTime", "Type", "Amount", "Comment", "User", "Status", "EditedAt"]

def ensure_header():
    values = ws.get_all_values()
    if not values:
        ws.append_row(HEADERS)
        return
    # если первая строка не похожа на шапку — тоже поставим шапку сверху
    first_row = values[0]
    if len(first_row) < 4 or first_row[0] != "OpID":
        ws.insert_row(HEADERS, 1)

def now_str():
    return datetime.now(ZoneInfo(TZ)).strftime("%Y-%m-%d %H:%M:%S")


# =========================
# Parsing
# =========================
# Сумма: optional +/- затем цифры с разделителями пробел . , _
# Примеры: 3650 | 3 650 | 3.650 | 3,650 | -3 650 | +3_650
AMOUNT_RE = re.compile(r"(?P<num>[+-]?\d[\d\s\.,_]*\d|[+-]?\d)")

def parse_amount(text: str):
    """
    Возвращает (value:int|None, rest_comment:str)
    """
    m = AMOUNT_RE.search(text)
    if not m:
        return None, text.strip()

    raw = m.group("num").strip()
    sign = -1 if raw.startswith("-") else 1

    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None, text.strip()

    value = int(digits) * sign

    comment = (text[:m.start()] + text[m.end():]).strip()
    comment = re.sub(r"\s{2,}", " ", comment)
    return value, comment

def parse_operation(text: str):
    """
    Поддерживаем форматы:
    +1500 коммент
    -3650 коммент
    приход 1500 коммент
    расход 3650 коммент
    """
    t = text.strip()
    if not t:
        return None

    low = t.lower()

    # 1) если начинается с +/-
    if low[0] in ["+", "-"]:
        amount, comment = parse_amount(t)
        if amount is None:
            return None
        op_type = "приход" if amount > 0 else "расход"
        return op_type, amount, comment

    # 2) если начинается со слова
    if low.startswith("приход") or low.startswith("расход"):
        first = low.split()[0]
        rest = t[len(first):].strip()
        amount, comment = parse_amount(rest)
        if amount is None:
            return None
        # приводим знак по типу
        if first == "расход":
            amount = -abs(amount)
            op_type = "расход"
        else:
            amount = abs(amount)
            op_type = "приход"
        return op_type, amount, comment

    return None


# =========================
# Telegram
# =========================
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

HELP_TEXT = (
    "💼 <b>Wallet бот</b>\n\n"
    "Добавление операций:\n"
    "• <code>+1500 аванс</code>\n"
    "• <code>-3 650 такси</code>\n"
    "• <code>приход 5000 возврат</code>\n"
    "• <code>расход 1200 обед</code>\n\n"
    "Команды:\n"
    "• /balance — баланс\n"
    "• /last — последние операции\n"
    "• /edit ID сумма комментарий — исправить\n"
    "  пример: <code>/edit 12 -1200 обед</code>\n"
    "• /del ID — удалить (мягко)\n"
    "  пример: <code>/del 12</code>\n"
)

def require_access(message: Message) -> bool:
    return (message.from_user and message.from_user.id in ALLOWED_USER_IDS)

def get_all_rows():
    # список списков, включая шапку
    return ws.get_all_values()

def next_op_id(rows):
    # rows includes header, so operations count = len(rows)-1
    return max(0, len(rows) - 1) + 1

def find_row_by_opid(op_id: int):
    # Ищем строку по первому столбцу OpID
    # (для маленьких листов это нормально и надёжно)
    rows = get_all_rows()
    for idx, row in enumerate(rows[1:], start=2):  # sheet rows are 1-based, header at 1
        if row and row[0].strip() == str(op_id):
            return idx, row
    return None, None

def set_status_deleted(row_index: int):
    # Amount -> 0, Status -> DELETED, EditedAt -> now
    ws.update(f"D{row_index}:H{row_index}", [[
        "",  # Amount in D will be overwritten below properly; leaving empty won't help. We'll set exact range:
    ]])

@dp.message(F.text == "/start")
async def start_cmd(message: Message):
    if not require_access(message):
        return
    await message.answer(HELP_TEXT)

@dp.message(F.text == "/help")
async def help_cmd(message: Message):
    if not require_access(message):
        return
    await message.answer(HELP_TEXT)

@dp.message(F.text == "/balance")
async def balance_cmd(message: Message):
    if not require_access(message):
        return
    # SUM по Amount (колонка D), но считаем только числовые
    col = ws.col_values(4)  # D
    total = 0
    for v in col[1:]:  # skip header
        try:
            total += int(float(v))
        except Exception:
            continue
    sign = "+" if total >= 0 else "-"
    await message.answer(f"💰 Баланс: <b>{sign}{abs(total):,}</b> ₽".replace(",", " "))

@dp.message(F.text == "/last")
async def last_cmd(message: Message):
    if not require_access(message):
        return

    rows = get_all_rows()
    ops = rows[1:]  # without header
    ops = ops[-10:] if len(ops) > 10 else ops

    if not ops:
        await message.answer("Пока нет операций.")
        return

    lines = ["📒 <b>Последние операции:</b>"]
    for r in reversed(ops):  # newest first
        # OpID, DateTime, Type, Amount, Comment, User, Status, EditedAt
        opid = r[0] if len(r) > 0 else ""
        dt = r[1] if len(r) > 1 else ""
        typ = r[2] if len(r) > 2 else ""
        amt = r[3] if len(r) > 3 else ""
        cmt = r[4] if len(r) > 4 else ""
        status = r[6] if len(r) > 6 else "OK"
        # красиво:
        try:
            a = int(float(amt))
        except Exception:
            a = 0
        s = "+" if a >= 0 else "-"
        line = f"<code>{opid}</code> | {typ} {s}{abs(a):,} ₽ — {cmt} <i>({status})</i>".replace(",", " ")
        lines.append(line)

    await message.answer("\n".join(lines))

@dp.message(F.text.regexp(r"^/del\s+\d+\s*$"))
async def del_cmd(message: Message):
    if not require_access(message):
        return

    op_id = int(message.text.strip().split()[1])
    row_index, row = find_row_by_opid(op_id)
    if not row_index:
        await message.answer("Не нашёл такую операцию.")
        return

    # мягкое удаление: Amount=0, Status=DELETED, EditedAt=now
    ws.update(f"D{row_index}:H{row_index}", [[
        0,                          # Amount
        row[4] if len(row) > 4 else "",  # Comment (оставим)
        row[5] if len(row) > 5 else "",  # User
        "DELETED",                  # Status
        now_str()                   # EditedAt
    ]])
    await message.answer(f"Удалил (мягко) ✅ ID {op_id}")

@dp.message(F.text.regexp(r"^/edit\s+\d+\s+.+$"))
async def edit_cmd(message: Message):
    if not require_access(message):
        return

    # /edit ID сумма комментарий
    parts = message.text.strip().split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Формат: /edit ID сумма комментарий")
        return

    op_id = int(parts[1])
    tail = parts[2]

    row_index, row = find_row_by_opid(op_id)
    if not row_index:
        await message.answer("Не нашёл такую операцию.")
        return

    parsed = parse_operation(tail)
    if not parsed:
        await message.answer("Не понял. Пример: <code>/edit 12 -1200 обед</code>")
        return

    op_type, amount, comment = parsed
    if not comment:
        await message.answer("Нужен комментарий. Пример: <code>/edit 12 -1200 обед</code>")
        return

    # Записываем изменения: Type, Amount, Comment, Status, EditedAt
    ws.update(f"C{row_index}:H{row_index}", [[
        op_type,
        amount,
        comment,
        row[5] if len(row) > 5 else (message.from_user.full_name if message.from_user else ""),
        "EDITED",
        now_str()
    ]])

    await message.answer(f"Исправил ✅ ID {op_id}")

@dp.message(F.text)
async def add_operation(message: Message):
    if not require_access(message):
        return

    parsed = parse_operation(message.text)
    if not parsed:
        # молчим, чтобы не мешать
        return

    op_type, amount, comment = parsed
    if not comment:
        await message.answer("Нужен комментарий. Пример: <code>-1200 обед</code>")
        return

    ensure_header()
    rows = get_all_rows()
    op_id = next_op_id(rows)

    ws.append_row([
        op_id,
        now_str(),
        op_type,
        amount,
        comment,
        message.from_user.full_name if message.from_user else "",
        "OK",
        ""
    ])

    sign = "+" if amount >= 0 else "-"
    await message.answer(f"Записал ✅ {op_type}: {sign}{abs(amount):,} ₽".replace(",", " "))

async def main():
    ensure_header()
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
