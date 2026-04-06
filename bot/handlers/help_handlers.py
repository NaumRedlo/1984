from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from utils.text_utils import escape_html

router = Router(name="help")

def get_help_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(text="Команды osu!", callback_data="help_osu"),
            InlineKeyboardButton(text="Система HPS", callback_data="help_hps")
        ],
        [
            InlineKeyboardButton(text="Баунти", callback_data="help_bounty"),
            InlineKeyboardButton(text="Аккаунт", callback_data="help_account")
        ],
        [
            InlineKeyboardButton(text="О проекте", callback_data="help_about")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

@router.message(Command("help"))
async def cmd_help(message: types.Message):
    text = (
        "<b>PROJECT 1984: CLASSIFIED — ДОСТУП РАЗРЕШЁН</b>\n"
        "Добро пожаловать в систему наблюдения <b>Отдела Баунти</b>.\n\n"
        "Используйте кнопки ниже для изучения доступных директив и протоколов.\n\n"
        "<i>Большой Брат следит за вашим рангом.</i>"
    )
    await message.answer(text, reply_markup=get_help_keyboard(), parse_mode="HTML")

@router.callback_query(F.data.startswith("help_"))
async def process_help_callback(callback: CallbackQuery):
    action = callback.data.split("_")[1]

    if action == "osu":
        text = (
            "<b>ДАННЫЕ НАБЛЮДЕНИЯ (osu!)</b>\n"
            "• <code>/profile</code> — Ваша статистика и ранг охотника.\n"
            "• <code>/rs, /recent</code> — Последняя сыгранная карта.\n"
            "• <code>/lb, /leaderboard, /top</code> — Таблица лидеров.\n"
            "  <i>9 категорий: HP, PP, Ранг, Точность, Плейкаунт,\n"
            "  Время, Р. очки, Попадания/игра, Топ скор.</i>\n"
            "• <code>/compare [никнейм]</code> — Сравнение с игроком.\n"
            "• <code>/refresh</code> — Принудительная синхронизация с osu!."
        )

    elif action == "hps":
        text = (
            "<b>ПРОТОКОЛЫ HPS 2.0</b>\n"
            "• <code>/hps [ссылка/id]</code> — Анализ потенциала карты.\n"
            "<i>Примечание: награды HP масштабируются от сложности и вашего PP.</i>"
        )

    elif action == "bounty":
        text = (
            "<b>СИСТЕМА БАУНТИ</b>\n"
            "• <code>/bountylist (/bli)</code> — Список активных баунти.\n"
            "• <code>/bountydetails (/bde) [id]</code> — Детали баунти.\n"
            "• <code>/submit [id]</code> — Отправить заявку на баунти.\n\n"
            "<b>Админ-команды:</b>\n"
            "• <code>/bountycreate (/bcr)</code> — Создать баунти.\n"
            "• <code>/bountyclose (/bcl) [id]</code> — Закрыть баунти.\n"
            "• <code>/bountydelete (/bdl) [id]</code> — Удалить баунти.\n"
            "• <code>/bountyedit (/bed) [id]</code> — Редактировать баунти.\n"
            "• <code>/review</code> — Список заявок на ревью.\n"
            "• <code>/reviewselect (/rsl) [id]</code> — Ревью заявки."
        )

    elif action == "account":
        text = (
            "<b>УПРАВЛЕНИЕ АККАУНТОМ</b>\n"
            "• <code>/register [никнейм]</code> — Регистрация в системе.\n"
        )

    elif action == "about":
        text = (
            "<b>ИНФОРМАЦИЯ О СИСТЕМЕ</b>\n"
            "<b>Project 1984</b> — автоматизированная система управления баунти, "
            "разработанная для сообщества osu!.\n\n"
            "Создана для отслеживания, расчёта и награждения выдающихся результатов."
        )

    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Назад к директивам", callback_data="help_main")]
    ])

    if action == "main":
        await callback.message.edit_text(
            "<b>PROJECT 1984: CLASSIFIED</b>\n"
            "Выберите категорию:",
            reply_markup=get_help_keyboard(),
            parse_mode="HTML"
        )
    else:
        await callback.message.edit_text(text, reply_markup=back_kb, parse_mode="HTML")

    await callback.answer()

__all__ = ["router"]
