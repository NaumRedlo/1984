import importlib
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils.memory_report import resident

STAGES = (
    "aiohttp",
    "sqlalchemy.ext.asyncio",
    "asyncpg",
    "PIL.Image",
    "fontTools.ttLib",
    "cryptography.fernet",
    "db.models",
    "utils.osu.api_client",
    "services.image",
    "services.render_farm.http",
    "services.oauth.server",
    "tasks.live_tracker",
    "tasks.profile_updater",
    "tasks.map_checks",
    "tasks.duels",
    "aiogram",
    "bot.main",
)


def main() -> None:
    if "--eager" in sys.argv:
        os.environ["AIOGRAM_EAGER_MODELS"] = "1"
    from utils.aiogram_lazy import install

    print("Telegram types:", "built on first use" if install() else "all built at import")
    before = resident()
    print(f"{'python alone':<28} {before:7.1f} MB")
    for name in STAGES:
        began = time.perf_counter()
        try:
            importlib.import_module(name)
        except Exception as exc:
            print(f"{name:<28} failed: {type(exc).__name__}: {exc}")
            continue
        now = resident()
        print(f"{name:<28} {now:7.1f} MB  {now - before:+7.1f}  {time.perf_counter() - began:5.2f} s")
        before = now
    from utils import aiogram_lazy

    if aiogram_lazy.installed():
        fine = aiogram_lazy.verify()
        print(f"{'after an update and a send':<28} {resident():7.1f} MB  {'checked' if fine else 'fell back'}")


if __name__ == "__main__":
    main()
