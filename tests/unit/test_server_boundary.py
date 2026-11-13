import ast
import os
from collections import deque

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PACKAGES = ("bot", "services", "tasks", "utils", "db", "config")

SERVER_ROOTS = (
    "services.oauth.server",
    "services.render_farm.http",
    "services.render_farm.events",
    "services.render_farm.telegram_rows",
    "services.duels.http",
    "tasks.duels",
    "tasks.live_tracker",
    "tasks.map_checks",
    "tasks.profile_updater",
)

BOT_SIDE = ("bot", "tasks.membership", "services.render_farm.orders", "services.render_farm.skin_upload",
            "services.render_farm.telegram_direct")

SERVER_MEMORY = (
    "services.oauth.server",
    "services.render_farm.http",
    "services.render_farm.events",
    "services.render_farm.invites",
    "services.render_farm.pairing",
    "services.render_farm.queue",
    "services.render_farm.roster",
    "services.render_farm.channels",
    "services.duels.live",
    "services.duels.training",
    "tasks.live_tracker",
)

SERVER_IMPORTS_TELEGRAM = set()

SERVER_IMPORTS_BOT = set()

BOT_READS_SERVER_MEMORY = {
    ("bot.handlers.start.handlers", "services.render_farm.invites"),
    ("bot.handlers.start.handlers", "services.render_farm.pairing"),
    ("services.render_farm.orders", "services.render_farm.invites"),
    ("services.render_farm.orders", "services.render_farm.queue"),
    ("services.render_farm.orders", "services.render_farm.roster"),
}


def _modules():
    found = {}
    for package in PACKAGES:
        for folder, _, files in os.walk(os.path.join(ROOT, package)):
            if "__pycache__" in folder:
                continue
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(folder, name)
                dotted = os.path.relpath(path, ROOT)[:-3].replace(os.sep, ".")
                if dotted.endswith(".__init__"):
                    dotted = dotted[: -len(".__init__")]
                found[dotted] = path
    return found


MODULES = _modules()


def _known(name):
    parts = name.split(".")
    while parts:
        dotted = ".".join(parts)
        if dotted in MODULES:
            return dotted
        parts.pop()
    return None


def _imports(module):
    path = MODULES[module]
    package = module if path.endswith("__init__.py") else module.rpartition(".")[0]
    with open(path, encoding="utf-8") as source:
        tree = ast.parse(source.read())
    named = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            named.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                above = package.split(".")
                above = above[: len(above) - (node.level - 1)]
                base = ".".join([part for part in above if part] + ([base] if base else []))
            named.add(base)
            named.update(f"{base}.{alias.name}" for alias in node.names)
    return named


def _inside(module, groups):
    return any(module == group or module.startswith(group + ".") for group in groups)


def _server_reach():
    seen, telegram, bot = set(), set(), set()
    waiting = deque(SERVER_ROOTS)
    while waiting:
        module = waiting.popleft()
        if module in seen:
            continue
        seen.add(module)
        for name in _imports(module):
            if name == "aiogram" or name.startswith("aiogram."):
                telegram.add(module)
                continue
            target = _known(name)
            if target is None:
                continue
            if _inside(target, ("bot",)):
                bot.add((module, target))
            elif target not in seen:
                waiting.append(target)
    return seen, telegram, bot


def _bot_reads():
    found = set()
    for module in MODULES:
        if not _inside(module, BOT_SIDE) or module == "bot.main":
            continue
        for name in _imports(module):
            target = _known(name)
            if target in SERVER_MEMORY:
                found.add((module, target))
    return found


def test_the_app_side_reaches_telegram_only_where_the_list_says():
    _, telegram, _ = _server_reach()
    assert telegram == SERVER_IMPORTS_TELEGRAM


def test_the_app_side_reaches_the_bot_package_only_where_the_list_says():
    _, _, bot = _server_reach()
    assert bot == SERVER_IMPORTS_BOT


def test_the_bot_reads_the_app_side_memory_only_where_the_list_says():
    assert _bot_reads() == BOT_READS_SERVER_MEMORY


def test_the_routes_know_telegram_only_through_the_port():
    with open(MODULES["services.render_farm.http"], encoding="utf-8") as source:
        text = source.read()
    assert "_bot" not in text
    assert not any(name == "aiogram" or name.startswith("aiogram.") for name in _imports("services.render_farm.http"))


def test_every_named_module_exists():
    for module in SERVER_ROOTS + SERVER_MEMORY:
        assert module in MODULES, module
