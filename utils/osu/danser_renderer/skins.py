"""Custom skin (.osk) management on the worker's danser Skins dir: sanitize a
folder name, list, install (zip-slip guarded), delete, rename.
"""

import io
import os
import re
import shutil
import zipfile

from utils.logger import get_logger
from config.settings import DANSER_SKINS_DIR
from utils.osu.danser_renderer.errors import DanserError

logger = get_logger("utils.danser")

# Deny-list, not an allow-list: real osu! skin names use parentheses, brackets,
# punctuation, and non-Latin scripts ("Skin (v2)", "★Skin★", "スキン") — only the
# genuinely filesystem-dangerous characters are stripped (path separators, NUL,
# other control chars). Traversal via a bare "." / ".." (no slashes needed for
# os.path.join to walk up) is blocked explicitly below since dots are otherwise
# allowed through.
_SKIN_NAME_DENY_RE = re.compile(r"[\\/\x00-\x1f\x7f]+")


def sanitize_skin_name(name: str) -> str:
    """A safe folder name for a skin (no path separators / traversal / control
    characters) — otherwise permissive."""
    name = os.path.basename((name or "").strip())
    if name.lower().endswith(".osk"):
        name = name[:-4]
    name = _SKIN_NAME_DENY_RE.sub("", name).strip()
    if name in (".", ".."):
        return ""
    return name[:64]


def list_skins() -> list:
    """Skin folder names present in DANSER_SKINS_DIR."""
    skins_dir = os.path.expanduser(DANSER_SKINS_DIR)
    if not os.path.isdir(skins_dir):
        return []
    return sorted(
        e for e in os.listdir(skins_dir)
        if os.path.isdir(os.path.join(skins_dir, e))
    )


def install_skin(osk_bytes: bytes, name: str) -> str:
    """Unpack an .osk (a zip) into DANSER_SKINS_DIR/<name>/. Returns the installed
    skin name. Raises DanserError on a bad/unsafe archive."""
    safe = sanitize_skin_name(name)
    if not safe:
        raise DanserError("Некорректное имя скина.")
    skins_dir = os.path.expanduser(DANSER_SKINS_DIR)
    dest = os.path.join(skins_dir, safe)
    os.makedirs(dest, exist_ok=True)

    dest_abs = os.path.abspath(dest)
    try:
        with zipfile.ZipFile(io.BytesIO(osk_bytes)) as zf:
            for member in zf.namelist():
                if member.endswith("/"):
                    continue
                target = os.path.normpath(os.path.join(dest_abs, member))
                # Reject absolute paths / traversal (zip-slip).
                if target != dest_abs and not target.startswith(dest_abs + os.sep):
                    raise DanserError("Небезопасный архив скина (path traversal).")
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(member) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
    except zipfile.BadZipFile:
        raise DanserError("Файл не является корректным .osk (zip).")

    logger.info(f"Installed skin '{safe}' into {dest}")
    return safe


def delete_skin(name: str) -> None:
    """Remove a skin folder from DANSER_SKINS_DIR. Raises DanserError if the
    name is invalid or the skin doesn't exist."""
    safe = sanitize_skin_name(name)
    if not safe or safe != name:
        raise DanserError("Некорректное имя скина.")
    skins_dir = os.path.expanduser(DANSER_SKINS_DIR)
    target = os.path.join(skins_dir, safe)
    if not os.path.isdir(target):
        raise DanserError("Скин не найден.")
    shutil.rmtree(target)
    logger.info(f"Deleted skin '{safe}'")


def rename_skin(name: str, new_name: str) -> str:
    """Rename a skin folder. Returns the sanitized new name actually used.
    Raises DanserError if the source is missing/invalid or the target name is
    invalid or already taken."""
    safe = sanitize_skin_name(name)
    if not safe or safe != name:
        raise DanserError("Некорректное текущее имя скина.")
    safe_new = sanitize_skin_name(new_name)
    if not safe_new:
        raise DanserError("Некорректное новое имя скина.")
    skins_dir = os.path.expanduser(DANSER_SKINS_DIR)
    src = os.path.join(skins_dir, safe)
    if not os.path.isdir(src):
        raise DanserError("Скин не найден.")
    dest = os.path.join(skins_dir, safe_new)
    if os.path.exists(dest):
        raise DanserError("Скин с таким именем уже существует.")
    os.rename(src, dest)
    logger.info(f"Renamed skin '{safe}' -> '{safe_new}'")
    return safe_new
