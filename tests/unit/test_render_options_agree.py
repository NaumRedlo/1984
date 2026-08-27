"""Every layer a render setting passes through accepts it.

A setting is written out by hand in eight places: the two dispatcher
signatures, what each forwards to `_remote_or_local`, the two local fall-back
lambdas, `_remote_or_local` itself, the job a worker reads, and the runner.

The hit-error meter reached seven of them. The eighth answered
`video() got an unexpected keyword argument 'meter'` — in production, on
somebody's render, with a traceback for an answer. Nothing smaller than this
catches that: each layer is correct in isolation and the fault lives between
them.

The settings are read off `Choices` rather than listed here, so a field added
to the menu is a field this test starts asking about on its own.
"""

import inspect

import pytest

from bot.handlers.dossier import renders
from dossier import runner
from services.render_farm import dispatch

# Settings that reach the engine under another name or in another shape, and so
# cannot be looked for by the name they have on `Choices`.
#
#   skin        a folder path here, a name the worker fetches there
#   leaderboard a table built from the chat, not a flag
#   size        goes through the ration check first
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
    """The job is a dict written by hand beside the signature, so a key left
    out of it is a setting that silently stops applying.

    It used to be checkable against the local render beside it — the same
    arguments passed twice, once as a call and once as a dict. That second copy
    is gone with the fallback, and with it the thing that would have noticed.
    Which makes this the only guard left on the settings reaching a machine at
    all, rather than one of two."""
    job = inspect.getsource(dispatch._on_the_farm)
    for name in _settings():
        assert f'"{name}"' in job, f"the job never mentions {name}"


def test_the_worker_reads_back_every_setting_the_job_carries():
    """The other half of the same seam: the job is written in one file and
    unpacked in another, and neither file mentions the other.

    The other file is in another repository now, which makes this more worth
    having rather than less — the two can be changed weeks apart by somebody
    who has only one of them open."""
    from dossier import worker as client

    worker = inspect.getsource(client)
    for name in _settings():
        assert f'"{name}"' in worker, f"the worker never reads {name} out of the job"
