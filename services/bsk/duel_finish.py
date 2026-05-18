"""BSK duel finish and expiry."""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram import Bot
from sqlalchemy import select

from db.database import get_db_session
from db.models.bsk_duel import BskDuel
from db.models.bsk_duel_round import BskDuelRound
from db.models.user import User
from services.bsk.composite import composite_score, composite_points, points_multiplier_for
from services.bsk.duel_constants import ACCEPT_TIMEOUT_MINUTES
from services.bsk.duel_telegram import send_or_edit_photo as _send_or_edit_photo
from services.bsk.rating import update_ratings
from services.bsk.ml_inference import predict_round_winner
from utils.formatting.text import escape_html
from utils.logger import get_logger
from utils.telegram_safe import safe_edit_text

logger = get_logger("bsk.duel_finish")


async def _get_user(session, user_id: int):
    return (await session.execute(
        select(User).where(User.id == user_id)
    )).scalar_one_or_none()


async def _finish_duel(bot: Bot, duel_id: int) -> None:
    async with get_db_session() as session:
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()
        if not duel:
            return

        p1 = await _get_user(session, duel.player1_user_id)
        p2 = await _get_user(session, duel.player2_user_id)

        # Extract all values before session closes
        p1_name = p1.osu_username if p1 else "Игрок 1"
        p2_name = p2.osu_username if p2 else "Игрок 2"
        p1_country = (p1.country or '') if p1 else ''
        p2_country = (p2.country or '') if p2 else ''
        s1 = duel.player1_total_score
        s2_score = duel.player2_total_score
        is_test = duel.is_test
        mode = duel.mode
        current_round = duel.current_round
        chat_id = duel.chat_id
        message_id = duel.message_id
        thread_id = duel.message_thread_id
        p1_uid = duel.player1_user_id

        if s1 > s2_score:
            winner_id = duel.player1_user_id
        elif s2_score > s1:
            winner_id = duel.player2_user_id
        else:
            winner_id = None

        duel.status = 'completed'
        duel.completed_at = datetime.now(timezone.utc)
        duel.winner_user_id = winner_id
        duel.pick_candidates = None
        duel.pick_candidates_p1 = None
        duel.pick_candidates_p2 = None
        duel.pick_turn = None
        duel.pick_played = None

        # Update per-user duel win/loss counters (non-test only)
        if not is_test and winner_id is not None:
            if p1:
                if winner_id == duel.player1_user_id:
                    p1.duel_wins = (p1.duel_wins or 0) + 1
                else:
                    p1.duel_losses = (p1.duel_losses or 0) + 1
            if p2:
                if winner_id == duel.player2_user_id:
                    p2.duel_wins = (p2.duel_wins or 0) + 1
                else:
                    p2.duel_losses = (p2.duel_losses or 0) + 1

        await session.commit()

    # Close IRC room if connected
    if chat_id:
        try:
            from services.bancho_irc import get_irc_client
            irc = get_irc_client()
            if irc.connected:
                async with get_db_session() as _irc_sess:
                    _d = (await _irc_sess.execute(
                        select(BskDuel).where(BskDuel.id == duel_id)
                    )).scalar_one_or_none()
                    if _d and _d.osu_match_id:
                        from services.bsk.irc_room import close_room
                        await close_room(irc, int(_d.osu_match_id))
        except Exception as e:
            logger.debug(f"_finish_duel: IRC room close failed: {e}")

    # ── Per-duel rating update (non-test, real winner) ──────────────────
    if not is_test and winner_id is not None:
        try:
            async with get_db_session() as _rsess:
                rounds_for_rating = (await _rsess.execute(
                    select(BskDuelRound)
                    .where(BskDuelRound.duel_id == duel_id)
                    .order_by(BskDuelRound.round_number)
                )).scalars().all()

                # Aggregate map_weights as the average across played rounds
                # (rounds with explicit weights only). Falls back to uniform
                # 0.25 if nothing was recorded.
                w_sum = {'aim': 0.0, 'speed': 0.0, 'acc': 0.0, 'cons': 0.0}
                w_count = 0
                winner_rounds = 0
                loser_rounds = 0
                for r in rounds_for_rating:
                    if r.w_aim is not None or r.w_speed is not None or r.w_acc is not None or r.w_cons is not None:
                        w_sum['aim']   += r.w_aim   or 0.25
                        w_sum['speed'] += r.w_speed or 0.25
                        w_sum['acc']   += r.w_acc   or 0.25
                        w_sum['cons']  += r.w_cons  or 0.25
                        w_count += 1
                    if r.winner_player == 1:
                        if winner_id == p1_uid:
                            winner_rounds += 1
                        else:
                            loser_rounds += 1
                    elif r.winner_player == 2:
                        if winner_id == p1_uid:
                            loser_rounds += 1
                        else:
                            winner_rounds += 1

                if w_count > 0:
                    map_weights = {k: v / w_count for k, v in w_sum.items()}
                else:
                    map_weights = {'aim': 0.25, 'speed': 0.25, 'acc': 0.25, 'cons': 0.25}

            loser_id = duel.player2_user_id if winner_id == duel.player1_user_id else duel.player1_user_id
            winner_pp = float((p1.player_pp if winner_id == p1_uid else p2.player_pp) or 0) if (p1 and p2) else 0.0
            loser_pp  = float((p2.player_pp if winner_id == p1_uid else p1.player_pp) or 0) if (p1 and p2) else 0.0

            w_rating, l_rating, w_old_div, w_new_div, l_old_div, l_new_div = await update_ratings(
                winner_id, loser_id, mode,
                map_weights=map_weights,
                winner_pp=winner_pp, loser_pp=loser_pp,
                winner_rounds=winner_rounds,
                loser_rounds=loser_rounds,
            )

            # Division change notifications
            from services.bsk.division_notify import notify_division_change
            if w_old_div != w_new_div:
                asyncio.create_task(notify_division_change(
                    bot, winner_id, w_old_div, w_new_div, chat_id, thread_id,
                    bsk_points=w_rating.mu_global, mode=mode,
                ))
            if l_old_div != l_new_div:
                asyncio.create_task(notify_division_change(
                    bot, loser_id, l_old_div, l_new_div, chat_id, thread_id,
                    bsk_points=l_rating.mu_global, mode=mode,
                ))

            # Stamp the after-snapshots on the LAST round so the end-card delta
            # aggregator (reads last-non-null *_after) shows the correct duel
            # delta.
            async with get_db_session() as _ssess:
                last_round = (await _ssess.execute(
                    select(BskDuelRound)
                    .where(BskDuelRound.duel_id == duel_id)
                    .order_by(BskDuelRound.round_number.desc())
                )).scalars().first()
                if last_round:
                    if winner_id == p1_uid:
                        last_round.p1_mu_aim_after   = w_rating.mu_aim
                        last_round.p1_mu_speed_after = w_rating.mu_speed
                        last_round.p1_mu_acc_after   = w_rating.mu_acc
                        last_round.p1_mu_cons_after  = w_rating.mu_cons
                        last_round.p2_mu_aim_after   = l_rating.mu_aim
                        last_round.p2_mu_speed_after = l_rating.mu_speed
                        last_round.p2_mu_acc_after   = l_rating.mu_acc
                        last_round.p2_mu_cons_after  = l_rating.mu_cons
                    else:
                        last_round.p2_mu_aim_after   = w_rating.mu_aim
                        last_round.p2_mu_speed_after = w_rating.mu_speed
                        last_round.p2_mu_acc_after   = w_rating.mu_acc
                        last_round.p2_mu_cons_after  = w_rating.mu_cons
                        last_round.p1_mu_aim_after   = l_rating.mu_aim
                        last_round.p1_mu_speed_after = l_rating.mu_speed
                        last_round.p1_mu_acc_after   = l_rating.mu_acc
                        last_round.p1_mu_cons_after  = l_rating.mu_cons
                    await _ssess.commit()
        except Exception as e:
            logger.error(
                f"_finish_duel: per-duel rating update failed for duel {duel_id}: {e}",
                exc_info=True,
            )

    try:
        from services.image import card_renderer
        from db.models.bsk_rating import BskRating as _BskRating

        async with get_db_session() as _fsess:
            rounds_db = (await _fsess.execute(
                select(BskDuelRound)
                .where(BskDuelRound.duel_id == duel_id)
                .order_by(BskDuelRound.round_number)
            )).scalars().all()

            # Compute per-skill deltas from before/after snapshots
            p1_deltas = {}
            p2_deltas = {}
            for comp in ('aim', 'speed', 'acc', 'cons'):
                p1_before = next((getattr(r, f'p1_mu_{comp}_before') for r in rounds_db if getattr(r, f'p1_mu_{comp}_before') is not None), None)
                p1_after  = next((getattr(r, f'p1_mu_{comp}_after')  for r in reversed(rounds_db) if getattr(r, f'p1_mu_{comp}_after') is not None), None)
                p2_before = next((getattr(r, f'p2_mu_{comp}_before') for r in rounds_db if getattr(r, f'p2_mu_{comp}_before') is not None), None)
                p2_after  = next((getattr(r, f'p2_mu_{comp}_after')  for r in reversed(rounds_db) if getattr(r, f'p2_mu_{comp}_after') is not None), None)
                p1_deltas[comp] = (p1_after - p1_before) if (p1_before is not None and p1_after is not None) else None
                p2_deltas[comp] = (p2_after - p2_before) if (p2_before is not None and p2_after is not None) else None

        round_history = [
            {
                'round_number': r.round_number,
                'beatmap_title': r.beatmap_title or 'Unknown',
                'star_rating': float(r.star_rating or 0),
                'winner': r.winner_player or 0,
                'p1_points': int(r.player1_points or 0),
                'p2_points': int(r.player2_points or 0),
            }
            for r in rounds_db
        ]

        winner_num = 1 if (winner_id and winner_id == p1_uid) else 2 if winner_id else 0
        end_card_data = {
            'p1_name': p1_name,
            'p2_name': p2_name,
            'p1_country': p1_country,
            'p2_country': p2_country,
            'p1_cover_url': (p1.cover_url or '') if p1 else '',
            'p2_cover_url': (p2.cover_url or '') if p2 else '',
            'winner': winner_num,
            'score_p1': int(s1),
            'score_p2': int(s2_score),
            'mode': mode,
            'total_rounds': current_round,
            'is_test': is_test,
            'rounds': round_history,
        }
        for comp in ('aim', 'speed', 'acc', 'cons'):
            end_card_data[f'p1_delta_{comp}'] = p1_deltas.get(comp)
            end_card_data[f'p2_delta_{comp}'] = p2_deltas.get(comp)

        img_bytes = await card_renderer.generate_bsk_duel_end_card_async(end_card_data)
        caption = "🎉 <b>ДУЭЛЬ ЗАВЕРШЕНА!</b>" + (" <i>[ТЕСТ]</i>" if is_test else "")
        await _send_or_edit_photo(
            bot, chat_id, message_id,
            img_bytes, caption=caption,
            thread_id=thread_id,
        )
    except Exception as e:
        logger.error(f"_finish_duel: failed to send end card: {e}", exc_info=True)


async def _expire_duel(bot: Bot, duel_id: int, osu_api) -> None:
    await asyncio.sleep(ACCEPT_TIMEOUT_MINUTES * 60)
    async with get_db_session() as session:
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()
        if not duel or duel.status != 'pending':
            return
        duel.status = 'expired'
        p2 = await _get_user(session, duel.player2_user_id)
        await session.commit()

        opp_name = escape_html(p2.osu_username) if p2 else "Соперник"
        await safe_edit_text(
            bot,
            f"⏰ <b>Вызов истёк</b>\n\n"
            f"<i>{opp_name} не ответил в течение {ACCEPT_TIMEOUT_MINUTES} минут.</i>\n"
            "Дуэль отменена.",
            chat_id=duel.chat_id,
            message_id=duel.message_id,
            parse_mode="HTML",
        )
