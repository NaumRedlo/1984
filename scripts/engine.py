#!/usr/bin/env python3

import argparse
import hashlib
import os
import platform
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.path.expanduser("~/.dossier")
ENGINES = os.path.join(HOME, "engines")

CURRENT = os.path.join(HOME, "engine")

REPO = "NaumRedlo/Dossier"

MAX_BYTES = 200 * 1024 * 1024

class Unavailable(RuntimeError):
    pass

def wanted_tag(requirements: str = "") -> str:
    path = requirements or os.path.join(ROOT, "requirements.txt")
    try:
        body = open(path, encoding="utf-8").read()
    except OSError as exc:
        raise Unavailable(f"нет {path}: {exc}") from exc

    found = re.search(r"^\s*dossier\s*@\s*git\+[^\s@]+@([^\s#]+)", body, re.M)
    if not found:
        raise Unavailable(
            f"{path} не называет версию движка — строка вида\n"
            f"  dossier @ git+https://github.com/{REPO}@<tag>#subdirectory=client"
        )
    return found.group(1)

def slug() -> str:
    machine = platform.machine().lower()
    if sys.platform.startswith("linux") and machine in ("x86_64", "amd64"):
        return "linux-x64"
    if sys.platform == "darwin" and machine in ("arm64", "aarch64"):
        return "macos-arm64"
    if sys.platform == "win32" and machine in ("x86_64", "amd64"):
        return "windows-x64"
    raise Unavailable(
        f"для {sys.platform}/{machine} релиза нет — собираются linux-x64, "
        f"macos-arm64 и windows-x64.\n"
        f"Собери из исходников: git clone https://github.com/{REPO} && "
        f"cd Dossier && cargo build --release"
    )

def installed() -> str:
    try:
        return os.path.basename(os.path.realpath(CURRENT))
    except OSError:
        return ""

def _fetch(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=120) as reply:
            body = reply.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise Unavailable(f"нет такого файла в релизе:\n  {url}") from exc
        raise Unavailable(f"{url}: {exc}") from exc
    except urllib.error.URLError as exc:
        raise Unavailable(f"не удалось скачать {url}: {exc.reason}") from exc
    if len(body) > MAX_BYTES:
        raise Unavailable(f"{url} больше {MAX_BYTES // 1024 // 1024} МБ — это не релиз")
    return body

def download(tag: str, named: str) -> str:
    base = f"https://github.com/{REPO}/releases/download/{tag}"
    archive = f"dossier-{tag}-{slug()}.zip"

    print(f"  беру {archive} из {tag}…")
    body = _fetch(f"{base}/{archive}")

    said = _fetch(f"{base}/{archive}.sha256").decode("utf-8", "replace").split()
    expected = said[0] if said else ""
    got = hashlib.sha256(body).hexdigest()
    if not expected:
        raise Unavailable(f"{archive}.sha256 пустой — нечем проверить скачанное")
    if got != expected:
        raise Unavailable(
            f"скачанное не совпало с суммой, опубликованной рядом:\n"
            f"  ожидалось {expected}\n  получилось {got}\n"
            f"Ничего не распаковано."
        )
    print(f"  сумма сошлась: {got[:16]}…")

    os.makedirs(ENGINES, exist_ok=True)
    landing = os.path.join(ENGINES, named)
    staging = landing + ".unpacking"
    shutil.rmtree(staging, ignore_errors=True)

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as handle:
        handle.write(body)
        temporary = handle.name
    try:
        with zipfile.ZipFile(temporary) as archive_file:

            archive_file.extractall(staging)
    finally:
        os.unlink(temporary)

    inner = [name for name in os.listdir(staging)
             if os.path.isdir(os.path.join(staging, name))]
    made = os.path.join(staging, inner[0]) if len(inner) == 1 else staging

    shutil.rmtree(landing, ignore_errors=True)
    shutil.move(made, landing)
    shutil.rmtree(staging, ignore_errors=True)

    with open(os.path.join(landing, ".sha256"), "w", encoding="utf-8") as handle:
        handle.write(got)

    for name in ("dossier", "dossier-worker"):
        binary = os.path.join(landing, name)
        if os.path.isfile(binary):
            os.chmod(binary, 0o755)
    return landing

def point_at(landing: str) -> None:
    os.makedirs(HOME, exist_ok=True)
    beside = CURRENT + ".new"
    if os.path.islink(beside) or os.path.exists(beside):
        os.unlink(beside)
    os.symlink(landing, beside)
    os.replace(beside, CURRENT)

def published(tag: str) -> str:
    base = f"https://github.com/{REPO}/releases/download/{tag}"
    try:
        said = _fetch(f"{base}/dossier-{tag}-{slug()}.zip.sha256")
    except Unavailable:
        return ""
    parts = said.decode("utf-8", "replace").split()
    return parts[0] if parts else ""

def _here(named: str) -> str:
    try:
        with open(os.path.join(ENGINES, named, ".sha256"), encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""

def ensure(*, force: bool = False) -> str:
    tag = wanted_tag()
    named = f"{tag}-{slug()}"
    binary = os.path.join(CURRENT, "dossier.exe" if os.name == "nt" else "dossier")

    if not force and installed() == named and os.path.isfile(binary):

        theirs = published(tag)
        if not theirs or theirs == _here(named):
            print(f"  уже {tag} — ничего делать не нужно")
            return binary
        print(f"  {tag} на месте, но он переехал — беру заново")

    landing = download(tag, named)
    point_at(landing)
    print(f"  теперь {tag}: {CURRENT} → {landing}")
    return binary

def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--force", action="store_true",
                        help="скачать заново, даже если версия та же")
    parser.add_argument("--list", action="store_true",
                        help="какие версии уже лежат на этой машине")
    parser.add_argument("--print-bin", action="store_true",
                        help="напечатать путь и ничего больше — для .env")
    options = parser.parse_args()

    try:
        if options.list:
            current = installed()
            for name in sorted(os.listdir(ENGINES)) if os.path.isdir(ENGINES) else []:
                print(f"  {'→' if name == current else ' '} {name}")
            return 0

        binary = ensure(force=options.force)
        if options.print_bin:
            print(binary)
        else:
            said = os.popen(f'"{binary}" --version 2>/dev/null').read().strip()
            print(f"  движок: {said or 'не отвечает на --version'}")
            print(f"\n  В .env — один раз и больше не менять:\n"
                  f"    DOSSIER_BIN={binary}")
    except Unavailable as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
