"""Who the farm can see.

The farm could never answer "is anybody rendering for us tonight". A job knows
which worker holds it, but a machine sitting ready left no trace at all, and a
machine that was present and *declining* left less than none — declining meant
not calling the endpoint, so it looked exactly like a laptop that was shut.

Those two states want opposite reactions from whoever is looking, which is the
whole reason this exists.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from services.render_farm.queue import RenderQueue  # noqa: E402
from services.render_farm.roster import GONE_AFTER, Roster  # noqa: E402


def _capacity(take=True, reason="the machine is idle", threads=8, polite=False):
    return {"take": take, "reason": reason, "threads": threads, "polite": polite}


def test_a_machine_that_is_here_and_declining_is_not_a_machine_that_is_gone():
    """The case the whole module exists for. "Three are here and all on
    battery" and "nobody is here" are different problems."""
    roster = Roster()
    roster.hello("laptop", capacity=_capacity(take=False, reason="on battery at 12%"),
                 now=0)

    here = roster.here(now=1)
    assert [w.name for w in here] == ["laptop"]
    assert here[0].state(rendering=False) == "resting"
    assert here[0].reason == "on battery at 12%", "and it says why, not just no"


def test_silence_means_gone():
    roster = Roster()
    roster.hello("laptop", capacity=_capacity(), now=0)
    assert roster.here(now=GONE_AFTER - 1)
    assert not roster.here(now=GONE_AFTER + 1)


def test_rendering_beats_everything_else_it_might_say():
    """A worker that took a job before its battery dropped is still rendering,
    and reporting it as resting would report the wrong machine as free."""
    roster = Roster()
    worker = roster.hello("laptop", capacity=_capacity(take=False), now=0)
    assert worker.state(rendering=True) == "rendering"


def test_a_heartbeat_keeps_a_busy_worker_on_the_list():
    """A rendering worker stops calling `claim` for minutes — it heartbeats on
    the job instead. Without this the farm would lose a machine at exactly the
    moment it started being useful."""
    roster = Roster()
    roster.hello("laptop", build="dossier 0.1.0 (abc1234)", capacity=_capacity(), now=0)
    for beat in range(1, 10):
        roster.hello("laptop", now=beat * (GONE_AFTER / 2))
    assert roster.here(now=9 * (GONE_AFTER / 2))


def test_a_heartbeat_does_not_blank_what_a_claim_said():
    """A heartbeat carries neither build nor capacity. Overwriting with nothing
    would make the farm view flicker between knowing and not."""
    roster = Roster()
    roster.hello("laptop", build="dossier 0.1.0 (abc1234)",
                 capacity=_capacity(threads=12), now=0)
    roster.hello("laptop", now=1)

    worker = roster.here(now=1)[0]
    assert worker.build == "dossier 0.1.0 (abc1234)"
    assert worker.threads == 12


def test_the_counts_are_per_worker():
    roster = Roster()
    roster.hello("a", capacity=_capacity(), now=0)
    roster.hello("b", capacity=_capacity(), now=0)
    roster.delivered("a")
    roster.delivered("a")
    roster.handed_back("b")

    by_name = {w.name: w for w in roster.here(now=1)}
    assert (by_name["a"].delivered, by_name["a"].handed_back) == (2, 0)
    assert (by_name["b"].delivered, by_name["b"].handed_back) == (0, 1)


def test_counting_for_a_worker_nobody_has_heard_of_is_not_an_error():
    """A result can arrive from a machine the roster has already swept — a long
    render on a flaky link. Dropping the count beats inventing a worker."""
    roster = Roster()
    roster.delivered("a stranger")
    assert not roster.here(now=0)


def test_the_queue_says_who_is_rendering_rather_than_the_roster_guessing():
    """Two records of the same fact get out of step, and this one would do it
    exactly when somebody was looking."""
    queue = RenderQueue()
    job = queue.offer("/tmp/replay.osr", "a map", {})
    assert queue.rendering() == set()

    queue.claim("laptop")
    assert queue.rendering() == {"laptop"}

    queue.give_back(job.id, "laptop", "did not work")
    assert queue.rendering() == set()


def test_longest_serving_first():
    """So the order of a farm listing does not shuffle between two readings of
    it a second apart."""
    roster = Roster()
    roster.hello("second", capacity=_capacity(), now=5)
    roster.hello("first", capacity=_capacity(), now=1)
    roster.hello("third", capacity=_capacity(), now=9)
    assert [w.name for w in roster.here(now=10)] == ["first", "second", "third"]
