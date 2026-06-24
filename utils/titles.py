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
    _t("registered", "On Record", "Pass your first map.", 1, "common",
       "Палец опустился вовремя. Уже зафиксировано."),
    _t("first_s", "Tidy", "Earn an S rank or better.", 1, "common"),
    _t("clean_95", "By the Book", "95% accuracy or better on a map from 3*.", 1, "common"),
    _t("first_4star", "Noticed", "Pass a map from 4*.", 1, "common",
       "Сложность отмечена в деле."),
    _t("played_100", "Diligent", "Pass 100 maps.", 100, "common"),
    _t("frequency_160", "On Frequency", "Pass a map from 160 BPM.", 1, "common",
       "Ты поймал ритм передачи."),

    # ── Uncommon ─────────────────────────────────────────────────────────
    _t("hd_4star", "Unseen", "Pass a map from 4* with HD.", 1, "uncommon",
       "Ноты гаснут раньше, чем ты их видишь. Бьёшь по памяти."),
    _t("dt_4star", "Accelerated", "Pass a map from 4* with DT.", 1, "uncommon",
       "Время сжали. Ты успел."),
    _t("hr_45star", "Under Load", "Pass a map from 4.5* with HR.", 1, "uncommon"),
    _t("acc_99", "Within Norms", "99% accuracy or better on a map from 4*.", 1, "uncommon"),
    _t("fc_4star", "Clean Line", "FC a map from 4*.", 1, "uncommon",
       "Цепь не прервалась ни разу."),

    # ── Rare ─────────────────────────────────────────────────────────────
    _t("ss_4star", "Without a Blemish", "Get an SS on a map from 4*.", 1, "rare",
       "В записи нет ошибок. Признана эталонной."),
    _t("hdhr_5star", "Double Clamp", "Pass a map from 5* with HDHR.", 1, "rare"),
    _t("acc_995", "Clean Protocol", "99.5% accuracy or better on a map from 6*.", 1, "rare",
       "Колебания в пределах нормы. Норма — ты."),
    _t("fc_bpm_190", "Stenographer", "FC a map from 190 BPM.", 1, "rare",
       "Пальцы стучат быстрее, чем ум осознаёт текст."),
    _t("dry_stats", "Dry Statistics", "Pass a map from 5* with 0 miss and 3 or fewer 100s.", 1, "rare",
       "Почти всё — точно в цель."),
    _t("fc_len_4m", "Shift Marathoner", "FC a map from 5*, 4 min or longer.", 1, "rare"),

    # ── Epic ─────────────────────────────────────────────────────────────
    _t("fl_6star", "Working Blind", "Pass a map from 6* with FL.", 1, "epic",
       "Свет выключили. Промахов нет."),
    _t("ss_6star", "Benchmark", "Get an SS on a map from 6*.", 1, "epic"),
    _t("hddt_65star", "Triple Escort", "Pass a map from 6.5* with HDDT.", 1, "epic"),
    _t("ss_hd_55star", "Clean Slate", "Get an SS on a map from 5.5* with HD.", 1, "epic"),
    _t("fc_bpm_210", "Machine Gunner", "FC a map from 210 BPM.", 1, "epic",
       "Очередь не захлёбывается."),
    _t("fc_len_5m", "Long Shift", "FC a map 5 min or longer.", 1, "epic",
       "Ты не сел ни разу."),
    _t("fc_hr_6star", "Immovable", "FC a map from 6* with HR.", 1, "epic"),

    # ── Legendary ────────────────────────────────────────────────────────
    _t("ss_7star", "Flawless Record", "Get an SS on a map from 7*.", 1, "legendary",
       "Архив не нашёл, к чему придраться."),
    _t("ss_fl_55star", "Blind Surveillance", "Get an SS on a map from 5.5* with FL.", 1, "legendary",
       "Ты не видел ничего. И не ошибся ни разу."),
    _t("ss_hdhr_6star", "Double Pressure", "Get an SS on a map from 6* with HDHR.", 1, "legendary"),
    _t("fc_bpm_230", "Gun Tower", "FC a map from 230 BPM.", 1, "legendary"),
    _t("fc_len_6m", "Unbending", "FC a map from 6*, 6 min or longer.", 1, "legendary"),

    # ── Mythic ───────────────────────────────────────────────────────────
    _t("ss_8star", "The Machine", "Get an SS on a map from 8*.", 1, "mythic",
       "Реплей проверили трижды. В нём нет ошибки — значит, нет и человека."),
    _t("ss_hddt_75star", "Perfect Subject", "Get an SS on a map from 7.5* with HDDT.", 1, "mythic",
       "Ни отклонения, ни тени, ни замедления. Образец."),
    _t("ss_fl_7star", "Archive Standard", "Get an SS on a map from 7* with FL.", 1, "mythic"),
    _t("fc_bpm_250", "Superhuman", "FC a map from 250 BPM.", 1, "mythic",
       "Передача идёт без помех."),

    # ── Secret (not announced — surface on unlock) ───────────────────────
    _t("doublethink", "Doublethink", "SS an EZ map up to 2* and pass a map from 7*.", 1, "secret",
       "Два навыка, противоречащих друг другу, в одной голове."),
    _t("impossible_number", "Impossible Number", "Pass a map 2* above your top average.", 1, "secret",
       "Статистика сказала — невозможно. Ты сделал это."),
])
