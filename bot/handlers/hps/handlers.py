from aiogram import Router, types
from aiogram.types import BufferedInputFile
from sqlalchemy import select

from db.models.user import User
from db.models.bsk_map_pool import BskMapPool
from db.database import get_db_session
from services.hps.bsk_user_skill import compute_bsk_user_skill
from services.image import card_renderer
from utils.hp_calculator import (
    MapInfo,
    PlayerSkill,
    ScoreStats,
    calculate_hps,
)
from utils.osu.helpers import extract_beatmap_id
from utils.logger import get_logger
from utils.formatting.text import format_error
from bot.filters import TextTriggerFilter, TriggerArgs
from services.oauth.token_manager import get_valid_token

logger = get_logger(__name__)

router = Router(name="hps")


def _map_info_from_payload(
    *,
    pool: BskMapPool | None,
    star_rating: float,
    od: float,
    drain_time: int,
    max_combo: int,
) -> tuple[MapInfo, bool]:
    """Build a MapInfo from a beatmap payload + optional bsk_map_pool row.

    Mirrors services.hps.payout._map_info_for_bounty, but consumes osu! API
    fields directly because /hps operates on arbitrary maps that don't have a
    Bounty row.
    """
    sr = float(star_rating or 0.0)
    if pool is not None:
        return MapInfo(
            aim_stars=float(pool.aim_stars   if pool.aim_stars   is not None else sr),
            speed_stars=float(pool.speed_stars if pool.speed_stars is not None else sr),
            acc_stars=float(pool.acc_stars   if pool.acc_stars   is not None else sr),
            cons_stars=float(pool.cons_stars  if pool.cons_stars  is not None else sr),
            w_aim=float(pool.w_aim   if pool.w_aim   is not None else 0.25),
            w_speed=float(pool.w_speed if pool.w_speed is not None else 0.25),
            w_acc=float(pool.w_acc   if pool.w_acc   is not None else 0.25),
            w_cons=float(pool.w_cons if pool.w_cons is not None else 0.25),
            od=float(od or 0.0),
            drain_time_seconds=int(drain_time or 0),
            max_combo=int(max_combo or 0),
        ), False
    return MapInfo.fallback_from_sr(
        star_rating=sr,
        od=float(od or 0.0),
        drain_time=int(drain_time or 0),
        max_combo=int(max_combo or 0),
    ), True


@router.message(TextTriggerFilter("hps"))
async def calculate_hps_command(
    message: types.Message,
    trigger_args: TriggerArgs,
    osu_api_client,
):
    user_id = message.from_user.id
    args = trigger_args.args

    wait_msg = None
    try:
        async with get_db_session() as session:
            stmt = select(User).where(User.telegram_id == user_id)
            user = (await session.execute(stmt)).scalar_one_or_none()

            if not user:
                await message.answer(
                    format_error("Вы не зарегистрированы. Используйте register [никнейм]"),
                    parse_mode="HTML",
                )
                return

            player_pp = user.player_pp or 0
            osu_user_id = user.osu_user_id
            user_db_id = user.id

            # Snapshot BSK_user inside the same session — Ψ relies on this and
            # we need the value before we drop the session to talk to osu!.
            skill = await compute_bsk_user_skill(user, session)

        token = await get_valid_token(user_db_id)
        is_last = not args or args.strip().lower() == "last"
        wait_msg = await message.answer("Обработка запроса...")

        recent_score: dict | None = None
        if is_last:
            await wait_msg.edit_text("Загрузка последней сыгранной карты...")
            scores = await osu_api_client.get_user_recent_scores(osu_user_id, limit=1, oauth_token=token)

            if not scores:
                await wait_msg.edit_text(format_error("Не удалось найти недавние скоры."))
                return

            recent_score = scores[0]
            beatmap = recent_score.get("beatmap", {})
            beatmapset = recent_score.get("beatmapset", {})
        else:
            beatmap_id = extract_beatmap_id(args)
            if not beatmap_id:
                await wait_msg.edit_text(format_error("Не удалось распознать ID или ссылку на карту."))
                return

            await wait_msg.edit_text(f"Загрузка информации о карте ID: {beatmap_id}...")
            beatmap = await osu_api_client.get_beatmap(beatmap_id)
            if not beatmap:
                await wait_msg.edit_text(format_error(f"Карта {beatmap_id} не найдена."))
                return
            beatmapset = beatmap.get("beatmapset", {})

        star_rating = float(beatmap.get("difficulty_rating", 0.0))
        total_length = int(beatmap.get("total_length", 0))
        map_version = beatmap.get("version", "Unknown")
        artist = beatmapset.get("artist", "Unknown")
        title = beatmapset.get("title", "Unknown")
        map_title = f"{artist} - {title}"
        map_od = float(beatmap.get("accuracy", 0.0))
        map_max_combo = int(beatmap.get("max_combo", 0))
        map_beatmap_id = int(beatmap.get("id", 0))

        # If the map is in the BSK pool, /hps shows per-axis stars (drives Ψ).
        async with get_db_session() as session:
            pool_row = (await session.execute(
                select(BskMapPool).where(BskMapPool.beatmap_id == map_beatmap_id)
            )).scalar_one_or_none() if map_beatmap_id else None

        map_info, used_fallback = _map_info_from_payload(
            pool=pool_row,
            star_rating=star_rating,
            od=map_od,
            drain_time=total_length,
            max_combo=map_max_combo,
        )
        player_skill = PlayerSkill(
            aim=skill.aim, speed=skill.speed, acc=skill.acc, cons=skill.cons,
        )

        # If we got here from `last`, derive a real UR from the player's actual
        # recent score on this map.  Otherwise (arbitrary map ID, no score)
        # leave it as None so Ω = 1.0 (neutral) and the breakdown reflects the
        # bare Φ·Ψ·Λ·C_pen contribution rather than a fabricated number.
        # Three reference scenarios — combo/misses vary, UR stays at the player's
        # real (or neutral) value so all panels share the same Ω.  Participation
        # (combo=0) collapses to HP=0 via C_pen=sqrt(0)=0 so it is not previewed.
        scenarios = [
            ("Win",         "win",       map_max_combo, 0),
            ("Condition",   "condition", map_max_combo, 0),
            ("Partial 60%", "partial",   int(map_max_combo * 0.6), 3),
        ]

        results = []
        for label, rt, combo, misses in scenarios:
            res = calculate_hps(
                result_type=rt,
                map_info=map_info,
                player_skill=player_skill,
                score=ScoreStats(
                    n_300=combo, n_100=0, n_50=0, misses=misses, combo=combo,
                ),
                is_first_submission=False,
            )
            results.append((label, res))

        # First scenario carries the breakdown we display in the card.
        _, ref = results[0]

        # Per-axis BSK_map (real if pool hit; SR-fallback if not — same value
        # repeated for all 4 axes by MapInfo.fallback_from_sr).
        bsk_map_axes = {
            'aim':   map_info.aim_stars,
            'speed': map_info.speed_stars,
            'acc':   map_info.acc_stars,
            'cons':  map_info.cons_stars,
        }
        bsk_user_axes = {
            'aim':   skill.aim,
            'speed': skill.speed,
            'acc':   skill.acc,
            'cons':  skill.cons,
        }
        # Module multiplier (no R, no Vanguard) — banner number on the card.
        total_multiplier = (
            ref['phi'] * ref['psi'] * ref['omega']
            * ref['lambda'] * ref['c_pen']
        )

        scenarios_for_card = [
            {'name': label, 'hp_reward': res['final_hp'], 'r': res['r']}
            for (label, res) in results
        ]

        card_data = {
            'beatmapset_id': beatmapset.get('id', 0) if beatmapset else 0,
            'creator_id':    beatmapset.get('user_id', 0) if beatmapset else 0,
            'map_title':     map_title,
            'map_version':   map_version,
            'creator':       beatmapset.get('creator', '') if beatmapset else '',
            'star_rating':   star_rating,
            'duration':      total_length,
            'bpm':           float(beatmap.get('bpm', 0.0) or 0.0),
            'max_combo':     map_max_combo,
            'od':            map_od,
            'bsk_map':       ref['bsk_map'],
            'delta':         ref['delta'],
            'bsk_map_axes':  bsk_map_axes,
            'bsk_user_axes': bsk_user_axes,
            'in_pool':       not used_fallback,
            'scenarios':     scenarios_for_card,
            'breakdown':     ref,
            'total_multiplier': total_multiplier,
        }

        img_bytes = await card_renderer.generate_hps_card_async(card_data)
        await wait_msg.delete()
        await message.answer_photo(
            photo=BufferedInputFile(img_bytes.getvalue(), filename='hps.png'),
        )
        wait_msg = None

    except Exception:
        logger.exception(f"Critical error in /hps for user {message.from_user.id}")
        error_text = format_error("Внутренняя ошибка при расчёте HPS.")

        if wait_msg:
            await wait_msg.edit_text(error_text, parse_mode="HTML")
        else:
            await message.answer(error_text, parse_mode="HTML")
