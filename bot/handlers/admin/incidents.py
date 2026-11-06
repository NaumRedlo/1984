import json

from aiogram import Router, types

from bot.filters import TextTriggerFilter, TriggerArgs
from services import incidents
from utils.admin_check import AdminFilter
from utils.formatting.text import escape_html

router = Router(name="admin_incidents")
router.message.filter(AdminFilter())

LISTED = 15
MOST = 50
SHOWN = 3900

def _when(noted: incidents.Noted) -> str:
    return noted.created_at.strftime("%d.%m %H:%M") if noted.created_at else "?"

def _line(noted: incidents.Noted) -> str:
    return (f"<code>{noted.code}</code>  {_when(noted)}  {escape_html(noted.area)} / {escape_html(noted.stage)}\n"
            f"{escape_html(noted.reason[:160])}")

def _pretty(detail: str) -> str:
    try:
        return json.dumps(json.loads(detail), ensure_ascii=False, indent=1, sort_keys=True)
    except (TypeError, ValueError):
        return detail

def _whole(noted: incidents.Noted) -> str:
    parts = [
        f"<b>{noted.code}</b>  {_when(noted)} UTC",
        f"{escape_html(noted.area)} / {escape_html(noted.stage)}",
        escape_html(noted.reason),
    ]
    if noted.telegram_id:
        parts.append(f"Пользователь: <code>{noted.telegram_id}</code>, <code>whois {noted.telegram_id}</code>")
    if noted.chat_id:
        parts.append(f"Чат: <code>{noted.chat_id}</code>")
    head = "\n".join(parts)
    detail = f"\n\n<pre>{escape_html(_pretty(noted.detail))}</pre>" if noted.detail else ""
    room = SHOWN - len(head) - len(detail) - 20
    trace = ""
    if noted.trace and room > 200:
        trace = f"\n\n<pre>{escape_html(noted.trace[-room:])}</pre>"
    return (head + detail + trace)[:4096]

@router.message(TextTriggerFilter("incidents"))
async def cmd_incidents(message: types.Message, trigger_args: TriggerArgs):
    area, limit = None, LISTED
    for word in (trigger_args.args or "").split():
        if word.isdigit():
            limit = max(1, min(MOST, int(word)))
        else:
            area = word.lower()
    found = await incidents.recent(area, limit)
    if not found:
        await message.answer("Сбоев нет." if area is None else f"Сбоев в «{escape_html(area)}» нет.", parse_mode="HTML")
        return
    hint = "Подробности: <code>incident КОД</code>"
    lines: list[str] = []
    used = len(hint)
    for one in found:
        line = _line(one)
        if used + len(line) + 2 > SHOWN:
            break
        lines.append(line)
        used += len(line) + 2
    await message.answer("\n\n".join([*lines, hint]), parse_mode="HTML")

@router.message(TextTriggerFilter("incident"))
async def cmd_incident(message: types.Message, trigger_args: TriggerArgs):
    code = (trigger_args.args or "").strip()
    if not code:
        await message.answer("Использование: <code>incident КОД</code>", parse_mode="HTML")
        return
    noted = await incidents.find(code)
    if noted is None:
        await message.answer(f"Сбой <code>{escape_html(code.upper())}</code> не найден.", parse_mode="HTML")
        return
    await message.answer(_whole(noted), parse_mode="HTML")
