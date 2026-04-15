"""
Проверка целостности архитектуры: импорты, шимы, роутеры.
"""
import ast
import importlib
import pkgutil
import sys

import pytest


SHIM_FILES = [
    "bot.handlers.auth_handlers",
    "bot.handlers.admin_handlers",
    "bot.handlers.profile_handlers",
    "bot.handlers.recent_handlers",
    "bot.handlers.compare_handlers",
    "bot.handlers.duel_handlers",
    "bot.handlers.start_handlers",
    "bot.handlers.help_handlers",
    "bot.handlers.hps_handlers",
    "bot.handlers.bounty_handlers",
    "bot.handlers.leaderboard_handlers",
    "utils.osu_api_client",
    "utils.osu_helpers",
    "utils.text_utils",
    "utils.resolve_user",
    "services.image_generator",
    "services.duel_manager",
]

FEATURE_PACKAGES = [
    "bot.handlers.auth",
    "bot.handlers.admin",
    "bot.handlers.profile",
    "bot.handlers.common",
    "bot.handlers.start",
    "bot.handlers.duel",
    "bot.handlers.hps",
    "bot.handlers.bounty",
    "bot.handlers.leaderboard",
]

UTIL_PACKAGES = [
    "utils.formatting",
    "utils.osu",
]


class TestModuleImports:
    """Все модули проекта должны импортироваться без ошибок."""

    @pytest.mark.parametrize("package", FEATURE_PACKAGES + UTIL_PACKAGES)
    def test_package_importable(self, package):
        mod = importlib.import_module(package)
        assert mod is not None

    @pytest.mark.parametrize("shim", SHIM_FILES)
    def test_shim_importable(self, shim):
        mod = importlib.import_module(shim)
        assert mod is not None


class TestShimIntegrity:
    """Шим-файлы не должны содержать собственных функций или классов."""

    @pytest.mark.parametrize("shim", SHIM_FILES)
    def test_shim_has_no_definitions(self, shim):
        mod = importlib.import_module(shim)
        source_file = mod.__file__
        if source_file is None:
            pytest.skip("нет исходника")

        with open(source_file, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())

        defs = [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        assert defs == [], f"Шим {shim} содержит определения: {[d.name for d in defs]}"


class TestRouterRegistration:
    """Каждый handler-пакет экспортирует router."""

    @pytest.mark.parametrize("package", FEATURE_PACKAGES)
    def test_router_exported(self, package):
        mod = importlib.import_module(package)
        assert hasattr(mod, "router"), f"{package} не экспортирует router"

    def test_no_duplicate_router_names(self):
        names = []
        for package in FEATURE_PACKAGES:
            mod = importlib.import_module(package)
            r = getattr(mod, "router", None)
            if r and hasattr(r, "name"):
                names.append(r.name)
        # имена роутеров не должны повторяться
        assert len(names) == len(set(names)), f"Дубли имён роутеров: {names}"


class TestNoCircularImports:
    """Импорт основных пакетов не вызывает циклических зависимостей."""

    CORE_MODULES = [
        "bot.main",
        "services.image",
        "services.duel",
        "utils.hp_calculator",
        "utils.formatting.text",
        "utils.osu.helpers",
        "utils.osu.resolve_user",
        "db.models",
    ]

    @pytest.mark.parametrize("module", CORE_MODULES)
    def test_import_succeeds(self, module):
        mod = importlib.import_module(module)
        assert mod is not None
