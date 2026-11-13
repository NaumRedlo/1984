from aiogram.types import BufferedInputFile

from services.leaderboard.marks import mark_rows, team_rows
from services.leaderboard.service import build_absolute_board, build_delta_board

async def build_absolute_card(session, key: str, chat_id: int, page: int = 0, *,
                              viewer_user_id=None, lang: str = "en"):
    import asyncio as _asyncio

    from services.leaderboard.delta_card import build_absolute_payload
    from services.image.render.leaderboard_delta import render_delta_leaderboard

    board = await build_absolute_board(session, key, chat_id, page,
                                       viewer_user_id=viewer_user_id, lang=lang)
    await mark_rows(session, board.get("rows", []) + [board.get("self_row")])
    await team_rows(session, board.get("rows", []) + [board.get("self_row")])
    png = await _asyncio.to_thread(render_delta_leaderboard, build_absolute_payload(board, lang))
    photo = BufferedInputFile(png, filename=f"leaderboard_{key}.png")
    return photo, board

async def build_delta_card(session, key: str, chat_id: int, page: int = 0, *,
                           viewer_user_id=None, lang: str = "en"):
    import asyncio as _asyncio

    from services.leaderboard.delta_card import build_payload
    from services.image.render.leaderboard_delta import render_delta_leaderboard

    board = await build_delta_board(session, key, chat_id, page,
                                    viewer_user_id=viewer_user_id, lang=lang)
    await mark_rows(session, board.get("rows", []) + [board.get("self_row")])
    await team_rows(session, board.get("rows", []) + [board.get("self_row")])
    payload = build_payload(board, lang)
    png = await _asyncio.to_thread(render_delta_leaderboard, payload)
    photo = BufferedInputFile(png, filename=f"leaderboard_{key}_delta.png")
    return photo, board
