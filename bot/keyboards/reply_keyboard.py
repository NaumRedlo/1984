from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

BUTTON_TRIGGER_MAP: dict[str, str] = {
    "Профиль": "profile",
    "Недавнее": "rs",
    "Топ": "lb",
    "HPS": "hps",
    "Баунти": "bountylist",
    "Помощь": "help",
}


def get_main_keyboard() -> ReplyKeyboardMarkup:
    """Persistent 2x3 reply keyboard shown at the bottom of the chat."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="Профиль"),
                KeyboardButton(text="Недавнее"),
                KeyboardButton(text="Топ"),
            ],
            [
                KeyboardButton(text="HPS"),
                KeyboardButton(text="Баунти"),
                KeyboardButton(text="Помощь"),
            ],
        ],
        resize_keyboard=True,
    )
