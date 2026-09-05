import inspect

import pytest

from bot.handlers.dossier import renders
from dossier import runner
from services.render_farm import dispatch

RESHAPED = {"skin", "leaderboard", "size"}

def _settings() -> set[str]:
    return set(renders.Choices.__dataclass_fields__) - RESHAPED

def _takes(fn) -> set[str]:
    return set(inspect.signature(fn).parameters)

@pytest.mark.parametrize(
    "layer, engine",
    [(dispatch.video, runner.video), (dispatch.exhibit, runner.exhibit)],
    ids=["video", "exhibit"],
)
def test_the_dispatcher_and_the_runner_take_the_same_settings(layer, engine):
    for name in _settings():
        assert name in _takes(engine), f"the runner cannot take {name}"
        assert name in _takes(layer), f"{layer.__name__} cannot pass on {name}"

def test_a_worker_is_told_every_setting_somebody_chose():
    job = inspect.getsource(dispatch._on_the_farm)
    for name in _settings():
        assert f'"{name}"' in job, f"the job never mentions {name}"

def test_the_worker_reads_back_every_setting_the_job_carries():
    from dossier import worker as client

    worker = inspect.getsource(client)
    for name in _settings():
        assert f'"{name}"' in worker, f"the worker never reads {name} out of the job"
