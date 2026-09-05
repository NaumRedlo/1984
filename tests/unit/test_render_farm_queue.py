from services.render_farm.queue import (
    LEASE_SECONDS, MAX_AGE_SECONDS, MAX_ATTEMPTS, RenderQueue, State,
)

SETTINGS = {"size": "1280x720", "fps": 60, "mute": False, "skin": None}

def make(q, title="a map", *, now=0.0):
    return q.offer(f"/tmp/{title}.osr", title, SETTINGS, now=now)

def test_the_longest_wait_is_served_first():
    q = RenderQueue()
    first, second = make(q, "first", now=10.0), make(q, "second", now=20.0)
    assert q.claim("mac", now=30.0).id == first.id
    assert q.claim("mac", now=31.0).id == second.id

def test_a_claimed_job_is_not_offered_twice():
    q = RenderQueue()
    make(q, now=0.0)
    assert q.claim("mac", now=1.0) is not None
    assert q.claim("other", now=2.0) is None

def test_nothing_to_do_is_not_an_error():
    assert RenderQueue().claim("mac") is None

def test_settings_are_copied_out_of_the_caller_s_hands():
    q = RenderQueue()
    settings = dict(SETTINGS)
    job = q.offer("/tmp/x.osr", "x", settings)
    settings["fps"] = 15
    assert job.settings["fps"] == 60

def test_a_lease_that_runs_out_puts_the_job_back():
    q = RenderQueue()
    job = make(q, now=0.0)
    q.claim("mac", now=1.0)
    q.sweep(now=1.0 + LEASE_SECONDS + 1)
    assert job.state is State.WAITING and job.worker is None
    assert q.claim("other", now=200.0).id == job.id

def test_a_heartbeat_keeps_the_job():
    q = RenderQueue()
    job = make(q, now=0.0)
    q.claim("mac", now=1.0)
    for tick in range(1, 10):
        moment = tick * (LEASE_SECONDS / 2)
        assert q.heartbeat(job.id, "mac", now=moment)
        q.sweep(now=moment)
    assert job.state is State.CLAIMED

def test_only_the_worker_holding_a_job_may_speak_for_it():
    q = RenderQueue()
    job = make(q, now=0.0)
    q.claim("mac", now=1.0)
    assert not q.heartbeat(job.id, "impostor")
    assert not q.finish(job.id, "impostor", {"path": "/tmp/v.mp4"})
    assert not q.give_back(job.id, "impostor", "nope")

def test_a_job_handed_back_is_offered_again():
    q = RenderQueue()
    job = make(q, now=0.0)
    q.claim("mac", now=1.0)
    assert q.give_back(job.id, "mac", "battery")
    assert job.state is State.WAITING
    assert q.claim("other", now=2.0).id == job.id

def test_a_job_that_keeps_coming_back_stops_being_offered():
    q = RenderQueue()
    job = make(q, now=0.0)
    for attempt in range(MAX_ATTEMPTS):
        taken = q.claim("mac", now=1.0 + attempt)
        assert taken is not None, f"attempt {attempt} should still be offered"
        q.give_back(job.id, "mac", "the map would not download")

    assert q.claim("mac", now=99.0) is None

    assert job.state is State.WAITING and not job.settled.is_set()

def test_a_worker_that_keeps_dying_counts_the_same_as_one_that_refuses():
    q = RenderQueue()
    make(q, now=0.0)
    for attempt in range(MAX_ATTEMPTS):
        assert q.claim("mac", now=1.0 + attempt) is not None
        q.sweep(now=1.0 + attempt + LEASE_SECONDS + 1)
    assert q.claim("mac", now=999.0) is None

def test_finishing_wakes_whoever_is_waiting():
    q = RenderQueue()
    job = make(q, now=0.0)
    q.claim("mac", now=1.0)
    assert q.finish(job.id, "mac", {"path": "/tmp/v.mp4"})
    assert job.settled.is_set() and not job.withdrawn
    assert job.payload["path"] == "/tmp/v.mp4"

def test_withdrawing_wakes_the_waiter_too_but_says_the_opposite():
    q = RenderQueue()
    job = make(q, now=0.0)
    q.withdraw(job.id)
    assert job.settled.is_set() and job.withdrawn

def test_a_withdrawn_job_cannot_be_finished_afterwards():
    q = RenderQueue()
    job = make(q, now=0.0)
    q.claim("mac", now=1.0)
    q.withdraw(job.id)
    assert not q.finish(job.id, "mac", {"path": "/tmp/v.mp4"})

def test_a_job_nobody_ever_took_does_not_sit_here_for_ever():
    q = RenderQueue()
    job = make(q, now=0.0)
    q.sweep(now=MAX_AGE_SECONDS + 1)
    assert q.get(job.id) is None and job.withdrawn

def test_a_claim_is_visible_to_whoever_is_waiting_on_it():
    q = RenderQueue()
    job = make(q, now=0.0)
    assert not job.taken.is_set()
    q.claim("mac", now=1.0)
    assert job.taken.is_set()
