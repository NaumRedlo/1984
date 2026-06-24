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

    # ── Epic ─────────────────────────────────────────────────────────────
    _t("td_4star", "Sensory Zombie", "Pass a map from 4* with TD.", 1, "epic",
       "Палец по стеклу. Высшая форма страдания, добровольно."),
    _t("fl_6star", "Working Blind", "Pass a map from 6* with FL.", 1, "epic",
       "Свет выключили. Промахов нет. Это пугает даже нас."),
    _t("fc_len_5m", "Shift Marathoner", "FC a map 5 min or longer.", 1, "epic",
       "Ты не встал ни разу. Мы забеспокоились."),
    _t("fc_bpm_210", "Machine Gunner", "FC a map from 210 BPM.", 1, "epic",
       "Очередь не захлёбывается."),
    _t("fc_bpm_250", "Overdrive", "FC a map from 250 BPM.", 1, "epic",
       "Передача идёт без помех."),

    # ── Legendary ────────────────────────────────────────────────────────
    _t("ss_7star", "Flawless Record", "Get an SS on a map from 7*.", 1, "legendary",
       "Архив искал, к чему придраться. Не нашёл. Архив раздражён."),
    _t("ss_fl_55star", "Blind Surveillance", "Get an SS on a map from 6* with FL.", 1, "legendary",
       "Ты не видел ничего. И не ошибся ни разу."),  # reworked: 5.5* → 6*

    # ── Mythic ───────────────────────────────────────────────────────────
    _t("ss_8star", "The Machine", "Get an SS on a map from 8.5*.", 1, "mythic",
       "Реплей проверили трижды. Ошибки нет — значит, нет и человека."),  # reworked: 8* → 8.5*
    _t("ss_hddt_75star", "Faster Than Sight", "Get an SS on a map from 8* with HDDT.", 1, "mythic",
       "Ни тени, ни замедления, ни отклонения. Образец для плаката."),  # reworked: 7.5* → 8*
    _t("played_100k", "Perpetual Motion", "Pass 100,000 maps.", 100000, "mythic",
       "Сто тысяч записей. Впиши в дело и собственное имя — ты его, кажется, забыл."),
    _t("fc_marathon_30m", "Keep calm, keep calm...", "FC a map from 5.5*, 30 min or longer.", 1, "mythic",
       "Тридцать минут без срыва. Мы вызвали врача. На всякий."),

    # ── Secret (not announced — surface on unlock) ───────────────────────
    _t("doublethink", "Doublethink", "SS an EZ map up to 2* and pass a map from 7*.", 1, "secret",
       "Два навыка, отрицающих друг друга, в одной голове. Министерство в восторге."),
])
