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
