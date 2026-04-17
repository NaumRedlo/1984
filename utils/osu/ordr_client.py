"""o!rdr API client for rendering osu! replays to video.

Docs: https://ordr.issou.best/docs
"""

import asyncio
from typing import Optional, Callable, Awaitable

import aiohttp

from utils.logger import get_logger

logger = get_logger("utils.ordr_client")

ORDR_API_BASE = "https://apis.issou.best/ordr"

# o!rdr error codes → human-readable messages
ORDR_ERROR_MESSAGES = {
    2: "Ошибка парсинга реплея",
    5: "Повреждённый файл реплея",
    6: "Поддерживается только стандартный osu! режим",
    8: "Карта не найдена",
    9: "Аудио недоступно (копирайт)",
    11: "Мод Autoplay не поддерживается",
    15: "Карта слишком длинная",
    18: "Ошибка рендерера",
    19: "Карта повреждена",
    20: "Карта не удалось загрузить",
    22: "Проблема совместимости",
    27: "Ошибка рендерера (таймаут)",
    28: "Ошибка рендерера (краш)",
    30: "Рейтинг звёзд > 20",
    34: "Неверный URL реплея",
    35: "Отсутствует обязательное поле",
    36: "Слишком много ошибок в недавних реплеях",
    42: "Точность слишком низкая",
}


class OrdrError(Exception):
    """Raised when o!rdr returns an error."""
    def __init__(self, error_code: int, message: str = ""):
        self.error_code = error_code
        self.message = message or ORDR_ERROR_MESSAGES.get(error_code, f"Неизвестная ошибка (код {error_code})")
        super().__init__(self.message)


class OrdrTimeoutError(Exception):
    """Raised when render exceeds timeout."""
    pass


async def submit_render(
    replay_data: Optional[bytes] = None,
    replay_url: Optional[str] = None,
    skin: str = "default",
    resolution: str = "1280x720",
    cursor_size: float = 1.0,
    cursor_trail: bool = True,
    show_pp_counter: bool = True,
    show_scoreboard: bool = False,
    show_key_overlay: bool = True,
    show_hit_error_meter: bool = True,
    show_mods: bool = True,
    show_result_screen: bool = True,
    bg_dim: int = 80,
    api_key: str = "",
) -> int:
    """Submit a replay to o!rdr for rendering.

    Provide either replay_data (bytes) or replay_url (str).
    Returns the renderID on success, raises OrdrError on failure.
    """
    if not replay_data and not replay_url:
        raise OrdrError(-1, "Нужен файл реплея или URL")

    form = aiohttp.FormData()
    if replay_data:
        form.add_field("replayFile", replay_data, filename="replay.osr", content_type="application/octet-stream")
    else:
        form.add_field("replayURL", replay_url)
    form.add_field("skin", skin)
    # If skin is a numeric ID, enable customSkin mode
    if skin.isdigit():
        form.add_field("customSkin", "true")
    form.add_field("resolution", resolution)
    form.add_field("visibility", "UNLISTED")

    # Render settings — o!rdr expects string values
    form.add_field("cursorSize", str(cursor_size))
    form.add_field("cursorTrail", str(cursor_trail).lower())
    form.add_field("showPPCounter", str(show_pp_counter).lower())
    form.add_field("showScoreboard", str(show_scoreboard).lower())
    form.add_field("showKeyOverlay", str(show_key_overlay).lower())
    form.add_field("showHitErrorMeter", str(show_hit_error_meter).lower())
    form.add_field("showMods", str(show_mods).lower())
    form.add_field("showResultScreen", str(show_result_screen).lower())
    form.add_field("inGameBGDim", str(bg_dim))

    if api_key:
        form.add_field("verificationKey", api_key)

    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(f"{ORDR_API_BASE}/renders", data=form) as resp:
            data = await resp.json()

            if resp.status == 201:
                render_id = data.get("renderID")
                logger.info(f"Render submitted successfully: renderID={render_id}")
                return render_id

            error_code = data.get("errorCode", -1)
            error_msg = data.get("message", "")
            logger.warning(f"o!rdr submit failed: HTTP {resp.status}, code={error_code}, msg={error_msg}")
            raise OrdrError(error_code, error_msg or ORDR_ERROR_MESSAGES.get(error_code, f"HTTP {resp.status}"))


async def wait_for_render(
    render_id: int,
    timeout: int = 300,
    poll_interval: int = 5,
    on_progress: Optional[Callable[[str], Awaitable[None]]] = None,
) -> str:
    """Poll o!rdr until the render is done. Returns the video URL.

    on_progress is called with the progress string on each poll (e.g. "Downloading map...").
    Raises OrdrError on render failure, OrdrTimeoutError on timeout.
    """
    elapsed = 0
    last_progress = ""

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        while elapsed < timeout:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

            try:
                async with session.get(f"{ORDR_API_BASE}/renders", params={"renderID": render_id}) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()
            except Exception as e:
                logger.debug(f"Poll error for render {render_id}: {e}")
                continue

            renders = data.get("renders", [])
            if not renders:
                continue

            render = renders[0]
            progress = render.get("progress", "")
            error_code = render.get("errorCode", 0)

            if error_code and error_code != 0:
                error_msg = render.get("errorMessage", "")
                raise OrdrError(error_code, error_msg)

            if progress != last_progress:
                last_progress = progress
                logger.debug(f"Render {render_id} progress: {progress}")
                if on_progress and progress:
                    try:
                        await on_progress(progress)
                    except Exception:
                        pass

            if progress == "Done.":
                video_url = render.get("videoUrl", "")
                if video_url:
                    logger.info(f"Render {render_id} done: {video_url}")
                    return video_url

    raise OrdrTimeoutError(f"Render {render_id} timed out after {timeout}s")


async def download_video(url: str) -> bytes:
    """Download the rendered video from o!rdr."""
    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                raise OrdrError(-1, f"Не удалось скачать видео: HTTP {resp.status}")
            return await resp.read()
