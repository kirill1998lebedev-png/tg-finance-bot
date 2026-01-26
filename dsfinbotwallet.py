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
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from google.oauth2.service_account import Credentials

# ========= ENV =========
BOT_TOKEN = os.getenv("BOT_TOKEN")
SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
WORKSHEET_NAME = os.getenv("GOOGLE_WORKSHEET", "WALLET_AG")
ALLOWED_USER_IDS = {int(x) for x in os.getenv("ALLOWED_USER_IDS", "").split(",") if x.strip()}
TZ = os.getenv("TIMEZONE", "Europe/Moscow")
CREDS = json.loads(os.getenv("GOOGLE_CREDS_JSON"))

# ========= GOOGLE =========
creds = Credentials.from_service_account_info(
    CREDS,
    scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ],
)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SHEET_ID)

try:
    ws = sh.worksheet(WORKSHEET_NAME)
except:
    ws = sh.add_worksheet(WORKSHEET_NAME, rows=1000, cols=10)

HEADERS = ["timestamp", "type", "amount", "comment", "from"]
if not ws.row_values(1):
    ws.update("A1:E1", [HEADERS])

# ========= BOT =========
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Приход"), KeyboardButton(text="➖ Расход")],
        [KeyboardButton(text="📊 Баланс"), KeyboardButton(text="🕘 Последние")],
        [KeyboardButton(text="✏️ Редактировать"), KeyboardButton(text="❌ Удалить")],
        [KeyboardButton(text="ℹ️ Справка")],
    ],
    resize_keyboard=True,
)

AMOUNT_RE = re.compile(r"[+-]?\d[\d\s.,_]*")

def now():
    return datetime.now(ZoneInfo(TZ)).strftime("%Y-%m-%d %H:%M:%S")

# ========= FSM =========
class WalletState(StatesGroup):
    add_type = State()
    add_value = State()
    edit_select = State()
    edit_value = State()

# ========= COMMANDS =========
@dp.message(F.text == "/start")
async def start(m: Message):
    await m.answer("💼 Wallet-бот готов", reply_markup=keyboard)

@dp.message(F.text == "📊 Баланс")
async def balance(m: Message):
    vals = ws.col_values(3)[1:]
    total = sum(int(v) for v in vals if v)
    await m.answer(f"📊 Баланс: <b>{total} ₽</b>")

@dp.message(F.text == "🕘 Последние")
async def last(m: Message):
    rows = ws.get_all_values()[1:][-5:]
    if not rows:
        await m.answer("Нет операций")
        return

    text = "🕘 Последние операции:\n\n"
    for i, r in enumerate(rows, 1):
        text += f"{i}️⃣ {r[1]} | {r[2]} ₽ | {r[3]}\n"
    await m.answer(text)

@dp.message(F.text == "❌ Удалить")
async def delete_last(m: Message):
    rows = ws.get_all_values()
    if len(rows) <= 1:
        await m.answer("Нечего удалять")
        return
    ws.delete_rows(len(rows))
    await m.answer("❌ Последняя операция удалена")

# ========= ADD =========
@dp.message(F.text.in_(["➕ Приход", "➖ Расход"]))
async def choose_type(m: Message, state: FSMContext):
    op = "приход" if "Приход" in m.text else "расход"
    await state.update_data(op=op)
    await state.set_state(WalletState.add_value)
    await m.answer("Введи сумму и комментарий\nПример:\n5000 аванс")

@dp.message(WalletState.add_value)
async def add_value(m: Message, state: FSMContext):
    if m.from_user.id not in ALLOWED_USER_IDS:
        return

    data = await state.get_data()
    op = data["op"]

    m_amount = AMOUNT_RE.search(m.text)
    if not m_amount:
        await m.answer("Не нашёл сумму")
        return

    raw = m_amount.group()
    amount = int(re.sub(r"\D", "", raw))
    amount = -abs(amount) if op == "расход" else abs(amount)
    comment = m.text.replace(raw, "").strip()

    ws.append_row(
        [now(), op, amount, comment, m.from_user.full_name],
        table_range="A1",
        insert_data_option="INSERT_ROWS",
        value_input_option="USER_ENTERED",
    )

    await state.clear()
    await m.answer("✅ Записано", reply_markup=keyboard)

# ========= EDIT =========
@dp.message(F.text == "✏️ Редактировать")
async def edit_choose(m: Message, state: FSMContext):
    await state.set_state(WalletState.edit_select)
    await m.answer("Введи номер операции из списка «Последние»")

@dp.message(WalletState.edit_select)
async def edit_select(m: Message, state: FSMContext):
    try:
        idx = int(m.text)
    except:
        await m.answer("Нужно число")
        return

    rows = ws.get_all_values()
    data = rows[1:]
    if idx < 1 or idx > len(data):
        await m.answer("Такой операции нет")
        return

    row_num = len(rows) - (len(data) - idx)
    await state.update_data(row=row_num)
    await state.set_state(WalletState.edit_value)
    await m.answer("Введи новую сумму и комментарий")

@dp.message(WalletState.edit_value)
async def edit_value(m: Message, state: FSMContext):
    data = await state.get_data()
    row = data["row"]

    m_amount = AMOUNT_RE.search(m.text)
    if not m_amount:
        await m.answer("Не нашёл сумму")
        return

    raw = m_amount.group()
    amount = int(re.sub(r"\D", "", raw))
    op = ws.cell(row, 2).value
    amount = -abs(amount) if op == "расход" else abs(amount)
    comment = m.text.replace(raw, "").strip()

    ws.update(f"C{row}", amount)
    ws.update(f"D{row}", comment)

    await state.clear()
    await m.answer("✏️ Операция обновлена", reply_markup=keyboard)

# ========= HELP =========
@dp.message(F.text == "ℹ️ Справка")
async def help_cmd(m: Message):
    await m.answer(
        "ℹ️ Справка\n\n"
        "➕ / ➖ — ввод прихода и расхода\n"
        "Просто пиши сумму и комментарий\n\n"
        "📊 Баланс — текущий баланс\n"
        "🕘 Последние — 5 операций\n"
        "✏️ Редактировать — изменить любую операцию\n"
        "❌ Удалить — удалить последнюю\n",
        reply_markup=keyboard,
    )

# ========= START =========
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())