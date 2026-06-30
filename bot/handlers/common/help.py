"""Help (`help`) — an inline-keyboard menu, same style as `settings` (a chat
message + buttons, not a rendered card). Pick a category to see its commands."""

from aiogram import Router, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from bot.filters import TextTriggerFilter, TriggerArgs
from utils.logger import get_logger

logger = get_logger("handlers.help")
router = Router(name="help")

_HOME_TEXT = (
    "📖 <b>Справка — Project 1984</b>\n\n"
    "Выберите раздел, чтобы посмотреть команды:"
)

# code -> (button label, section body)
_SECTIONS = {
    "osu": ("🎮 osu!", (
        "🎮 <b>Команды osu!</b>\n\n"
        "<code>pf</code> — карточка статы и ранга\n"
        "<code>rs</code> — последняя сыгранная карта\n"
        "<code>cmp [ник]</code> — сравнить статы с игроком\n"
        "<code>lb</code> — лидерборд (9 категорий)\n"
        "<code>lbm [id/ссылка]</code> — локальный лидерборд карты\n"
        "🎬 кнопка под карточкой <code>rs</code> — рендер реплея в видео\n"
        "<code>tt</code> — коллекция титулов\n"
        "<code>rf</code> — синхронизация с osu! API"
    )),
    "bounty": ("🏹 Баунти / HPS", (
        "🏹 <b>Баунти и система HPS</b>\n\n"
        "<code>hps [ссылка/id]</code> — анализ HP-потенциала карты\n"
        "<code>bli</code> — список активных баунти\n"
        "<code>bde [id]</code> — детали баунти + превью HP\n"
        "<code>acc [id]</code> — принять баунти\n"
        "<code>mb</code> — твои текущие и прошлые сабмишены\n\n"
        "Еженедельный авто-пул: 24 баунти — тиры C (2-4.5★) / B (4.5-7★) / "
        "A (7-10★) / Open, по 6 в каждом.\n"
        "Без лимитов на попытки — бери любой, перепроходи до зачёта.\n\n"
        "<b>Типы и множитель HP:</b>\n"
        "SS — x1.6 (100% точность)\n"
        "Metronome — x1.4 (минимальный UR)\n"
        "Accuracy — x1.2 (рекорд по точности)\n"
        "Marathon — x1.2 (длинная карта)\n"
        "Mod — x1.1 (с нужными модами)\n"
        "Pass — x1.0 (просто пройти)\n"
        "First FC — x1.0 (первый Full Combo)"
    )),
    "duel": ("⚔️ Дуэли", (
        "⚔️ <b>Система дуэлей</b>\n\n"
        "<code>duel</code> — карточка рейтинга + панель матчмейкинга\n"
        "<code>duel &lt;ник&gt; [casual|ranked]</code> — вызвать игрока (пинг в ЛС)\n"
        "<code>duelst</code> — статус текущей дуэли и счёт\n"
        "<code>duelc</code> — отменить свой вызов\n"
        "<code>duels</code> — карточка рейтинга TrueSkill\n"
        "<code>duelh [N]</code> — последние N завершённых дуэлей"
    )),
    "account": ("👤 Аккаунт", (
        "👤 <b>Аккаунт</b>\n\n"
        "<code>reg [ник]</code> — регистрация в системе\n"
        "<code>link</code> — привязать osu! через OAuth\n"
        "<code>relink</code> — перепривязать OAuth (без потери прогресса)\n"
        "<code>unlink</code> — отвязать аккаунт (кулдаун 30 дней)\n"
        "<code>sts</code> — настройки бота\n"
        "<code>start</code> / <code>help</code> — приветствие / эта справка"
    )),
}


def _home_kb() -> InlineKeyboardMarkup:
    codes = list(_SECTIONS)
    rows = []
    for i in range(0, len(codes), 2):
        rows.append([
            InlineKeyboardButton(text=_SECTIONS[c][0], callback_data=f"help_{c}")
            for c in codes[i:i + 2]
        ])
    rows.append([InlineKeyboardButton(text="Закрыть", callback_data="help_close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="‹ Назад", callback_data="help_main"),
        InlineKeyboardButton(text="Закрыть", callback_data="help_close"),
    ]])


@router.message(TextTriggerFilter("help"))
async def help_command(message: types.Message, trigger_args: TriggerArgs = None):
    await message.answer(_HOME_TEXT, reply_markup=_home_kb(), parse_mode="HTML")


@router.callback_query(F.data.startswith("help_"))
async def process_help_callback(callback: CallbackQuery):
    action = callback.data.replace("help_", "", 1)
    if action == "close":
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.answer()
        return

    if action == "main":
        text, kb = _HOME_TEXT, _home_kb()
    elif action in _SECTIONS:
        text, kb = _SECTIONS[action][1], _back_kb()
    else:
        await callback.answer()
        return

    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


__all__ = ["router"]
