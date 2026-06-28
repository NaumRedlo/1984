from __future__ import annotations

from dataclasses import dataclass

# Rarity tiers in ascending order. Drives both sort order and palette in the
# titles dashboard. "secret" titles are not announced — they surface on unlock.
RARITY_ORDER = ("common", "uncommon", "rare", "epic", "legendary", "mythic", "secret")

RARITY_META: dict[str, dict] = {
    "common":    {"label": "Common",    "color": (158, 158, 158)},
    "uncommon":  {"label": "Uncommon",  "color": (76, 175, 80)},
    "rare":      {"label": "Rare",      "color": (66, 165, 245)},
    "epic":      {"label": "Epic",      "color": (171, 71, 188)},
    "legendary": {"label": "Legendary", "color": (255, 179, 0)},
    "mythic":    {"label": "Mythic",    "color": (229, 57, 53)},
    "secret":    {"label": "Secret",    "color": (130, 96, 170)},
}


@dataclass(frozen=True)
class TitleDef:
    code: str
    name: str
    description: str
    target: int
    rarity: str
    flavor: str = ""

    @property
    def color(self) -> tuple[int, int, int]:
        return RARITY_META[self.rarity]["color"]

    @property
    def rarity_label(self) -> str:
        return RARITY_META[self.rarity]["label"]

    @property
    def secret(self) -> bool:
        return self.rarity == "secret"

    @property
    def rarity_order(self) -> int:
        return RARITY_ORDER.index(self.rarity)


def _t(code, name, description, target, rarity, flavor=""):
    return code, TitleDef(code, name, description, target, rarity, flavor)


# Canonical registry — the 49-title "titles_1984.md" set (7 tiers × 7), rolled out
# in waves by data availability. Only titles with a wired criterion/calculator
# (see utils/title_progress.py) surface in the dashboard; the rest land per wave.
# Card UI is English; `flavor` keeps the Russian 1984 quote for caption use.
# Codes are stable identifiers (kept across renames to preserve user progress).
#
# Wave 1 — "computable now": from stored best/attempt fields + user stats, no new
# schema, no new logging subsystem. Reworked thresholds noted inline.
TITLE_REGISTRY: dict[str, TitleDef] = dict([
    # ── Common ───────────────────────────────────────────────────────────
    _t("registered", "Subject #1", "Enlist with the bot.", 1, "common",
       "Добро пожаловать в выборку. Согласие не запрашивалось."),
    _t("rank_d", "Rough Start", "Earn a D rank on a map.", 1, "common",
       "Не комом, а официально задокументированным комом."),
    _t("short_30", "Footnote", "Pass a map shorter than 30 seconds.", 1, "common",
       "Самая короткая запись в деле. Зато есть."),
    _t("graveyard", "Necrotourist", "Play a map with Graveyard status.", 1, "common",
       "Забрёл на кладбище, что-то потрогал. Зафиксировано."),  # wave 3
    _t("profile_5day", "Still Here", "Open your own profile 5 times in a day.", 5, "common",
       "Ты проверяешь, на месте ли ты. Ты на месте."),  # wave 4
    _t("level_25", "Recruit", "Reach osu! level 25.", 25, "common",
       "Тебя вписали в общий строй. Номер запомни — спрашивать будут по нему."),  # batch II
    _t("account_2y", "Citizen of Record", "Have an account older than 2 years.", 1, "common",
       "Долгий стаж под наблюдением. Благонадёжность пока условна."),  # batch II
    _t("s_50", "Serial Performer", "Earn 50 S ranks.", 50, "common",
       "Полсотни добротных, неброских записей. Образцовая серость поощряется."),  # batch II

    # ── Uncommon ─────────────────────────────────────────────────────────
    _t("wysi", "WYSI", "Get a combo containing 727.", 1, "uncommon",
       "727. Поздно отворачиваться."),  # wave 2
    _t("volunteer", "Volunteer", "Become an osu!supporter.", 1, "uncommon",
       "Ты сам доплатил, чтобы тебя было видно чётче."),  # wave 3
    _t("broken_record", "On repeat!", "Play one map 20 times.", 20, "uncommon",
       "Ты крутил запись, пока не протёр дыру в реестре."),  # wave 2
    _t("lowacc_streak_10", "Persistent", "Play 10 maps in a row below 90% accuracy.", 10, "uncommon",
       "Ты проходишь. Просто… кое-как. Кое-как мы тоже записываем."),  # wave 2
    _t("fail_95", "Last Note", "Fail a map after completing 95% of it.", 1, "uncommon",
       "Так близко. Грамота за участие выдана."),  # wave 5
    _t("reeducated", "Re-educated", "Earn a D, then later an A or better, on the same map.", 1, "uncommon",
       "Падение и образцовое исправление в одном деле."),  # wave 2 (moved epic→uncommon)
    _t("masks_5", "Wardrobe of Masks", "Play maps with 5 different mods.", 5, "uncommon",
       "Ты примерил разные лица системы. Все они тебе к лицу."),  # batch II
    _t("ss_100", "Five Collector", "Earn 100 SS ranks.", 100, "uncommon",
       "Сотня безупречных записей в деле. Архив доволен. Архив всегда доволен."),  # batch II

    # ── Rare ─────────────────────────────────────────────────────────────
    _t("off_day", "Crooked", "Fail one map 30 times.", 30, "rare",
       "Ты повторял, пока не стало правильно. Упорство восхищает."),  # wave 2
    _t("dejavu", "Déjà Vu", "Get the same score on two different maps.", 1, "rare",
       "Совпадений не бывает. И всё же."),  # wave 2
    _t("perfectionist", "Perfectionist", "Replay a map you S-ranked and SS it.", 1, "rare",
       "Хорошо — враг отличного. Ты избавился от врага."),  # wave 2
    _t("archaeologist", "Archaeologist", "Pass a map ranked 12 years ago or earlier.", 1, "rare",
       "Ты разрыл слой реестра, который все забыли."),  # wave 3
    _t("session_3h", "Clockwork", "Play a 3-hour session without a break.", 180, "rare",
       "Сессия без шва. Ты сам стал конвейером."),  # wave 4
    _t("week_500", "Stakhanovite", "Play 500 maps in a week.", 500, "rare",
       "План квартала выполнен за семь дней. Отдых не предусмотрен."),  # wave 4
    _t("combo_2000", "Long Chain", "Get a 2000+ combo on one score.", 2000, "rare",
       "Цепь длиной в две тысячи звеньев и ни одного разрыва. Образцовая непрерывность послушания."),  # batch II
    _t("heavy_hand", "Heavy Hand", "FC a map from 5* with effective AR 10.3+.", 1, "rare",
       "Скорость чтения за пределом норматива. Норматив будет пересмотрен."),  # batch II

    # ── Epic ─────────────────────────────────────────────────────────────
    _t("td_4star", "Sensory Zombie", "Pass a map from 4* with TD.", 1, "epic",
       "Палец по стеклу. Высшая форма страдания, добровольно."),
    _t("fl_6star", "Working Blind", "Pass a map from 6* with FL.", 1, "epic",
       "Свет выключили. Промахов нет. Это пугает даже нас."),
    _t("fc_len_5m", "Shift Marathoner", "FC a map 5 minutes or longer.", 1, "epic",
       "Ты не встал ни разу. Мы забеспокоились."),
    _t("fc_bpm_210", "Machine Gunner", "FC a map from 210 BPM.", 1, "epic",
       "Очередь не захлёбывается."),
    _t("session_30maps", "Assembly Line", "Play 30 maps in one unbroken session.", 30, "epic",
       "Норма перевыполнена досрочно. Это даже немного жутко."),  # wave 4
    _t("ss_hdfl_5", "Tunnel Vision", "Get an SS on a map from 5* with HDFL.", 1, "epic",
       "Ни предупреждения, ни света. Только память и вера в линию партии."),  # batch II
    _t("sr_10", "Double Digit Threat", "Pass a map of 10* or harder.", 1, "epic",
       "Сложность перешла в двузначные числа. Объявлена повышенная готовность."),  # batch II (eff. SR)

    # ── Legendary ────────────────────────────────────────────────────────
    _t("ss_7star", "Flawless Record", "Get an SS on a map from 7*.", 1, "legendary",
       "Архив искал, к чему придраться. Не нашёл. Архив раздражён."),
    _t("ss_fl_55star", "Blind Surveillance", "Get an SS on a map from 6* with FL.", 1, "legendary",
       "Ты не видел ничего. И не ошибся ни разу."),  # reworked: 5.5* → 6*
    _t("archivist", "Archivist", "Hold the highest ranked score in the chat.", 1, "legendary",
       "Ты перелопатил больше нот, чем кто-либо в реестре. Зачем — вопрос к тебе."),  # wave 3 (relative — wave 4)
    _t("streak_30d", "Sleepless Watch", "Stay active 30 days in a row.", 30, "legendary",
       "Тридцать дней без явки в кровать. Образцовая преданность. Поспи."),  # wave 4
    _t("ez_pass_7", "Tightrope", "Pass a map from 7* with EZ.", 1, "legendary",
       "Министерство милостиво дало тебе три жизни и широкий подход. Ты всё равно прошёл по самому краю."),  # batch II
    _t("ss_bpm240", "Watchmaker", "Get an SS on a 6*+ map at 240+ BPM.", 1, "legendary",
       "Ты обогнал собственный пульс — и не сбился ни на удар."),  # batch II (eff. SR/BPM)
    _t("hdhr_fc7", "Double Sentence", "FC a map from 7* with HDHR.", 1, "legendary",
       "Свет погас, а допуски ужали разом. Два наказания в одном деле — и ни единого срыва."),  # batch II (eff. SR)

    # ── Mythic ───────────────────────────────────────────────────────────
    _t("ss_8star", "The Machine", "Get an SS on a map from 8.5*.", 1, "mythic",
       "Реплей проверили трижды. Ошибки нет — значит, нет и человека."),  # reworked: 8* → 8.5*
    _t("ss_hddt_75star", "Faster Than Sight", "Get an SS on a map from 8* with HDDT.", 1, "mythic",
       "Ни тени, ни замедления, ни отклонения. Образец для плаката."),  # reworked: 7.5* → 8*
    _t("fc_bpm_250", "Overdrive", "FC a map from 7* at 250 BPM.", 1, "mythic",
       "Передача идёт без помех."),  # reworked: +7* requirement, Epic → Mythic
    _t("played_100k", "Perpetual Motion", "Pass 100,000 maps.", 100000, "mythic",
       "Сто тысяч записей. Впиши в дело и собственное имя — ты его, кажется, забыл."),
    _t("fc_marathon_30m", "Stay Calm", "FC a map from 5.5*, 30 minutes or longer.", 1, "mythic",
       "Тридцать минут без срыва. Мы вызвали врача. На всякий."),
    _t("ss_streak_10", "Idealist", "Get 10 SS ranks in a row.", 10, "mythic",
       "Так звали одного человека. Теперь нас в разговоре двое."),  # wave 2

    # ── Secret (not announced — surface on unlock) ───────────────────────
    _t("doublethink", "Doublethink", "SS an EZ map up to 2* and pass a map from 7*.", 1, "secret",
       "Два навыка, отрицающих друг друга, в одной голове. Министерство в восторге."),
    _t("repeat_15", "Stuck in a Loop", "Play one map 15 times in a row in a session.", 15, "secret",
       "Ты застрял в одном мгновении и проживаешь его снова. Cookiezi так уходил из игры. Ты — просто застрял."),  # wave 4
    _t("compare_50", "Informant", "Use /compare on others 50 times.", 50, "secret",
       "Ты следишь за другими внимательнее, чем за собой. Донос засчитан."),  # wave 4
    _t("comeback_180d", "quit w", "Return after 180+ days of silence.", 1, "secret",
       "Двумя словами легенда уходила в армию. Ты вернулся. Дезертир наоборот."),  # wave 4
    _t("magic7", "Magnificent Seven", "Land a score containing 777777.", 1, "secret",
       "Совпадение из семи семёрок. Совпадений не бывает."),  # wave 5
    _t("choke_95", "Not This Time", "Break a full combo in the last 5% at 99%+ accuracy.", 1, "secret",
       "На последней ноте всё рухнуло. Здесь живёт твой худший страх. Его зовут слайдер-брейк."),  # wave 5
])
