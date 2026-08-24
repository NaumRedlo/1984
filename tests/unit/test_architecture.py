import ast
import importlib

import pytest


SHIM_FILES = [
    "utils.osu_helpers",
    "utils.text_utils",
]

FEATURE_PACKAGES = [
    "bot.handlers.auth",
    "bot.handlers.admin",
    "bot.handlers.profile",
    "bot.handlers.common",
    "bot.handlers.start",
    "bot.handlers.leaderboard",
]

UTIL_PACKAGES = [
    "utils.formatting",
    "utils.osu",
]


class TestModuleImports:
    @pytest.mark.parametrize("package", FEATURE_PACKAGES + UTIL_PACKAGES)
    def test_package_importable(self, package):
        mod = importlib.import_module(package)
        assert mod is not None

    @pytest.mark.parametrize("shim", SHIM_FILES)
    def test_shim_importable(self, shim):
        mod = importlib.import_module(shim)
        assert mod is not None


class TestShimIntegrity:
    @pytest.mark.parametrize("shim", SHIM_FILES)
    def test_shim_has_no_definitions(self, shim):
        mod = importlib.import_module(shim)
        source_file = mod.__file__
        if source_file is None:
            pytest.skip("no source code")

        with open(source_file, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())

        defs = [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        assert defs == [], f"Shim {shim} contains definitions: {[d.name for d in defs]}"


class TestRouterRegistration:
    @pytest.mark.parametrize("package", FEATURE_PACKAGES)
    def test_router_exported(self, package):
        mod = importlib.import_module(package)
        assert hasattr(mod, "router"), f"{package} doesn't export router"

    def test_no_duplicate_router_names(self):
        names = []
        for package in FEATURE_PACKAGES:
            mod = importlib.import_module(package)
            r = getattr(mod, "router", None)
            if r and hasattr(r, "name"):
                names.append(r.name)
        assert len(names) == len(set(names)), f"Duplicate router names: {names}"


class TestNoCircularImports:
    CORE_MODULES = [
        "bot.main",
        "services.image",
        "utils.formatting.text",
        "utils.osu.helpers",
        "utils.osu.resolve_user",
        "db.models",
    ]

    @pytest.mark.parametrize("module", CORE_MODULES)
    def test_import_succeeds(self, module):
        mod = importlib.import_module(module)
        assert mod is not None
