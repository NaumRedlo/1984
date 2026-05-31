"""Admin panel — inline-button navigator over every admin command (DM only).

Entry: send `admin` (or `ap`) in the bot's private chat. Renders a categorised
menu from `panel_registry`. Safe read-only commands have a "▶️ Выполнить здесь"
button that runs them in the DM; everything else shows a card with the exact
command text (tap the <code> to copy) and a "где выполнять" hint.

Admin-gating is inherited from the aggregator router (handlers.py applies
AdminFilter to messages and callbacks), so no filter is declared here.
"""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.filters import TextTriggerFilter
from bot.handlers.admin.panel_registry import (
    CATEGORIES,
    Category,
    CommandSpec,
    find_category,
    find_command,
)
from utils.formatting.text import escape_html
from utils.logger import get_logger

logger = get_logger(__name__)

router = Router(name="admin_panel")

_TG_LIMIT = 4000  # leave headroom under Telegram's 4096 hard cap

_WHERE_BADGE = {
    "dm":    "📍 Можно прямо здесь, в ЛС.",
    "any":   "📍 Где угодно — в ЛС или в группе.",
    "group": "📍 Выполнять <b>в группе</b> (не в ЛС).",
    "topic": "📍 Выполнять <b>в нужном чате/топике</b> — id запоминается из "
             "места запуска.",
}


# ── text builders ────────────────────────────────────────────────────────────

def _home_text() -> str:
    return (
        "🛠 <b>Админ-панель</b>\n"
        "─────────────\n"
        "Выбери категорию. Безопасные команды можно выполнить прямо здесь, "
        "для остальных панель покажет готовый текст и подскажет, где его отправить."
    )


def _category_text(cat: Category) -> str:
    return f"{cat.icon} <b>{escape_html(cat.title)}</b>\n\nВыбери команду:"


def _command_card_text(cmd: CommandSpec, cat: Category) -> str:
    cmd_text = cmd.trigger + (f" {cmd.args}" if cmd.args else "")
    lines = [
        f"{cat.icon} <b>{escape_html(cmd.label)}</b>",
        "",
        escape_html(cmd.desc),
        "",
        f"Команда: <code>{escape_html(cmd_text)}</code>",
        _WHERE_BADGE.get(cmd.where, _WHERE_BADGE["any"]),
    ]
    if cmd.destructive:
        lines.append(
            "⚠️ Необратимо — запусти команду вручную (у неё своё подтверждение)."
        )
    lines.append("")
    if cmd.executor:
        lines.append("Нажми «▶️ Выполнить здесь» или скопируй команду выше.")
    else:
        lines.append("Нажми на команду выше, чтобы скопировать, и отправь её.")
    return "\n".join(lines)


# ── keyboards ──────────────────────────────────────────────────────────────

def _home_kb() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for cat in CATEGORIES:
        row.append(InlineKeyboardButton(
            text=f"{cat.icon} {cat.title}", callback_data=f"ap:c:{cat.key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _category_kb(cat: Category) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for cmd in cat.commands:
        mark = "▶️ " if cmd.executor else ("⚠️ " if cmd.destructive else "")
        rows.append([InlineKeyboardButton(
            text=f"{mark}{cmd.label}", callback_data=f"ap:m:{cmd.trigger}")])
    rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data="ap:h")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _command_kb(cmd: CommandSpec, cat: Category) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if cmd.executor:
        rows.append([InlineKeyboardButton(
            text="▶️ Выполнить здесь", callback_data=f"ap:r:{cmd.trigger}")])
    rows.append([
        InlineKeyboardButton(text=f"⬅️ {cat.title}", callback_data=f"ap:c:{cat.key}"),
        InlineKeyboardButton(text="🏠 Меню", callback_data="ap:h"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _category_of(trigger: str) -> Category | None:
    for cat in CATEGORIES:
        if any(c.trigger == trigger for c in cat.commands):
            return cat
    return None


async def _safe_edit(call: types.CallbackQuery, text: str,
                     kb: InlineKeyboardMarkup) -> None:
    """Edit the panel message, ignoring the 'message is not modified' error
    that fires when the user re-taps the button they're already on."""
    try:
        await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except TelegramBadRequest:
        pass


async def _send_chunked(message: types.Message, text: str) -> None:
    if len(text) <= _TG_LIMIT:
        await message.answer(text, parse_mode="HTML")
        return
    for i in range(0, len(text), _TG_LIMIT):
        await message.answer(text[i:i + _TG_LIMIT], parse_mode="HTML")


# ── handlers ───────────────────────────────────────────────────────────────

@router.message(TextTriggerFilter("admin", "ap"))
async def cmd_admin_panel(message: types.Message) -> None:
    if message.chat.type != "private":
        await message.answer(
            "🛠 Открой админ-панель в ЛС бота: напиши мне <code>admin</code> в личке.",
            parse_mode="HTML",
        )
        return
    await message.answer(_home_text(), reply_markup=_home_kb(), parse_mode="HTML")


@router.callback_query(F.data == "ap:h")
async def cb_home(call: types.CallbackQuery) -> None:
    await _safe_edit(call, _home_text(), _home_kb())
    await call.answer()


@router.callback_query(F.data.startswith("ap:c:"))
async def cb_category(call: types.CallbackQuery) -> None:
    cat = find_category(call.data.split(":", 2)[2])
    if not cat:
        await call.answer("Категория не найдена", show_alert=True)
        return
    await _safe_edit(call, _category_text(cat), _category_kb(cat))
    await call.answer()


@router.callback_query(F.data.startswith("ap:m:"))
async def cb_command(call: types.CallbackQuery) -> None:
    trigger = call.data.split(":", 2)[2]
    cmd = find_command(trigger)
    cat = _category_of(trigger)
    if not cmd or not cat:
        await call.answer("Команда не найдена", show_alert=True)
        return
    await _safe_edit(call, _command_card_text(cmd, cat), _command_kb(cmd, cat))
    await call.answer()


@router.callback_query(F.data.startswith("ap:r:"))
async def cb_run(call: types.CallbackQuery) -> None:
    trigger = call.data.split(":", 2)[2]
    cmd = find_command(trigger)
    if not cmd or not cmd.executor:
        await call.answer("Эту команду нельзя выполнить из панели", show_alert=True)
        return
    await call.answer("Выполняю…")
    try:
        text = await cmd.executor()
    except Exception as e:
        logger.warning(f"admin panel: executor {trigger!r} failed: {e}", exc_info=True)
        await call.message.answer(
            f"Ошибка при выполнении <code>{escape_html(trigger)}</code>:\n"
            f"<code>{escape_html(str(e))}</code>",
            parse_mode="HTML",
        )
        return
    await _send_chunked(call.message, text)
