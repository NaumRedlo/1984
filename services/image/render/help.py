import asyncio
from io import BytesIO
from typing import Dict

from PIL import ImageDraw

from services.image.constants import (
    CARD_WIDTH,
    ROW_EVEN,
    ROW_ODD,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    ACCENT_RED,
    PADDING_X,
)
from services.image.utils import load_icon


class HelpCardMixin:
    # Category definitions for help cards
    HELP_CATEGORIES: Dict[str, Dict] = {
        "osu": {
            "title": "osu! COMMANDS",
            "commands": [
                {"name": "profile, pf", "desc": "Your stats and rank card"},
                {"name": "rs, recent", "desc": "Last played map"},
                {"name": "compare [username]", "desc": "Compare stats with another player"},
                {"name": "lb, leaderboard, top", "desc": "Leaderboard (9 categories)"},
                {"name": "lbm [id/url]", "desc": "Local map leaderboard"},
                {"name": "refresh", "desc": "Force sync with osu! API"},
            ],
        },
        "hps": {
            "title": "HPS SYSTEM",
            "commands": [
                {"name": "hps [link/id]", "desc": "Analyze map HP potential"},
                {"name": "bountylist, bli", "desc": "Active bounties list"},
                {"name": "bountydetails, bde [id]", "desc": "Bounty details + HP preview"},
                {"name": "accept, acc [id]", "desc": "Accept a bounty (3/week, 3 attempts)"},
                {"name": "mybounties, mb", "desc": "Your active & past bounty submissions"},
            ],
        },
        "bounty": {
            "title": "BOUNTY SYSTEM",
            "text": (
                "Weekly auto-pool: Tier C (2-4.5*) / B (4.5-7*) / A (7-10*) / Open\n"
                "Claim limit: 3 auto-bounties per week, 3 attempts per bounty.\n\n"
                "Types & HP multiplier:\n"
                "SS          x1.6 — 100% accuracy\n"
                "Metronome   x1.4 — lowest Unstable Rate\n"
                "Accuracy    x1.2 — high accuracy record\n"
                "Marathon    x1.2 — long map clear\n"
                "Mod         x1.1 — clear with required mods\n"
                "Pass        x1.0 — clear the map\n"
                "First FC    x1.0 — first Full Combo"
            ),
        },
        "duel": {
            "title": "BEATSKILL SYSTEM",
            "commands": [
                {"name": "bsk", "desc": "Your BeatSkill rating card + matchmaking panel"},
                {"name": "bskduel, bskd <nick> [casual|ranked]", "desc": "Challenge a player (DM ping)"},
                {"name": "bskstatus, bskst", "desc": "Duel status, pick/ban phase & score"},
                {"name": "bskcancel, bskc", "desc": "Cancel your pending challenge"},
                {"name": "bskstats, bsks", "desc": "Your stats across 4 skill axes"},
                {"name": "bskhistory, bskh [N]", "desc": "Last N completed duels"},
            ],
        },
        "account": {
            "title": "ACCOUNT",
            "commands": [
                {"name": "register, reg [username]", "desc": "Register in the system"},
                {"name": "link", "desc": "Link osu! account via OAuth"},
                {"name": "relink", "desc": "Re-authorize osu! OAuth (no data wipe)"},
                {"name": "unlink", "desc": "Unlink osu! account (30d cooldown)"},
                {"name": "start", "desc": "Welcome message"},
                {"name": "help", "desc": "This help menu"},
            ],
        },
        "about": {
            "title": "ABOUT PROJECT",
            "text": (
                "Project 1984 — competitive ecosystem for osu! players.\n\n"
                "HPS v2: earn Hunter Points through weekly bounties.\n"
                "Formula: Base * PHI(map) * PSI(skill gap) * Lambda(length)\n"
                "         * C_pen(combo/misses) * R(result) * T(type)\n"
                "Soft cap: 600 HP via tanh. Vanguard +25 HP (first clear).\n\n"
                "Ranks & divisions (5 per rank, 25 total):\n"
                "Candidate (0-249) -> Member (250-749)\n"
                "Inspector (750-1499) -> Commissioner (1500-2999)\n"
                "Big Brother (3000+, legendary at 10000)\n\n"
                "BeatSkill (BSK): 1v1 rated duels on a 6-map pool\n"
                "4 skill axes: Aim / Speed / Acc / Cons (0-10* scale).\n\n"
                "Big Brother is watching your rank."
            ),
        },
    }

    def generate_help_main_card(self) -> BytesIO:
        W = CARD_WIDTH
        header_h = 28
        panel_h = 56
        gap = 8
        cats = list(self.HELP_CATEGORIES.items())
        content_h = len(cats) * (panel_h + gap) + 50
        footer_h = 40
        H = header_h + 20 + content_h + footer_h

        img, draw = self._create_canvas(W, H)
        self._draw_header(draw, "PROJECT 1984 — HELP", "", W)

        y = header_h + 16
        self._text_center(draw, W // 2, y, "Select a category below", self.font_label, TEXT_SECONDARY)
        y += 28

        cat_icon_names = {
            "osu": "osulogo",
            "hps": "hpssystem",
            "bounty": "bounty",
            "duel": "versus",
            "account": "account",
            "about": "information",
        }

        cat_descriptions = {
            "osu": "Profile, recent scores, leaderboards",
            "hps": "Map potential analysis",
            "bounty": "Bounty list, details, submissions",
            "duel": "BeatSkill 1v1 rating duels",
            "account": "Registration and settings",
            "about": "About this project",
        }

        icon_sz_help = 28
        for code, cat_def in cats:
            self._draw_panel(draw, PADDING_X, y, W - 2 * PADDING_X, panel_h)

            icon_name = cat_icon_names.get(code)
            icon_img = load_icon(icon_name, size=icon_sz_help) if icon_name else None
            text_offset = PADDING_X + 14
            if icon_img:
                icon_y = y + (panel_h - icon_sz_help) // 2
                img.paste(icon_img, (PADDING_X + 12, icon_y), icon_img)
                draw = ImageDraw.Draw(img)
                text_offset = PADDING_X + 12 + icon_sz_help + 10

            title = cat_def["title"]
            draw.text((text_offset, y + 8), title, font=self.font_row, fill=TEXT_PRIMARY)

            desc = cat_descriptions.get(code, "")
            draw.text((text_offset, y + 32), desc, font=self.font_small, fill=TEXT_SECONDARY)

            y += panel_h + gap

        return self._save(img)

    def generate_help_card(self, category: str) -> BytesIO:
        cat_def = self.HELP_CATEGORIES.get(category)
        if not cat_def:
            return self.generate_help_main_card()

        W = CARD_WIDTH
        header_h = 28
        footer_h = 40

        if "text" in cat_def:
            lines = cat_def["text"].split("\n")
            content_h = len(lines) * 24 + 30
            H = header_h + content_h + footer_h

            img, draw = self._create_canvas(W, H)
            self._draw_header(draw, "PROJECT 1984 — HELP", cat_def["title"], W)

            y = header_h + 16
            for line in lines:
                draw.text((PADDING_X, y), line, font=self.font_label, fill=TEXT_PRIMARY if line.strip() else TEXT_SECONDARY)
                y += 24

            footer_y = H - footer_h
            self._draw_footer(draw, img, "BIG BROTHER IS WATCHING YOUR RANK", footer_y, W)
            return self._save(img)

        commands = cat_def.get("commands", [])
        row_h = 44
        content_h = max(len(commands), 1) * row_h + 16
        H = header_h + content_h + footer_h

        img, draw = self._create_canvas(W, H)
        self._draw_header(draw, "PROJECT 1984 — HELP", cat_def["title"], W)

        y = header_h + 8
        for i, cmd in enumerate(commands):
            row_bg = ROW_EVEN if i % 2 == 0 else ROW_ODD
            draw.rectangle([(0, y), (W, y + row_h)], fill=row_bg)

            name = cmd["name"]
            desc = cmd["desc"]

            draw.text((PADDING_X, y + 11), name, font=self.font_row, fill=ACCENT_RED)

            name_bbox = draw.textbbox((0, 0), name, font=self.font_row)
            name_w = name_bbox[2] - name_bbox[0]
            desc_x = max(PADDING_X + name_w + 20, 340)

            draw.text((desc_x, y + 13), desc, font=self.font_label, fill=TEXT_SECONDARY)
            y += row_h

        return self._save(img)

    async def generate_help_main_card_async(self) -> BytesIO:
        return await asyncio.to_thread(self.generate_help_main_card)

    async def generate_help_card_async(self, category: str) -> BytesIO:
        return await asyncio.to_thread(self.generate_help_card, category)

