import subprocess
import sys

from utils import aiogram_lazy

CHILD = """
import sys
sys.path.insert(0, {root!r})
import os
os.environ["AIOGRAM_EAGER_MODELS"] = {eager!r}
from utils import aiogram_lazy, memory_report
from utils.aiogram_lazy import install
install()
import aiogram.types as types
import aiogram.methods as methods
built = sum(1 for module in (types, methods) for name in module.__all__
            if type(getattr(getattr(module, name), "__pydantic_validator__", None)).__name__ == "SchemaValidator")
fine = aiogram_lazy.verify()
size = int(memory_report.resident())
everything = aiogram_lazy.build_everything()
aiogram_lazy._exercise()
print(built, size, fine, aiogram_lazy.installed(), everything)
"""


def _child(eager: str) -> tuple[int, int, str, str, int]:
    import os

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    told = subprocess.run([sys.executable, "-c", CHILD.format(root=root, eager=eager)], capture_output=True, text=True,
                          check=True).stdout.split()
    return int(told[0]), int(told[1]), told[2], told[3], int(told[4])


def test_this_suite_runs_with_telegram_types_built_on_first_use():
    assert aiogram_lazy.installed()


def test_an_update_a_callback_a_message_and_an_answer_work_when_built_on_first_use():
    aiogram_lazy._exercise()
    assert aiogram_lazy.verify()


def test_importing_builds_far_fewer_types_and_takes_far_less_memory():
    lazy_built, lazy_size, lazy_fine, lazy_on, everything = _child("")
    eager_built, eager_size, eager_fine, eager_on, nothing = _child("1")
    assert (lazy_on, eager_on) == ("True", "False")
    assert lazy_fine == "True" and eager_fine == "True"
    assert eager_built > 500 and lazy_built < eager_built / 2
    assert eager_size - lazy_size > 50, (lazy_size, eager_size)
    assert everything > 300 and nothing == 0, "all types can still be built at once when asked"
