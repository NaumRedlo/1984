from aiogram import Router, types
from sqlalchemy import select

from bot.filters import TextTriggerFilter, TriggerArgs
from config.settings import ADMIN_IDS
from db.database import get_db_session
from db.models.render_worker_token import RenderWorkerToken
from services.render_farm import trust
from services.render_farm.roster import roster
from utils.admin_check import AdminFilter
from utils.formatting.text import escape_html

router = Router(name="admin_farm")
router.message.filter(AdminFilter())


async def _owners() -> dict[int, str]:
    async with get_db_session() as session:
        rows = await session.execute(
            select(RenderWorkerToken.issued_to, RenderWorkerToken.issued_name)
            .where(RenderWorkerToken.revoked_at.is_(None), RenderWorkerToken.issued_to.is_not(None))
        )
    named: dict[int, str] = {}
    for telegram_id, name in rows.all():
        if telegram_id and (name or telegram_id not in named):
            named[int(telegram_id)] = name or ""
    return named


def _who(telegram_id: int, named: dict[int, str]) -> str:
    name = named.get(telegram_id)
    return f"{escape_html(name)} <code>{telegram_id}</code>" if name else f"<code>{telegram_id}</code>"


def _mark(telegram_id: int) -> str:
    if telegram_id in ADMIN_IDS:
        return "админ"
    return "доверен" if trust.trusted(telegram_id) else "только свои"


@router.message(TextTriggerFilter("workers"))
async def cmd_workers(message: types.Message, trigger_args: TriggerArgs):
    named = await _owners()
    lines = ["<b>В сети</b>"]
    here = roster.here()
    for worker in here:
        owner = _who(worker.owner, named) if worker.owner else "без владельца"
        lines.append(f"{escape_html(worker.name or worker.key[:8])}: {owner}, {_mark(worker.owner)}")
    if not here:
        lines.append("никого")
    lines += ["", "<b>Доверены</b>"]
    lines += [_who(owner, named) for owner in trust.listed()] or ["никто, кроме админов"]
    rest = sorted(owner for owner in named if not trust.trusted(owner))
    if rest:
        lines += ["", "<b>Привязаны, рендерят только свои</b>"]
        lines += [_who(owner, named) for owner in rest]
    lines += ["", "<code>trust &lt;telegram_id&gt;</code>, <code>untrust &lt;telegram_id&gt;</code>"]
    await message.answer("\n".join(lines), parse_mode="HTML")


def _target(trigger_args: TriggerArgs) -> int | None:
    raw = (trigger_args.args or "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else None


@router.message(TextTriggerFilter("trust"))
async def cmd_trust(message: types.Message, trigger_args: TriggerArgs):
    target = _target(trigger_args)
    if target is None:
        await message.answer("Использование: <code>trust &lt;telegram_id&gt;</code>", parse_mode="HTML")
        return
    named = await _owners()
    if await trust.grant(target):
        await message.answer(f"{_who(target, named)} доверен: его устройства берут рендеры из бесед.", parse_mode="HTML")
    else:
        await message.answer(f"{_who(target, named)} уже доверен.", parse_mode="HTML")


@router.message(TextTriggerFilter("untrust"))
async def cmd_untrust(message: types.Message, trigger_args: TriggerArgs):
    target = _target(trigger_args)
    if target is None:
        await message.answer("Использование: <code>untrust &lt;telegram_id&gt;</code>", parse_mode="HTML")
        return
    named = await _owners()
    if await trust.revoke(target):
        await message.answer(f"{_who(target, named)} больше не доверен: его устройства рендерят только свои реплеи.", parse_mode="HTML")
    elif target in ADMIN_IDS:
        await message.answer(f"{_who(target, named)} админ и доверен всегда.", parse_mode="HTML")
    else:
        await message.answer(f"{_who(target, named)} и так не был доверен.", parse_mode="HTML")
