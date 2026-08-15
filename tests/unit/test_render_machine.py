"""What the render host agrees to do, and when it refuses.

The policy decides how much of somebody's laptop a render is allowed to take.
Getting it wrong is not a rendering bug — it is a flat battery in a bag, or a
machine that stutters under its owner's hands — so it is tested against the
literal output of the commands it reads rather than against a mock of them.
"""

from services.dossier.machine import (
    BATTERY_ABORT, BATTERY_FLOOR, Capacity, decide, parse_battery,
    parse_idle_seconds, parse_power_mode, parse_thermal_pressure, should_abort,
)

CORES = 12  # an M4 Pro: 8 performance, 4 efficiency

ON_BATTERY = (
    "Now drawing from 'Battery Power'\n"
    " -InternalBattery-0 (id=21430371)\t69%; discharging; 9:33 remaining present: true\n"
)
ON_AC = (
    "Now drawing from 'AC Power'\n"
    " -InternalBattery-0 (id=21430371)\t67%; charging; 1:00 remaining present: true\n"
)
DESKTOP = "Now drawing from 'AC Power'\n"
COOL = (
    "Note: No thermal warning level has been recorded\n"
    "Note: No performance warning level has been recorded\n"
)


def take(**over):
    """The policy on an idle, plugged-in machine unless told otherwise."""
    args = dict(on_battery=False, percent=100, power_mode=0,
                idle_seconds=9999, hot=False, cores=CORES)
    args.update(over)
    return decide(**args)


# ── reading the machine ───────────────────────────────────────────────────

def test_the_real_output_of_pmset_is_understood():
    """Pinned to literal captures: these strings are the whole interface, and a
    regex that stops matching them fails open in the direction of taking jobs
    it should have refused."""
    assert parse_battery(ON_BATTERY) == (True, 69)
    assert parse_battery(ON_AC) == (False, 67)


def test_a_machine_with_no_battery_is_not_a_machine_to_spare():
    """A desktop reports no percentage. Reading that as 0% would refuse every
    job on the one host that has no reason to refuse any."""
    on_battery, percent = parse_battery(DESKTOP)
    assert on_battery is False and percent == 100
    assert take(on_battery=on_battery, percent=percent).take


def test_idle_time_arrives_in_nanoseconds():
    assert parse_idle_seconds('"HIDIdleTime" = 5600000000') == 5.6


def test_an_unreadable_idle_time_means_somebody_is_here():
    """The cautious way round. Guessing "idle" wrong takes over a machine
    somebody is using; guessing "busy" wrong only costs the render some speed."""
    assert parse_idle_seconds("nothing of the sort") == 0.0
    assert take(idle_seconds=parse_idle_seconds("")).polite


def test_a_cool_machine_reports_no_pressure():
    assert parse_thermal_pressure(COOL) is False
    assert parse_thermal_pressure("CPU_Speed_Limit = 70") is True
    assert parse_thermal_pressure("CPU_Speed_Limit = 100") is False


def test_the_energy_mode_is_read_from_the_active_profile():
    assert parse_power_mode(" powermode            0\n sleep    1\n") == 0
    assert parse_power_mode(" powermode            1\n") == 1


# ── when to refuse ────────────────────────────────────────────────────────

def test_the_battery_floor_is_a_floor_not_a_target():
    """At the line we stop. The check happens once, when the job is taken, so
    the line has to be where a render started just above it can still finish."""
    assert not take(on_battery=True, percent=BATTERY_FLOOR - 1).take
    assert take(on_battery=True, percent=BATTERY_FLOOR).take
    assert take(on_battery=True, percent=BATTERY_FLOOR + 1).take


def test_a_full_battery_is_no_help_if_the_operator_asked_for_low_power():
    """They have already said what they want the machine to do. Deciding we
    know better because there is charge available is exactly the behaviour
    that gets a background worker uninstalled."""
    assert not take(power_mode=1, percent=100).take
    assert not take(power_mode=1, on_battery=False).take


def test_a_refusal_says_why():
    """It travels back to the bot and ends up in a log somebody reads at the
    point they are wondering why nothing rendered."""
    assert "low power" in take(power_mode=1).reason
    assert "14%" in take(on_battery=True, percent=14).reason


def test_a_render_already_running_gives_the_job_back_before_the_battery_dies():
    assert should_abort(BATTERY_ABORT - 1, on_battery=True)
    assert not should_abort(BATTERY_ABORT, on_battery=True)
    # Plugged in, the question does not arise.
    assert not should_abort(2, on_battery=False)


# ── how hard to work ──────────────────────────────────────────────────────

def test_an_idle_plugged_in_machine_is_ours():
    got = take()
    assert got.threads == 8 and got.encoder_threads == 4
    assert got.threads + got.encoder_threads == CORES, "the two pools share one machine"
    assert not got.polite


def test_a_hand_on_the_trackpad_gets_out_of_the_way():
    """Four threads measured 2.1x the time of an unrestricted render and left
    eight cores free — the trade this whole policy exists to make."""
    got = take(idle_seconds=3)
    assert got.threads == 4 and got.encoder_threads == 2
    assert got.polite, "nice costs nothing idle and decides who yields under load"


def test_on_battery_it_takes_half_the_machine():
    got = take(on_battery=True, percent=69)
    assert got.threads == 6 and got.encoder_threads == 3


def test_heat_drops_a_tier_rather_than_refusing():
    """The job is already worth doing; adding to the pressure is the part worth
    avoiding."""
    cool, hot = take(), take(hot=True)
    assert hot.take
    assert hot.threads < cool.threads and hot.encoder_threads < cool.encoder_threads
    assert "hot" in hot.reason


def test_heat_counts_even_with_somebody_at_the_keyboard():
    """The branch that returned early used to skip this check — a hot machine
    under its owner's hands is the worst of the two cases, not the exempt one."""
    assert take(idle_seconds=3, hot=True).threads < take(idle_seconds=3).threads


def test_no_tier_ever_asks_for_no_threads_at_all():
    """Every step halves something. On a small host the halving must not reach
    zero, which the engine would read as "decide for me" — the opposite of the
    restraint being asked for."""
    for cores in (1, 2, 3, 4):
        for over in ({}, {"hot": True}, {"on_battery": True, "percent": 50},
                     {"on_battery": True, "percent": 50, "hot": True}):
            got = take(cores=cores, **over)
            assert got.threads >= 1 and got.encoder_threads >= 1, (cores, over)


def test_a_refusal_carries_no_thread_counts_to_act_on():
    assert take(power_mode=1) == Capacity(False, "the machine is in low power mode")
