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
                {"name": "bountydetails, bde [id]", "desc": "Bounty details"},
                {"name": "submit [id]", "desc": "Submit bounty entry"},
            ],
        },
        "bounty": {
            "title": "BOUNTY TYPES",
            "text": (
                "First FC — first Full Combo on a map\n"
                "Snipe — beat a specific player's score\n"
                "History — historically set record\n"
                "Accuracy — record by accuracy (98%/99%/100%)\n"
                "Pass — clear the map\n"
                "Mod — clear with HD/HR/DT/FL etc.\n"
                "SS — 100% accuracy\n"
                "Marathon — marathon maps (10:00+)\n"
                "Memory — Flashlight (FL) clear\n"
                "Metronome — lowest Unstable Rate record\n"
                "Easter Egg — precise 'meme' accuracy values"
            ),
        },
        "duel": {
            "title": "BEATSKILL SYSTEM",
            "commands": [
                {"name": "bsk", "desc": "Your BeatSkill rating card + matchmaking panel"},
                {"name": "bskduel, bskd <nick> [casual|ranked]", "desc": "Challenge a player"},
                {"name": "bskstatus, bskst", "desc": "Current duel status & score"},
                {"name": "bskcancel, bskc", "desc": "Cancel your pending challenge"},
                {"name": "bskstats, bsks", "desc": "Your BSK statistics"},
                {"name": "bskhistory, bskh [N]", "desc": "Last N completed duels"},
            ],
        },
        "account": {
            "title": "ACCOUNT",
            "commands": [
                {"name": "register, reg [username]", "desc": "Register in the system"},
                {"name": "link", "desc": "Link osu! account via OAuth"},
                {"name": "unlink", "desc": "Unlink osu! account (30d cooldown)"},
                {"name": "start", "desc": "Welcome message"},
                {"name": "help", "desc": "This help menu"},
            ],
        },
        "about": {
            "title": "ABOUT PROJECT",
            "text": (
                "Project 1984 — competitive ecosystem for osu! players.\n\n"
                "HPS: earn Hunter Points through weekly bounties.\n"
                "Ranks: Candidate → Party Member → Inspector\n"
                "        → High Commissioner → Big Brother\n\n"
                "BeatSkill (BSK): 1v1 rated duels on map pool.\n"
                "4 skill components: Aim / Speed / Acc / Cons.\n"
                "ML model analyses map patterns and refines\n"
                "component weights from real match history.\n\n"
                "Big Brother is watching your rank."
            ),
        },
    }

    def generate_help_main_card(self) -> BytesIO:
        W = CARD_WIDTH
        header_h = 36
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
        header_h = 36
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

