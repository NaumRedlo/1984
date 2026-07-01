from __future__ import annotations

from dataclasses import dataclass

# Rarity tiers in ascending order. Drives both sort order and palette in the
# titles dashboard. "secret" titles are not announced — they surface on unlock.
RARITY_ORDER = ("common", "uncommon", "rare", "epic", "legendary", "mythic", "secret")

RARITY_META: dict[str, dict] = {
    "common":    {"label": "Common",    "label_ru": "Обычный",       "color": (158, 158, 158)},
    "uncommon":  {"label": "Uncommon",  "label_ru": "Необычный",     "color": (76, 175, 80)},
    "rare":      {"label": "Rare",      "label_ru": "Редкий",        "color": (66, 165, 245)},
    "epic":      {"label": "Epic",      "label_ru": "Эпический",     "color": (171, 71, 188)},
    "legendary": {"label": "Legendary", "label_ru": "Легендарный",   "color": (255, 179, 0)},
    "mythic":    {"label": "Mythic",    "label_ru": "Мифический",    "color": (229, 57, 53)},
    "secret":    {"label": "Secret",    "label_ru": "Секретный",     "color": (130, 96, 170)},
}


def rarity_label_for(rarity: str, lang: str = "en") -> str:
    meta = RARITY_META.get(rarity, {})
    if (lang or "en").lower() == "ru":
        return meta.get("label_ru", meta.get("label", rarity))
    return meta.get("label", rarity)


@dataclass(frozen=True)
class TitleDef:
    code: str
    name: str
    description: str
    target: int
    rarity: str
    flavor: str = ""
    name_ru: str = ""
    description_ru: str = ""

    @property
    def color(self) -> tuple[int, int, int]:
        return RARITY_META[self.rarity]["color"]

    @property
    def rarity_label(self) -> str:
        return RARITY_META[self.rarity]["label"]

    def rarity_label_for(self, lang: str = "en") -> str:
        return rarity_label_for(self.rarity, lang)

    def name_for(self, lang: str = "en") -> str:
        return self.name_ru if (lang or "en").lower() == "ru" and self.name_ru else self.name

    def description_for(self, lang: str = "en") -> str:
        return self.description_ru if (lang or "en").lower() == "ru" and self.description_ru else self.description

    @property
    def secret(self) -> bool:
        return self.rarity == "secret"

    @property
    def rarity_order(self) -> int:
        return RARITY_ORDER.index(self.rarity)


def _t(code, name, description, target, rarity, flavor="", name_ru="", description_ru=""):
    return code, TitleDef(code, name, description, target, rarity, flavor, name_ru, description_ru)


# Canonical registry — the 49-title "titles_1984.md" set (7 tiers × 7), rolled out
# in waves by data availability. Only titles with a wired criterion/calculator
# (see utils/title_progress.py) surface in the dashboard; the rest land per wave.
# Card UI follows the player's language preference (see card-language-preference);
# `flavor` keeps the Russian 1984 quote for caption use regardless of card language.
# Codes are stable identifiers (kept across renames to preserve user progress).
#
# name_ru/description_ru (2026-07-02): Russian card text. Descriptions preserve
# the exact substrings the card's description tokenizer recognizes — SR values
# ("6.5*+"), mod clusters ("HDDT"), "FC", "Pass", and grade letters (S/A/B/C/D) —
# untranslated, since those render as pills/coloured text, not plain words.
#
# Wave 1 — "computable now": from stored best/attempt fields + user stats, no new
# schema, no new logging subsystem. Reworked thresholds noted inline.
TITLE_REGISTRY: dict[str, TitleDef] = dict([
    # ── Common ───────────────────────────────────────────────────────────
    _t("registered", "Subject №1", "Enlist with the bot.", 1, "common",
       "Добро пожаловать в выборку. Согласие не запрашивалось.",
       "Объект №1", "Зарегистрируйся в системе."),
    _t("rank_d", "Rough Start", "Earn a D rank on a map.", 1, "common",
       "Не комом, а официально задокументированным комом.",
       "Плохое начало", "Получи ранг D на карте."),
    _t("short_30", "Footnote", "Pass a map shorter than 30 seconds.", 1, "common",
       "Самая короткая запись в деле. Зато есть.",
       "Бегун", "Пройди карту короче 30 секунд."),
    _t("graveyard", "Necrotourist", "Play a map with Graveyard status.", 1, "common",
       "Забрёл на кладбище, что-то потрогал. Зафиксировано.",
       "Некротурист", "Сыграй карту со статусом Graveyard."),
    _t("profile_5day", "Still Here", "Open your own profile 5 times in a day.", 5, "common",
       "Ты проверяешь, на месте ли ты. Ты на месте.",
       "Всё ещё здесь", "Открой свой профиль 5 раз за день."),
    _t("level_25", "Recruit", "Reach osu! level 25.", 25, "common",
       "Тебя вписали в общий строй. Номер запомни — спрашивать будут по нему.",
       "Новобранец", "Достигни 25 уровня в osu!."),
    _t("account_2y", "Citizen of Record", "Have an account older than 2 years.", 1, "common",
       "Долгий стаж под наблюдением. Благонадёжность пока условна.",
       "Учтённый гражданин", "Владей аккаунтом старше 2 лет."),
    _t("s_50", "Serial Performer", "Earn 50 S ranks.", 50, "common",
       "Полсотни добротных, неброских записей. Образцовая серость поощряется.",
       "Серийный исполнитель", "Получи 50 рангов S."),

    # ── Uncommon ─────────────────────────────────────────────────────────
    _t("wysi", "WYSI", "Get a combo containing 727.", 1, "uncommon",
       "727. Поздно отворачиваться.",
       "WYSI", "Набери комбо, содержащее 727."),
    _t("volunteer", "Volunteer", "Become an osu!supporter.", 1, "uncommon",
       "Ты сам доплатил, чтобы тебя было видно чётче.",
       "Доброволец", "Стань osu!supporter'ом."),
    _t("broken_record", "On repeat!", "Play one map 20 times.", 20, "uncommon",
       "Ты крутил запись, пока не протёр дыру в реестре.",
       "Зависимость", "Сыграй одну карту 20 раз."),
    _t("lowacc_streak_10", "Persistent", "Play 10 maps in a row below 90% accuracy.", 10, "uncommon",
       "Ты проходишь. Просто… кое-как. Кое-как мы тоже записываем.",
       "Упорный", "Сыграй 10 карт подряд с точностью ниже 90%."),
    _t("fail_95", "Last Note", "Fail a map after completing 95% of it.", 1, "uncommon",
       "Так близко. Грамота за участие выдана.",
       "Последняя нота", "Зафейль карту, пройдя 95% от неё."),
    _t("reeducated", "Re-educated", "Earn a D, then later an A or better, on the same map.", 1, "uncommon",
       "Падение и образцовое исправление в одном деле.",
       "Перевоспитанный", "Получи D, а позже A или выше на той же карте."),
    _t("masks_5", "Wardrobe of Masks", "Play maps with 5 different mods.", 5, "uncommon",
       "Ты примерил разные лица системы. Все они тебе к лицу.",
       "Ведущий маскарада", "Сыграй карты с 5 разными модами."),
    _t("ss_100", "Five Collector", "Earn 100 SS ranks.", 100, "uncommon",
       "Сотня безупречных записей в деле. Архив доволен. Архив всегда доволен.",
       "Одет до иголочки", "Получи 100 рангов SS."),

    # ── Rare ─────────────────────────────────────────────────────────────
    _t("off_day", "Crooked", "Fail one map 30 times.", 30, "rare",
       "Ты повторял, пока не стало правильно. Упорство восхищает.",
       "Криворукий", "Зафейль одну карту 30 раз."),
    _t("dejavu", "Déjà Vu", "Get the same score on two different maps.", 1, "rare",
       "Совпадений не бывает. И всё же.",
       "Дежавю", "Набери одинаковый счёт на двух разных картах."),
    _t("perfectionist", "Perfectionist", "Replay a map you S-ranked and SS it.", 1, "rare",
       "Хорошо — враг отличного. Ты избавился от врага.",
       "Я могу лучше!", "Перепройди карту, где был ранг S, и получи SS."),
    _t("archaeologist", "Archaeologist", "Pass a map ranked 12 years ago or earlier.", 1, "rare",
       "Ты разрыл слой реестра, который все забыли.",
       "Археолог", "Пройди карту, ранкнутую 12 лет назад или раньше."),
    _t("session_3h", "Clockwork", "Play a 3-hour session without a break.", 180, "rare",
       "Сессия без шва. Ты сам стал конвейером.",
       "Часовой механизм", "Играй 3 часа подряд без перерыва."),
    _t("week_500", "Stakhanovite", "Play 500 maps in a week.", 500, "rare",
       "План квартала выполнен за семь дней. Отдых не предусмотрен.",
       "Стахановец", "Сыграй 500 карт за неделю."),
    _t("combo_2000", "Long Chain", "Get a 2000+ combo on one score.", 2000, "rare",
       "Цепь длиной в две тысячи звеньев и ни одного разрыва. Образцовая непрерывность послушания.",
       "Выносливый", "Набери комбо 2000+ за один заход."),
    _t("heavy_hand", "Heavy Hand", "FC a map from 5* with effective AR 10.3+.", 1, "rare",
       "Скорость чтения за пределом норматива. Норматив будет пересмотрен.",
       "Крепкая рука", "Сделай FC карты от 5* с эффективным AR 10.3+."),

    # ── Epic ─────────────────────────────────────────────────────────────
    _t("td_4star", "Sensory Zombie", "Pass a map from 4* with TD.", 1, "epic",
       "Палец по стеклу. Высшая форма страдания, добровольно.",
       "Сенсорный зомби", "Пройди карту от 4* с TD."),
    _t("fl_6star", "Working Blind", "Pass a map from 6* with FL.", 1, "epic",
       "Свет выключили. Промахов нет. Это пугает даже нас.",
       "Работа вслепую", "Пройди карту от 6* с FL."),
    _t("fc_len_5m", "Shift Marathoner", "FC a map 8 minutes or longer.", 1, "epic",
       "Ты не встал ни разу. Мы забеспокоились.",
       "Конец смены", "Сделай FC карты длиной от 8 минут."),
    _t("fc_bpm_210", "Rapid Fire", "FC a map from 240 BPM.", 1, "epic",
       "Очередь не захлёбывается.",
       "Скорострел", "Сделай FC карты от 240 BPM."),
    _t("session_30maps", "Assembly Line", "Play 150 maps in one unbroken session.", 150, "epic",
       "Норма перевыполнена досрочно. Это даже немного жутко.",
       "Сидячий конвейер", "Сыграй 150 карт за один непрерывный сеанс."),
    _t("ss_hdfl_5", "Tunnel Vision", "Get an SS on a map from 5* with HDFL.", 1, "epic",
       "Ни предупреждения, ни света. Только память и вера в линию партии.",
       "Туннельное зрение", "Получи SS на карте от 5* с HDFL."),
    _t("sr_10", "Double Digit Threat", "Pass a map of 10* or harder.", 1, "epic",
       "Сложность перешла в двузначные числа. Объявлена повышенная готовность.",
       "Двузначная угроза", "Пройди карту сложностью 10* или выше."),

    # ── Legendary ────────────────────────────────────────────────────────
    _t("ss_7star", "Flawless Record", "Get an SS on a map from 7*.", 1, "legendary",
       "Архив искал, к чему придраться. Не нашёл. Архив раздражён.",
       "Безупречность не предел", "Получи SS на карте от 7*."),
    _t("ss_fl_55star", "Blind Surveillance", "Get an SS on a map from 6* with FL.", 1, "legendary",
       "Ты не видел ничего. И не ошибся ни разу.",
       "Слепой надзор", "Получи SS на карте от 6* с FL."),
    _t("archivist", "Archivist", "Hold the highest ranked score in the chat.", 1, "legendary",
       "Ты перелопатил больше нот, чем кто-либо в реестре. Зачем — вопрос к тебе.",
       "Архивариус", "Держи лучший ранкнутый счёт в чате."),
    _t("streak_30d", "Sleepless Watch", "Stay active 30 days in a row.", 30, "legendary",
       "Тридцать дней без явки в кровать. Образцовая преданность. Поспи.",
       "Бессонная вахта", "Оставайся активным 30 дней подряд."),
    _t("ez_pass_7", "Tightrope", "Pass a map from 7* with EZ.", 1, "legendary",
       "Министерство милостиво дало тебе три жизни и широкий подход. Ты всё равно прошёл по самому краю.",
       "Канатоходец", "Пройди карту от 7* с EZ."),
    _t("ss_bpm240", "Watchmaker", "Get an SS on a 6*+ map at 240+ BPM.", 1, "legendary",
       "Ты обогнал собственный пульс — и не сбился ни на удар.",
       "Часовщик", "Получи SS на карте от 6*+ при 240+ BPM."),
    _t("hdhr_fc7", "Double Sentence", "FC a map from 7* with HDHR.", 1, "legendary",
       "Свет погас, а допуски ужали разом. Два наказания в одном деле — и ни единого срыва.",
       "Двойной приговор", "Сделай FC карты от 7* с HDHR."),

    # ── Mythic ───────────────────────────────────────────────────────────
    _t("ss_8star", "The Machine", "Get an SS on a map from 8.5*.", 1, "mythic",
       "Реплей проверили трижды. Ошибки нет — значит, нет и человека.",
       "Машина", "Получи SS на карте от 8.5*."),
    _t("ss_hddt_75star", "Faster Than Sight", "Get an SS on a map from 8* with HDDT.", 1, "mythic",
       "Ни тени, ни замедления, ни отклонения. Образец для плаката.",
       "Быстрее взгляда", "Получи SS на карте от 8* с HDDT."),
    _t("fc_bpm_250", "Overdrive", "FC a map from 7* at 300 BPM.", 1, "mythic",
       "Передача идёт без помех.",
       "Перегрузка", "Сделай FC карты от 7* на 300 BPM."),
    _t("played_100k", "Perpetual Motion", "Play 150,000 maps.", 150000, "mythic",
       "Сто тысяч записей. Впиши в дело и собственное имя — ты его, кажется, забыл.",
       "Вечный двигатель", "Сыграй 150 000 карт."),
    _t("fc_marathon_30m", "Stay Calm", "FC a map from 5.5*, 30 minutes or longer.", 1, "mythic",
       "Тридцать минут без срыва. Мы вызвали врача. На всякий.",
       "Внушающий спокойствие", "Сделай FC карты от 5.5* длиной от 30 минут."),
    _t("ss_streak_10", "Idealist", "Get 10 SS ranks in a row.", 10, "mythic",
       "Так звали одного человека. Теперь нас в разговоре двое.",
       "Идеалист", "Получи 10 рангов SS подряд."),

    # ── Secret (not announced — surface on unlock) ───────────────────────
    _t("doublethink", "Doublethink", "SS an EZ map up to 2* and pass a map from 7*.", 1, "secret",
       "Два навыка, отрицающих друг друга, в одной голове. Министерство в восторге.",
       "Двоемыслие", "Получи SS на карте с EZ до 2* и пройди карту от 7*."),
    _t("repeat_15", "Stuck in a Loop", "Play one map 15 times in a row in a session.", 15, "secret",
       "Ты застрял в одном мгновении и проживаешь его снова. Cookiezi так уходил из игры. Ты — просто застрял.",
       "В круге первый", "Сыграй одну карту 15 раз подряд за сеанс."),
    _t("compare_50", "Informant", "Use /compare on others 50 times.", 50, "secret",
       "Ты следишь за другими внимательнее, чем за собой. Донос засчитан.",
       "Осведомитель", "Используй /compare на других 50 раз."),
    _t("comeback_180d", "quit w", "Return after 180+ days of silence.", 1, "secret",
       "Двумя словами легенда уходила в армию. Ты вернулся. Дезертир наоборот.",
       "quit w", "Вернись после 180+ дней молчания."),
    _t("magic7", "Magnificent Seven", "Land a score containing 777777.", 1, "secret",
       "Совпадение из семи семёрок. Совпадений не бывает.",
       "Семёрка", "Набери счёт, содержащий 777777."),
    _t("choke_95", "Not This Time", "Break a full combo in the last 5% at 99%+ accuracy.", 1, "secret",
       "На последней ноте всё рухнуло. Здесь живёт твой худший страх. Его зовут слайдер-брейк.",
       "Попытка не пытка", "Сорви комбо в последних 5% при точности 99%+."),
])
