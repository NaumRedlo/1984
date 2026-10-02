from __future__ import annotations

from dataclasses import dataclass

RARITY_ORDER = ("common", "uncommon", "rare", "epic", "legendary", "mythic", "anomaly")

RARITY_META: dict[str, dict] = {
    "common":    {"label": "Common",    "label_ru": "Обычный",       "color": (158, 158, 158)},
    "uncommon":  {"label": "Uncommon",  "label_ru": "Необычный",     "color": (76, 175, 80)},
    "rare":      {"label": "Rare",      "label_ru": "Редкий",        "color": (66, 165, 245)},
    "epic":      {"label": "Epic",      "label_ru": "Эпический",     "color": (171, 71, 188)},
    "legendary": {"label": "Legendary", "label_ru": "Легендарный",   "color": (255, 179, 0)},
    "mythic":    {"label": "Mythic",    "label_ru": "Мифический",    "color": (229, 57, 53)},
    "anomaly":   {"label": "Anomaly",   "label_ru": "Аномальный",    "color": (38, 198, 218)},
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
    def rarity_order(self) -> int:
        return RARITY_ORDER.index(self.rarity)

def _t(code, name, description, target, rarity, name_ru="", description_ru=""):
    return code, TitleDef(code, name, description, target, rarity, name_ru, description_ru)

TITLE_REGISTRY: dict[str, TitleDef] = dict([

    _t("registered", "It's Nice to Meet You", "Sign up with the bot.", 1, "common",
       "Приятно познакомиться", "Зарегистрируйтесь в боте."),
    _t("rank_d", "Rough Start", "Get a D grade or lower.", 1, "common",
       "Плохое начало", "Получите ранг D или ниже."),
    _t("short_30", "Footnote", "Pass a map shorter than 30 seconds.", 1, "common",
       "Бегун", "Пройдите карту короче 30 секунд."),
    _t("graveyard", "Necrotourist", "Play a map with Graveyard status.", 1, "common",
       "Некротурист", "Сыграйте карту со статусом «кладбище»."),
    _t("profile_5day", "Still Here", "Open your own profile 5 times in a day.", 5, "common",
       "Всё ещё здесь", "Откройте свой профиль 5 раз за день."),
    _t("level_25", "Recruit", "Reach osu! level 25 or higher.", 25, "common",
       "Новобранец", "Достигните 25-го уровня в osu! или выше."),
    _t("account_2y", "Citizen of Record", "Have an account older than 2 years.", 1, "common",
       "Учтённый гражданин", "Имейте аккаунт старше 2 лет."),
    _t("daily_report", "Daily Report", "Play 10 ranked maps in a day.", 10, "common",
       "Ежедневный отчёт", "Сыграйте 10 рейтинговых карт за день."),
    _t("approved_record", "Approved Record", "Get your first FC after signing up with the bot.", 1, "common",
       "Одобренная запись", "Получите свой первый FC после регистрации в боте."),
    _t("fresh_ink", "Fresh Ink", "Pass a map ranked less than 30 days ago.", 1, "common",
       "Свежая печать", "Пройдите карту, ставшую рейтинговой менее 30 дней назад."),
    _t("map_hopper", "Map Hopper", "Play 10 different maps in one session.", 10, "common",
       "Перебежчик", "Сыграйте 10 разных карт за один сеанс."),

    _t("wysi", "WYSI", "Get a combo containing the number 727.", 1, "uncommon",
       "WYSI", "Наберите комбо, содержащее число 727."),
    _t("volunteer", "Volunteer", "Purchase osu!supporter at least once.", 1, "uncommon",
       "Доброволец", "Приобретите osu!supporter хотя бы раз."),
    _t("broken_record", "On repeat!", "Play one map 20 times.", 20, "uncommon",
       "На повторе!", "Сыграйте одну карту 20 раз."),
    _t("lowacc_streak_10", "Persistent", "Pass a map after failing it 10 or more times.", 10, "uncommon",
       "Упорный", "Пройдите карту после 10 и более провалов на ней."),
    _t("fail_95", "Last Note", "Fail a map after completing 95% of it.", 1, "uncommon",
       "Последняя нота", "Провалите карту, пройдя 95% от неё."),
    _t("reeducated", "Re-educated", "Earn a D, then later an A or better, on the same map.", 1, "uncommon",
       "Перевоспитанный", "Получите D, а позже A или выше на той же карте."),
    _t("masks_5", "Wardrobe of Masks", "Play maps with 5 different mods.", 5, "uncommon",
       "Ведущий маскарада", "Сыграйте карты с 5 разными модами."),
    _t("s_50", "Serial Performer", "Earn 50 S-ranks.", 50, "uncommon",
       "Серийный исполнитель", "Получите 50 рангов S."),
    _t("archaeologist", "Archaeologist", "Pass a map ranked 12 years ago or earlier.", 1, "uncommon",
       "Археолог", "Пройдите карту, ставшую рейтинговой 12 лет назад или ранее."),
    _t("routine_inspection", "Routine Inspection", "Get an A or better on 5 maps in a row.", 5, "uncommon",
       "Плановая проверка", "Получите A или выше на 5 картах подряд."),
    _t("revision_department", "Revision Department", "Improve your result on one map three times.", 3, "uncommon",
       "Отдел правок", "Трижды улучшите свой результат на одной карте."),
    _t("no_warmup", "No Warm-Up", "Get an S or SS on the first map of the day.", 1, "uncommon",
       "Без разминки", "Получите S или SS на первой карте дня."),
    _t("long_shift", "Long Shift", "Pass a map 10 minutes or longer.", 1, "uncommon",
       "Длинная смена", "Пройдите карту длительностью 10+ минут."),

    _t("ss_100", "Five Collector", "Earn 100 SS ranks.", 100, "rare",
       "Отличник", "Получите 100 рангов SS."),
    _t("off_day", "Total Failure", "Fail one map 30 times, up to 10 fails count per session.", 30, "rare",
       "Неудачливый", "Провалите одну карту 30 раз, не больше 10 за сеанс."),
    _t("perfectionist", "I Can Do Better!", "Re-play a map you S-ranked and SS it.", 1, "rare",
       "Я могу лучше!", "Перепройдите карту, на которой был ранг S, и получите SS."),
    _t("session_3h", "Clockwork", "Play for 3 hours in one day.", 180, "rare",
       "Плотная игра", "Играйте 3 часа за один день."),
    _t("week_500", "Stakhanovite", "Play 300 maps in a week.", 300, "rare",
       "Стахановец", "Сыграйте 300 карт за неделю."),
    _t("combo_2000", "Hardy", "Get a 2000 combo or above on one score.", 2000, "rare",
       "Выносливый", "Наберите комбо 2000 и больше за одну игру."),
    _t("ministry_accuracy", "Ministry of Accuracy", "Finish 10 maps in a row with 98%+ accuracy.", 10, "rare",
       "Министерство Точности", "Завершите 10 карт подряд с точностью 98% и выше."),
    _t("one_and_done", "One and Done", "FC a previously unplayed ranked map from 5* on the first try.", 1, "rare",
       "С первого дубля", "Получите FC с первой попытки на неигранной рейтинговой карте от 5*."),
    _t("time_traveller", "Time Traveller", "Pass maps ranked in 5 different years in one session.", 5, "rare",
       "Путешественник во времени", "За один сеанс пройдите карты, ставшие рейтинговыми в 5 разных годах."),
    _t("mod_passport", "Mod Passport", "Pass maps from 5* with NM, HD, HR, DT and FL.", 5, "rare",
       "Модифицированный паспорт", "Успешно пройдите карты от 5* с NM, HD, HR, DT и FL."),

    _t("heavy_hand", "Heavy Hand", "FC a map from 5* with AR 10.3 and above.", 1, "epic",
       "Крепкая рука", "Сделайте FC карты от 5* с AR 10.3 и выше."),
    _t("td_4star", "Sensory Zombie", "Pass a map from 4* with TD.", 1, "epic",
       "Сенсорный зомби", "Пройдите карту от 4* с TD."),
    _t("fl_6star", "Working Blind", "Pass a map from 6* with FL.", 1, "epic",
       "Работа вслепую", "Пройдите карту от 6* с FL."),
    _t("fc_len_5m", "Nerve-Wracking", "FC a map from 5* that is 8 minutes or longer.", 1, "epic",
       "Нервотрёпка", "Сделайте FC карты от 5* длиной от 8 минут."),
    _t("fc_bpm_210", "Rapid Fire", "FC a map from 6* at 240 BPM or more.", 1, "epic",
       "Скорострел", "Сделайте FC карты от 6* при 240 BPM и больше."),
    _t("session_30maps", "Assembly Line", "Play 50 different ranked maps in one session.", 50, "epic",
       "Сидячий конвейер", "Сыграйте 50 разных рейтинговых карт за один сеанс."),
    _t("archivist", "Archivist", "Hold the highest ranked score in the chat.", 1, "epic",
       "Архивариус", "Удерживайте наибольшее число рейтинговых очков в беседе."),
    _t("streak_30d", "Sleepless Watch", "Stay active 30 days in a row.", 30, "epic",
       "Бессонная вахта", "Сохраняйте активность 30 дней подряд."),
    _t("clean_sweep", "Clean Sweep", "FC 5 maps in a row from 5*.", 5, "epic",
       "Чистая серия", "Получите 5 FC подряд на картах от 5*."),
    _t("four_ministries", "Four Ministries", "FC a map from 5* separately with NM, HD, HR and DT.", 4, "epic",
       "Четыре Министерства", "Получите FC на картах от 5* отдельно с NM, HD, HR и DT."),
    _t("two_minutes_hate", "Two Minutes Hate", "FC a map from 7* shorter than two minutes.", 1, "epic",
       "Двухминутка ненависти", "Сделайте FC карты от 7*, короче двух минут."),
    _t("model_citizen", "Model Citizen", "Get 10 S or SS in a row on ranked maps.", 10, "epic",
       "Образцовый гражданин", "Получите 10 S/SS подряд на рейтинговых картах."),

    _t("ss_7star", "Flawless Record", "Get an SS on a map from 7*.", 1, "legendary",
       "Безупречность не предел", "Получите SS на карте от 7*."),
    _t("ss_fl_55star", "Blind Surveillance", "Get an SS on a map from 6* with FL.", 1, "legendary",
       "Слепой надзор", "Получите SS на карте от 6* с FL."),
    _t("ss_hdfl_5", "Tunnel Vision", "Get an SS on a map from 5* with HDFL.", 1, "legendary",
       "Туннельное зрение", "Получите SS на карте от 5* с HDFL."),
    _t("sr_10", "Double Digit Threat", "Pass a map of 10* or harder.", 1, "legendary",
       "Двузначная угроза", "Пройдите карту сложностью 10* или выше."),
    _t("ez_pass_7", "A Time Bomb", "Pass a map from 7* with EZ.", 1, "legendary",
       "Замедленная бомба", "Пройдите карту от 7* с EZ."),
    _t("ss_bpm240", "Close to Absolute", "Get an SS on a 6.5*+ map at 240 BPM or more.", 1, "legendary",
       "Приближен к абсолюту", "Получите SS на карте от 6.5* при 240 BPM и больше."),
    _t("hdhr_fc7", "Double Sentence", "FC a map from 7* with HDHR.", 1, "legendary",
       "Двойной приговор", "Сделайте FC карты от 7* с HDHR."),
    _t("big_brother", "Big Brother", "Lead three categories of the chat leaderboard at once.", 3, "legendary",
       "Большой Брат", "Одновременно возглавляйте три категории лидерборда беседы."),
    _t("untouchable", "Untouchable", "FC 10 maps in a row from 6*.", 10, "legendary",
       "Неприкасаемый", "Сделайте 10 FC подряд на картах от 6*."),
    _t("inner_party", "Inner Party", "Get 3 SS on different maps from 6* in one session.", 3, "legendary",
       "Внутренняя партия", "Получите 3 SS на разных картах от 6* за один сеанс."),

    _t("ss_8star", "The Machine", "Get an SS on a map from 8.5*.", 1, "mythic",
       "Киборг", "Получите SS на карте от 8.5*."),
    _t("ss_hddt_75star", "Faster Than Sight", "Get an SS on a map from 8* with HDDT.", 1, "mythic",
       "Быстрее взгляда", "Получите SS на карте от 8* с HDDT."),
    _t("fc_bpm_250", "Overdrive!", "FC a map from 7* at 300 BPM.", 1, "mythic",
       "Перегрузка!", "Сделайте FC карты от 7* на 300 BPM."),
    _t("played_100k", "Perpetual Motion", "Play 150,000 maps.", 150000, "mythic",
       "Вечный двигатель", "Сыграйте 150 000 карт."),
    _t("fc_marathon_30m", "Inspiring a Calm", "FC a map from 5.5*, 30 minutes or longer.", 1, "mythic",
       "Внушающий спокойствие", "Сделайте FC карты от 5.5* длиной от 30 минут."),
    _t("ss_streak_10", "Idealist", "Get 5 SS ranks in a row on ranked maps from 5*.", 5, "mythic",
       "Идеалист", "Получите 5 рангов SS подряд на рейтинговых картах от 5*."),
    _t("perfect_week", "Perfect Week", "Get at least one SS every day for 7 days in a row.", 7, "mythic",
       "Идеальная неделя", "Получайте хотя бы один SS каждый день 7 дней подряд."),
    _t("all_seeing_eye", "All-Seeing Eye", "FC a map from 7* separately with NM, HD, HR and DT.", 4, "mythic",
       "Всевидящее око", "Получите FC на картах от 7* отдельно с NM, HD, HR и DT."),

    _t("dejavu", "Déjà Vu", "Get the same score on two different maps.", 1, "anomaly",
       "Дежавю", "Наберите одинаковый счёт на двух разных картах."),
    _t("combo_1984", "1984", "Get exactly a 1984x combo.", 1, "anomaly",
       "1984", "Наберите комбо ровно 1984x."),
    _t("thoughtcrime", "Thoughtcrime", "Get 99.99% accuracy, but not an SS.", 1, "anomaly",
       "Мыслепреступление", "Получите точность 99.99%, но не SS."),
    _t("memory_hole", "Memory Hole", "Get the same score on two maps ranked a year apart or more.", 1, "anomaly",
       "Дыра памяти", "Получите одинаковый счёт на двух картах с разницей в год."),

    _t("doublethink", "Doublethink", "SS an EZ map up to 2* and pass a map from 7*.", 1, "anomaly",
       "Двоемыслие", "Получите SS на карте с EZ до 2* и пройдите карту от 7*."),
    _t("repeat_15", "Stuck in a Loop", "Play one map 15 times in a row in a session.", 15, "anomaly",
       "В круге первый", "Сыграйте одну карту 15 раз подряд за сеанс."),
    _t("compare_50", "Informant", "Use /cmp on others 50 times.", 50, "anomaly",
       "Осведомитель", "Используйте /cmp на других игроках 50 раз."),
    _t("comeback_180d", "quit w", "Return after more than 180 days of silence.", 1, "anomaly",
       "quit w", "Вернитесь после более чем 180 дней молчания."),
    _t("magic7", "Double Jackpot", "Land a score containing 777.777.", 1, "anomaly",
       "Двойной джекпот", "Наберите счёт, содержащий 777.777."),
    _t("choke_95", "Not This Time", "Break a full combo in the last 5% at 99% accuracy or above.", 1, "anomaly",
       "Попытка не пытка", "Сорвите комбо в последних 5% при точности 99% и выше."),
])
