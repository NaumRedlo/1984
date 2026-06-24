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


# Phase A: titles computable purely from stored best-scores fields (rank, mods,
# nominal star_rating, accuracy) plus user stats — no schema changes needed.
# Card UI is English; `flavor` keeps the Russian 1984 quote for caption use.
TITLE_REGISTRY: dict[str, TitleDef] = dict([
    # ── Common ───────────────────────────────────────────────────────────
    _t("registered", "On Record", "Clear your first map.", 1, "common",
       "Палец опустился вовремя. Уже зафиксировано."),
    _t("first_s", "Tidy", "Earn an S rank or better.", 1, "common"),
    _t("clean_95", "By the Book", "95%+ accuracy on a 3*+ map.", 1, "common"),
    _t("first_4star", "Noticed", "Clear a 4*+ map.", 1, "common",
       "Сложность отмечена в деле."),
    _t("played_100", "Diligent", "Clear 100 maps.", 100, "common"),

    # ── Uncommon ─────────────────────────────────────────────────────────
    _t("hd_4star", "Unseen", "Clear a 4*+ map with HD.", 1, "uncommon",
       "Ноты гаснут раньше, чем ты их видишь. Бьёшь по памяти."),
    _t("dt_4star", "Accelerated", "Clear a 4*+ map with DT.", 1, "uncommon",
       "Время сжали. Ты успел."),
    _t("hr_45star", "Under Load", "Clear a 4.5*+ map with HR.", 1, "uncommon"),
    _t("acc_99", "Within Norms", "99%+ accuracy on a 4*+ map.", 1, "uncommon"),

    # ── Rare ─────────────────────────────────────────────────────────────
    _t("ss_4star", "Without a Blemish", "Get an SS on a 4*+ map.", 1, "rare",
       "В записи нет ошибок. Признана эталонной."),
    _t("hdhr_5star", "Double Clamp", "Clear a 5*+ map with HDHR.", 1, "rare"),
    _t("acc_995", "Clean Protocol", "99.5%+ accuracy on a 6*+ map.", 1, "rare",
       "Колебания в пределах нормы. Норма — ты."),

    # ── Epic ─────────────────────────────────────────────────────────────
    _t("fl_6star", "Working Blind", "Clear a 6*+ map with FL.", 1, "epic",
       "Свет выключили. Промахов нет."),
    _t("ss_6star", "Benchmark", "Get an SS on a 6*+ map.", 1, "epic"),
    _t("hddt_65star", "Triple Escort", "Clear a 6.5*+ map with HDDT.", 1, "epic"),
    _t("ss_hd_55star", "Clean Slate", "Get an SS on a 5.5*+ map with HD.", 1, "epic"),

    # ── Legendary ────────────────────────────────────────────────────────
    _t("ss_7star", "Flawless Record", "Get an SS on a 7*+ map.", 1, "legendary",
       "Архив не нашёл, к чему придраться."),
    _t("ss_fl_55star", "Blind Surveillance", "Get an SS on a 5.5*+ map with FL.", 1, "legendary",
       "Ты не видел ничего. И не ошибся ни разу."),
    _t("ss_hdhr_6star", "Double Pressure", "Get an SS on a 6*+ map with HDHR.", 1, "legendary"),

    # ── Mythic ───────────────────────────────────────────────────────────
    _t("ss_8star", "The Machine", "Get an SS on an 8*+ map.", 1, "mythic",
       "Реплей проверили трижды. В нём нет ошибки — значит, нет и человека."),
    _t("ss_hddt_75star", "Perfect Subject", "Get an SS on a 7.5*+ map with HDDT.", 1, "mythic",
       "Ни отклонения, ни тени, ни замедления. Образец."),
    _t("ss_fl_7star", "Archive Standard", "Get an SS on a 7*+ map with FL.", 1, "mythic"),

    # ── Secret (not announced — surface on unlock) ───────────────────────
    _t("doublethink", "Doublethink", "SS an EZ map up to 2* and clear a 7*+ map.", 1, "secret",
       "Два навыка, противоречащих друг другу, в одной голове."),
    _t("impossible_number", "Impossible Number", "Clear a map 2* above your top average.", 1, "secret",
       "Статистика сказала — невозможно. Ты сделал это."),
])
