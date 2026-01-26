import os
import re
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

import gspread
from google.oauth2.service_account import Credentials


# ========= ENV =========
BOT_TOKEN = os.getenv("BOT_TOKEN")
SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
WORKSHEET = os.getenv("GOOGLE_WORKSHEET")
TZ = os.getenv("TIMEZONE", "Europe/Moscow")
ALLOWED_USER_IDS = {int(x) for x in os.getenv("ALLOWED_USER_IDS", "").split(",") if x.strip()}
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON")

if not all([BOT_TOKEN, SHEET_ID, WORKSHEET, GOOGLE_CREDS_JSON]):
    raise RuntimeError("Не заданы переменные окружения")


# ========= GOOGLE =========
creds = Credentials.from_service_account_info(
    json.loads(GOOGLE_CREDS_JSON),
    scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ],
)
gc = gspread.authorize(creds)
ws = gc.open_by_key(SHEET_ID).worksheet(WORKSHEET)


# ========= BOT =========
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())


# ========= REGEX =========
AMOUNT_RE = re.compile(r"[+-]?\d[\d\s\.,]*")
CAT_RE = re.compile(r"#(\w+)")


def now():
    return datetime.now(ZoneInfo(TZ)).strftime("%Y-%m-%d %H:%M:%S")


def last_used_row() -> int:
    """
    Возвращает количество строк с данными по мнению get_all_values().
    Если у тебя есть заголовок в первой строке — он будет учтён.
    """
    return len(ws.get_all_values())


def is_valid_row_id(row: int) -> bool:
    # минимум 2, если 1-я строка заголовок; если заголовка нет — всё равно лучше не трогать 1
    return 2 <= row <= last_used_row()


def strict_append_row(values):
    """
    Всегда пишет в A:F, чтобы ничего не уезжало вправо.
    """
    ws.append_row(
        values,
        table_range="A1",
        insert_data_option="INSERT_ROWS",
        value_input_option="USER_ENTERED",
    )


# ========= KEYBOARD =========
keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Приход"), KeyboardButton(text="➖ Расход")],
        [KeyboardButton(text="💰 Баланс"), KeyboardButton(text="🕘 Последние")],
        [KeyboardButton(text="✏️ Редактировать"), KeyboardButton(text="🗑 Удалить")],
        [KeyboardButton(text="ℹ️ Справка")],
    ],
    resize_keyboard=True,
)


# ========= FSM =========
class WalletState(StatesGroup):
    add_income = State()
    add_expense = State()
    edit_select = State()
    edit_value = State()
    delete_select = State()


# ========= HELP =========
@dp.message(F.text == "ℹ️ Справка")
async def help_msg(m: Message):
    await m.answer(
        "📌 <b>Как пользоваться</b>\n\n"
        "➕ Приход / ➖ Расход — ввод сумм\n"
        "Можно несколько строк:\n"
        "<code>1500 кофе\n3200 #еда обед</code>\n\n"
        "#категория — необязательна\n\n"
        "🕘 Последние — с ID строк\n"
        "✏️ Редактировать — по ID\n"
        "🗑 Удалить — по ID\n"
        "💰 Баланс — общий",
        reply_markup=keyboard,
    )


# ========= START =========
@dp.message(F.text == "/start")
async def start(m: Message):
    if m.from_user.id not in ALLOWED_USER_IDS:
        return
    await m.answer("💼 Кошелёк готов", reply_markup=keyboard)


# ========= ADD =========
async def process_lines(m: Message, sign: int):
    lines = [l.strip() for l in m.text.splitlines() if l.strip()]
    wrote = 0

    for line in lines:
        m_amount = AMOUNT_RE.search(line)
        if not m_amount:
            continue

        raw = m_amount.group()
        digits = re.sub(r"\D", "", raw)
        if not digits:
            continue

        amount = int(digits) * sign

        cat = ""
        m_cat = CAT_RE.search(line)
        if m_cat:
            cat = m_cat.group(1)

        comment = line.replace(raw, "")
        if m_cat:
            comment = comment.replace(m_cat.group(0), "")
        comment = comment.strip()

        # A..F
        strict_append_row([
            now(),
            "приход" if sign > 0 else "расход",
            amount,
            cat,
            comment,
            m.from_user.full_name,
        ])
        wrote += 1

    if wrote == 0:
        await m.answer("Не нашёл ни одной строки с суммой. Пример: <code>1500 кофе</code>")
    else:
        await m.answer(f"✅ Записал строк: {wrote}", reply_markup=keyboard)


@dp.message(F.text == "➕ Приход")
async def income(m: Message, state: FSMContext):
    await state.set_state(WalletState.add_income)
    await m.answer("Введи сумму и комментарий (можно несколько строк)")


@dp.message(F.text == "➖ Расход")
async def expense(m: Message, state: FSMContext):
    await state.set_state(WalletState.add_expense)
    await m.answer("Введи сумму и комментарий (можно несколько строк)")


@dp.message(WalletState.add_income)
async def income_add(m: Message, state: FSMContext):
    if m.from_user.id not in ALLOWED_USER_IDS:
        return
    await process_lines(m, +1)
    await state.clear()


@dp.message(WalletState.add_expense)
async def expense_add(m: Message, state: FSMContext):
    if m.from_user.id not in ALLOWED_USER_IDS:
        return
    await process_lines(m, -1)
    await state.clear()


# ========= BALANCE =========
@dp.message(F.text == "💰 Баланс")
async def balance(m: Message):
    rows = ws.get_all_values()[1:]  # если заголовок в 1 строке
    total = 0
    for r in rows:
        if len(r) > 2 and r[2]:
            try:
                total += int(r[2])
            except:
                pass
    await m.answer(f"💰 Баланс: <b>{total} ₽</b>")


# ========= LAST =========
@dp.message(F.text == "🕘 Последние")
async def last(m: Message):
    rows = ws.get_all_values()
    data = rows[1:]  # после заголовка
    if not data:
        await m.answer("Нет операций")
        return

    last_rows = data[-10:]
    start = len(data) - len(last_rows) + 2  # реальные ID строк в Google Sheets

    text = "🕘 Последние операции:\n\n"
    for i, r in enumerate(last_rows):
        rid = start + i
        op = r[1] if len(r) > 1 else ""
        amt = r[2] if len(r) > 2 else ""
        cat = f"#{r[3]} " if len(r) > 3 and r[3] else ""
        ком = r[4] if len(r) > 4 else ""
        text += f"ID <b>{rid}</b> — {op} {amt} ₽ {cat}{ком}\n"

    await m.answer(text)


# ========= EDIT =========
@dp.message(F.text == "✏️ Редактировать")
async def edit(m: Message, state: FSMContext):
    await state.set_state(WalletState.edit_select)
    await m.answer("Введи ID строки (из «Последние»)")


@dp.message(WalletState.edit_select)
async def edit_select(m: Message, state: FSMContext):
    try:
        row = int(m.text)
    except:
        return await m.answer("Нужно число — ID строки")

    if not is_valid_row_id(row):
        return await m.answer(f"❌ Нет такой строки. Доступный диапазон ID: 2..{last_used_row()}")

    # проверим, что это реально операция (в колонке B должен быть приход/расход)
    op = ws.cell(row, 2).value
    if op not in ("приход", "расход"):
        return await m.answer("❌ В этой строке нет операции (тип не приход/расход)")

    await state.update_data(row=row)
    await state.set_state(WalletState.edit_value)
    await m.answer("Введи новое значение. Пример:\n<code>3500 #такси ночное</code>")


@dp.message(WalletState.edit_value)
async def edit_value(m: Message, state: FSMContext):
    data = await state.get_data()
    row = data["row"]

    m_amount = AMOUNT_RE.search(m.text)
    if not m_amount:
        return await m.answer("Не нашёл сумму. Пример: <code>3500 такси</code>")

    raw = m_amount.group()
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return await m.answer("Не понял сумму")

    amount = int(digits)

    op = ws.cell(row, 2).value
    amount = -abs(amount) if op == "расход" else abs(amount)

    cat = ""
    m_cat = CAT_RE.search(m.text)
    if m_cat:
        cat = m_cat.group(1)

    comment = m.text.replace(raw, "")
    if m_cat:
        comment = comment.replace(m_cat.group(0), "")
    comment = comment.strip()

    # обновляем строго C:D:E
    ws.update(
        f"C{row}:E{row}",
        [[amount, cat, comment]],
        value_input_option="USER_ENTERED",
    )

    await state.clear()
    await m.answer(f"✏️ Обновлено (ID {row})", reply_markup=keyboard)


# ========= DELETE =========
@dp.message(F.text == "🗑 Удалить")
async def delete_start(m: Message, state: FSMContext):
    await state.set_state(WalletState.delete_select)
    await m.answer("Введи ID строки для удаления (из «Последние»)")


@dp.message(WalletState.delete_select)
async def delete_row(m: Message, state: FSMContext):
    try:
        row = int(m.text)
    except ValueError:
        return await m.answer("Нужно число — ID строки")

    if not is_valid_row_id(row):
        return await m.answer(f"❌ Нет такой строки. Доступный диапазон ID: 2..{last_used_row()}")

    op = ws.cell(row, 2).value
    if op not in ("приход", "расход"):
        return await m.answer("❌ В этой строке нет операции (тип не приход/расход)")

    # чистим A..F (но строку не удаляем)
    ws.update(
        f"A{row}:F{row}",
        [["", "", "", "", "", ""]],
        value_input_option="USER_ENTERED",
    )

    await state.clear()
    await m.answer(f"🗑 Удалено (ID {row})", reply_markup=keyboard)


# ========= RUN =========
async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())