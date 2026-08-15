"""Renders waiting for a machine, and what happens when one goes quiet.

The queue's whole job is that a render always ends up rendered — on the worker
if it can, on the bot's own host otherwise. Every test here is a way that could
fail to happen: a worker that claims and dies, a claim that arrives after the
bot gave up, two workers reaching for the same job.
"""

from services.render_farm.queue import (
    LEASE_SECONDS, MAX_AGE_SECONDS, RenderQueue, State,
)

SETTINGS = {"size": "1280x720", "fps": 60, "mute": False, "skin": None}


def make(q, title="a map", *, now=0.0):
    return q.offer(f"/tmp/{title}.osr", title, SETTINGS, now=now)


# ── handing work out ──────────────────────────────────────────────────────

def test_the_longest_wait_is_served_first():
    """Whoever has been staring at a progress bar longest goes next."""
    q = RenderQueue()
    first, second = make(q, "first", now=10.0), make(q, "second", now=20.0)
    assert q.claim("mac", now=30.0).id == first.id
    assert q.claim("mac", now=31.0).id == second.id


def test_a_claimed_job_is_not_offered_twice():
    """Two workers rendering the same replay is two machines' minutes for one
    video, and a race over which upload wins."""
    q = RenderQueue()
    make(q, now=0.0)
    assert q.claim("mac", now=1.0) is not None
    assert q.claim("other", now=2.0) is None


def test_nothing_to_do_is_not_an_error():
    assert RenderQueue().claim("mac") is None


def test_settings_are_copied_out_of_the_caller_s_hands():
    """The job outlives the call that made it, and a dict mutated afterwards
    would change a render already in flight."""
    q = RenderQueue()
    settings = dict(SETTINGS)
    job = q.offer("/tmp/x.osr", "x", settings)
    settings["fps"] = 15
    assert job.settings["fps"] == 60


# ── a worker that goes quiet ──────────────────────────────────────────────

def test_a_lease_that_runs_out_puts_the_job_back():
    """The laptop shut its lid mid-render. Nobody is coming back with that
    video, and the job has to become somebody else's."""
    q = RenderQueue()
    job = make(q, now=0.0)
    q.claim("mac", now=1.0)
    q.sweep(now=1.0 + LEASE_SECONDS + 1)
    assert job.state is State.WAITING and job.worker is None
    assert q.claim("other", now=200.0).id == job.id


def test_a_heartbeat_keeps_the_job():
    """A render says nothing for long stretches while it encodes; going quiet
    is not the same as dying, which is what the heartbeat exists to say."""
    q = RenderQueue()
    job = make(q, now=0.0)
    q.claim("mac", now=1.0)
    for tick in range(1, 10):
        moment = tick * (LEASE_SECONDS / 2)
        assert q.heartbeat(job.id, "mac", now=moment)
        q.sweep(now=moment)
    assert job.state is State.CLAIMED


def test_only_the_worker_holding_a_job_may_speak_for_it():
    """Otherwise a worker whose lease expired mid-render carries on and
    delivers a video for a job the bot has already rendered itself."""
    q = RenderQueue()
    job = make(q, now=0.0)
    q.claim("mac", now=1.0)
    assert not q.heartbeat(job.id, "impostor")
    assert not q.finish(job.id, "impostor", {"path": "/tmp/v.mp4"})
    assert not q.give_back(job.id, "impostor", "nope")


def test_a_job_handed_back_is_offered_again():
    """The map would not download, the battery hit the floor. Another worker
    may manage, and if none does the bot's own timeout catches it."""
    q = RenderQueue()
    job = make(q, now=0.0)
    q.claim("mac", now=1.0)
    assert q.give_back(job.id, "mac", "battery")
    assert job.state is State.WAITING
    assert q.claim("other", now=2.0).id == job.id


# ── settling ──────────────────────────────────────────────────────────────

def test_finishing_wakes_whoever_is_waiting():
    q = RenderQueue()
    job = make(q, now=0.0)
    q.claim("mac", now=1.0)
    assert q.finish(job.id, "mac", {"path": "/tmp/v.mp4"})
    assert job.settled.is_set() and not job.withdrawn
    assert job.payload["path"] == "/tmp/v.mp4"


def test_withdrawing_wakes_the_waiter_too_but_says_the_opposite():
    """Both mean "stop waiting"; one means "here is your video" and the other
    means "I am doing it myself". A waiter that could not tell them apart would
    send nothing at all."""
    q = RenderQueue()
    job = make(q, now=0.0)
    q.withdraw(job.id)
    assert job.settled.is_set() and job.withdrawn


def test_a_withdrawn_job_cannot_be_finished_afterwards():
    """The bot gave up and started rendering. A worker arriving late must not
    resolve a job whose video is already being made here."""
    q = RenderQueue()
    job = make(q, now=0.0)
    q.claim("mac", now=1.0)
    q.withdraw(job.id)
    assert not q.finish(job.id, "mac", {"path": "/tmp/v.mp4"})


def test_a_job_nobody_ever_took_does_not_sit_here_for_ever():
    """It pins a replay on disk, and the handler that offered it is long gone."""
    q = RenderQueue()
    job = make(q, now=0.0)
    q.sweep(now=MAX_AGE_SECONDS + 1)
    assert q.get(job.id) is None and job.withdrawn


def test_a_claim_is_visible_to_whoever_is_waiting_on_it():
    """The bot waits a few seconds for anyone to take the job and much longer
    once somebody has — it needs to know which of the two it is in."""
    q = RenderQueue()
    job = make(q, now=0.0)
    assert not job.taken.is_set()
    q.claim("mac", now=1.0)
    assert job.taken.is_set()
