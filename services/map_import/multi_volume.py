"""Multi-volume archive helper for /import multi.

osu! mappack archives routinely run into the 5–15 GB range. Most file
hosts cap a single upload below that (Google Drive: 5 GB without G One,
MediaFire: 4 GB, etc.), so users split with 7-Zip into multi-volume
archives — `pack.7z.001`, `pack.7z.002`, … or `pack.zip.001`, ...

This module:
  * Validates a list of downloaded part paths — same base name, complete
    001..NNN sequence, no gaps.
  * Drives the extraction:
      - .7z multi-volume → spawn `7z x <first_part> -o<out_dir>`
      - .zip split       → concatenate parts byte-wise into a single
                           .zip and let the normal bulk-import flow take
                           it. (Multi-volume zip files written by 7-Zip
                           store the central directory in the last part;
                           concatenation produces a valid zip.)

Both paths rely on external binaries:
  - `7z` (from p7zip-full on Debian/Ubuntu) — required for .7z parts
  - `cat` — used for .zip concatenation (POSIX baseline)

If 7z is missing we surface a clear RuntimeError so the operator can
install it.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from dataclasses import dataclass
from typing import Iterable


# Matches .zip.001 / .7z.042 etc. Anchored to the end of the filename.
_PART_SUFFIX_RE = re.compile(r"^(?P<base>.+\.(?P<kind>zip|7z))\.(?P<num>\d{2,4})$", re.IGNORECASE)


@dataclass(frozen=True)
class MultiVolumeJob:
    base_name: str           # e.g. "pack.7z" or "pack.zip"
    kind: str                # "7z" | "zip"
    parts: tuple[str, ...]   # sorted absolute paths


class MultiVolumeError(RuntimeError):
    """Raised for any user-actionable failure during multi-volume processing."""


def classify_parts(file_paths: Iterable[str]) -> MultiVolumeJob:
    """Group a list of downloaded file paths into a single multi-volume job.

    Validates: every filename matches `.zip.NNN` or `.7z.NNN`, all share
    the same base, and the numeric sequence is 001..len(parts) with no
    gaps. Raises MultiVolumeError on any mismatch with a useful message.
    """
    paths = [str(p) for p in file_paths]
    if len(paths) < 2:
        raise MultiVolumeError(
            "Для multi-volume нужно минимум 2 части."
        )

    matches: list[tuple[str, str, int, str]] = []  # base, kind, num, path
    for p in paths:
        name = os.path.basename(p)
        m = _PART_SUFFIX_RE.match(name)
        if not m:
            raise MultiVolumeError(
                f"Файл `{name}` не похож на часть multi-volume архива "
                f"(ожидаю `*.zip.001`, `*.7z.001`, …)."
            )
        matches.append((m.group("base"), m.group("kind").lower(), int(m.group("num")), p))

    bases = {m[0] for m in matches}
    if len(bases) > 1:
        raise MultiVolumeError(
            f"Части от разных архивов: {sorted(bases)}. Все имена до "
            f"`.NNN` суффикса должны совпадать."
        )
    kinds = {m[1] for m in matches}
    if len(kinds) > 1:
        raise MultiVolumeError(
            f"Смешанные форматы: {sorted(kinds)}. Все части должны быть "
            f"одного формата (`.zip` или `.7z`)."
        )

    matches.sort(key=lambda t: t[2])
    nums = [m[2] for m in matches]
    expected = list(range(1, len(nums) + 1))
    if nums != expected:
        missing = sorted(set(expected) - set(nums))
        raise MultiVolumeError(
            f"Не хватает частей: {missing}. Получено {nums}, "
            f"ожидал последовательность 1..{len(nums)}."
        )

    return MultiVolumeJob(
        base_name=matches[0][0],
        kind=matches[0][1],
        parts=tuple(m[3] for m in matches),
    )


async def assemble_to_archive(job: MultiVolumeJob, out_dir: str) -> str:
    """Combine parts into one usable archive in `out_dir`. Returns the
    path of the resulting file ready for bulk-import.

    For `.7z` multi-volume — extracts in place to `<out_dir>/extracted/`
    via the p7zip binary, then re-zips the result so the existing
    bulk-import (which expects a single .zip/.osz) can consume it.
    For `.zip` split — byte-wise concatenates parts, no re-compression.
    """
    os.makedirs(out_dir, exist_ok=True)

    if job.kind == "zip":
        merged = os.path.join(out_dir, "merged.zip")
        # Stream-concatenate without loading parts into memory.
        with open(merged, "wb") as out:
            for part in job.parts:
                with open(part, "rb") as src:
                    shutil.copyfileobj(src, out, length=4 * 1024 * 1024)
        return merged

    if job.kind == "7z":
        if shutil.which("7z") is None:
            raise MultiVolumeError(
                "На сервере нет утилиты `7z` — поставь `p7zip-full` "
                "(apt) или `p7zip` (alpine/arch), либо разбей архив в "
                "формате `.zip.001/002/...` вместо `.7z`."
            )

        extracted = os.path.join(out_dir, "extracted")
        os.makedirs(extracted, exist_ok=True)
        # 7z reads all volumes automatically when pointed at the first.
        # -y: assume yes on prompts (overwrites). -aos: skip existing.
        proc = await asyncio.create_subprocess_exec(
            "7z", "x", job.parts[0],
            f"-o{extracted}", "-y", "-bso0", "-bsp0", "-bse2",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = (stderr or b"").decode("utf-8", errors="replace").strip()
            raise MultiVolumeError(
                f"7z вернул код {proc.returncode}: {err[:200] or 'без вывода'}"
            )

        # Re-zip the extracted tree into one .zip so the existing
        # bulk-import can swallow it whole. We use stored compression
        # because the source is already compressed (.osz/.mp3/.osu).
        repacked = os.path.join(out_dir, "repacked.zip")
        await _zip_directory(extracted, repacked)
        # Free the disk taken by the intermediate tree.
        shutil.rmtree(extracted, ignore_errors=True)
        return repacked

    raise MultiVolumeError(f"Неизвестный формат частей: {job.kind!r}")


async def _zip_directory(src_dir: str, dest_path: str) -> None:
    """Pack `src_dir` into `dest_path` as a zip with no compression.

    Used to wrap a 7z-extracted tree into the .zip shape that
    `services.bsk.bulk_import.import_from_file` expects.
    """
    import zipfile

    def _work():
        with zipfile.ZipFile(
            dest_path, "w",
            compression=zipfile.ZIP_STORED,
            allowZip64=True,
        ) as zf:
            for root, _dirs, files in os.walk(src_dir):
                for name in files:
                    abs_path = os.path.join(root, name)
                    rel = os.path.relpath(abs_path, src_dir)
                    zf.write(abs_path, rel)

    # Heavy IO — push off the event loop.
    await asyncio.to_thread(_work)


__all__ = [
    "MultiVolumeJob",
    "MultiVolumeError",
    "classify_parts",
    "assemble_to_archive",
]
