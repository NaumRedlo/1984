"""What this machine is willing to render right now.

Runs on the render host, not on the bot's. The point of moving renders onto a
laptop is that the laptop is six times the server; the price is that it is also
somebody's laptop, and a render that makes it unusable — or that empties its
battery in a bag — costs more than it saves.

So a job is not taken unconditionally. The machine is asked four questions, all
of them answerable from `pmset` and `ioreg` without any daemon of our own:

- **Is it on battery, and how much is left?** Below the floor the job goes back
  and the server renders it. Above it, the render takes half the machine.
- **Has the operator asked for low power?** If macOS is in that mode they have
  said what they want; overriding it with our own arithmetic would be rude.
- **Is anyone at the keyboard?** Idle means the machine is ours; a hand on the
  trackpad means we get out of the way.
- **Is it already hot?** Thermal pressure drops us a tier rather than adding to
  it.

The numbers below were measured on the encoder half of a render, on an M4 Pro
(8 performance cores, 4 efficiency), at 720p60 veryfast/CRF 20:

    threads   wall     load    CPU-seconds
    2         1.90s    284%    5.31
    4         1.78s    324%    5.58
    6         1.07s    566%    5.57
    12        0.89s    779%    6.12
    taskpolicy -b   9.94s    208%    19.53

Two things in that table decided the policy. Total CPU work barely moves with
the thread cap, so capping threads does *not* meaningfully save charge — it
buys heat, fan noise and a responsive machine, which is worth buying for its
own sake but is not a battery measure. And the option that looks gentlest,
demoting the process to the background tier, burned three times the CPU work
for the same video: it is the worst thing available on battery, not the best.

The drawing half of a render scales differently and has not been measured — it
needs a real replay to render. When it is, these splits are what to revisit.
"""

import contextlib
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Iterator, Optional

from utils.logger import get_logger

logger = get_logger("services.dossier.machine")

# Below this, a job is handed back rather than started. A render is minutes, so
# one begun just above the line finishes some way under it — which is fine, and
# is why there is a second, lower line as well.
BATTERY_FLOOR = 15
# And this one is checked *during* a render: crossing it means stopping and
# handing the job back. An abandoned job is worse than a slow one, so this is a
# return, never a discard.
BATTERY_ABORT = 10

# No input for this long and the machine is ours to use.
IDLE_SECONDS = 300

# macOS energy mode: 0 automatic, 1 low power, 2 high power.
LOW_POWER = 1


@dataclass(frozen=True)
class Capacity:
    """Whether to take a job, and how hard to work on it if so."""

    take: bool
    reason: str
    threads: int = 0
    encoder_threads: int = 0
    # Only ever set when somebody is at the keyboard. On an idle machine it
    # costs nothing (measured: 0.82s against 0.86s), and under contention it is
    # the thing that decides who yields — so it is set exactly when there is
    # contention to lose.
    polite: bool = False


def _run(args: tuple[str, ...]) -> str:
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("could not ask the machine (%s): %s", args[0], exc)
        return ""
    return done.stdout


def parse_battery(text: str) -> tuple[bool, int]:
    """(on battery, percent) from `pmset -g batt`.

    A desktop reports no battery at all, and a machine whose charge cannot be
    read is treated as plugged in: refusing every job because a string did not
    match would be a worse failure than taking one.
    """
    on_battery = "'Battery Power'" in text
    found = re.search(r"(\d+)%", text)
    return on_battery, int(found.group(1)) if found else 100


def parse_power_mode(text: str) -> int:
    """The active `powermode` from `pmset -g`."""
    found = re.search(r"^\s*powermode\s+(\d+)", text, re.MULTILINE)
    return int(found.group(1)) if found else 0


def parse_idle_seconds(text: str) -> float:
    """Seconds since the last keypress or gesture, from `ioreg -c IOHIDSystem`.

    Reported in nanoseconds. An unreadable answer means "somebody is here",
    which is the cautious way round: it costs speed, not somebody's machine.
    """
    found = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', text)
    return int(found.group(1)) / 1e9 if found else 0.0


def parse_thermal_pressure(text: str) -> bool:
    """Whether `pmset -g therm` is reporting anything at all.

    It says "No thermal warning level has been recorded" on a cool machine, so
    a recorded level of anything other than zero is the signal.
    """
    found = re.search(r"CPU_Speed_Limit\s*=\s*(\d+)", text)
    if found:
        return int(found.group(1)) < 100
    return bool(re.search(r"warning level\s*=?\s*[1-9]", text))


def decide(*, on_battery: bool, percent: int, power_mode: int,
           idle_seconds: float, hot: bool, cores: int) -> Capacity:
    """The policy itself, given what the machine said. Pure, so it is tested.

    Order matters: the reasons to refuse are asked before the questions about
    how hard to work, because a refusal makes the rest moot.
    """
    if power_mode == LOW_POWER:
        return Capacity(False, "the machine is in low power mode")
    if on_battery and percent < BATTERY_FLOOR:
        return Capacity(False, f"on battery at {percent}%")

    busy = idle_seconds < IDLE_SECONDS
    if on_battery:
        # Half the machine: measured at 566% of a 1200% ceiling, for 1.2x the
        # time of an unrestricted render. The restraint is for heat in a closed
        # bag, not for charge — the table in this module's docstring says the
        # charge goes either way.
        threads, encoder = max(1, cores // 2), max(1, cores // 4)
        reason = f"on battery at {percent}%"
    elif busy:
        # Four threads costs 2.1x the time and leaves eight cores to whoever is
        # using them, which is the trade the whole policy exists to make.
        threads, encoder = 4, 2
        reason = "somebody is at the keyboard"
    else:
        # Drawing on the performance cores, encoding on what is left. Not
        # `cores - 1` for drawing: the encoder needs its own, and two pools
        # sized as though each had the machine to itself is how both end up
        # waiting on the same cores.
        threads, encoder = max(1, cores * 2 // 3), max(1, cores // 3)
        reason = "the machine is idle"

    if hot:
        # A tier down rather than a refusal: the job is already worth doing,
        # and adding to the pressure is the only part worth avoiding. Checked
        # after every branch, including the one where somebody is present —
        # a hot machine under someone's hands is the worst of both.
        threads, encoder = max(1, threads // 2), max(1, encoder // 2)
        reason += ", and it is hot"
    return Capacity(True, reason, threads, encoder, polite=busy)


def parse_linux_battery(capacity_text: str, status_text: str) -> tuple[bool, int]:
    """(on battery, percent) from `/sys/class/power_supply/BAT*`.

    Linux publishes this as two one-line files rather than as a command's prose,
    which makes it the easiest of the three platforms to read and the easiest to
    get subtly wrong: `status` is `Discharging`, `Charging`, `Full`, `Idle` or
    `Unknown`, and only the first of those means the wall is not helping.
    """
    try:
        percent = int(capacity_text.strip())
    except ValueError:
        percent = 100
    return status_text.strip().lower() == "discharging", max(0, min(100, percent))


def _linux_battery() -> tuple[bool, int]:
    """The first battery the machine admits to, or mains power if it has none.

    A desktop and a server both have no `BAT0`, and both should render at full
    tilt — which is what "not on battery, a hundred per cent" says.
    """
    supply = "/sys/class/power_supply"
    try:
        names = sorted(n for n in os.listdir(supply) if n.upper().startswith("BAT"))
    except OSError:
        return False, 100
    for name in names:
        try:
            with open(os.path.join(supply, name, "capacity"), encoding="ascii") as f:
                capacity_text = f.read()
            with open(os.path.join(supply, name, "status"), encoding="ascii") as f:
                status_text = f.read()
        except OSError:
            continue
        return parse_linux_battery(capacity_text, status_text)
    return False, 100


def _windows_battery() -> tuple[bool, int]:
    """(on battery, percent) from `GetSystemPowerStatus`.

    Through `ctypes` rather than a package: this file has no dependencies on
    either of the other two platforms and there is no reason for Windows to be
    the one that needs one.

    `ACLineStatus` is 0 off the wall, 1 on it and 255 unknown — unknown is read
    as mains, because refusing to render on a machine that will not say is worse
    than rendering on a laptop that is plugged in. `BatteryLifePercent` is 255
    when there is nothing to report.
    """
    import ctypes

    class Status(ctypes.Structure):
        _fields_ = [
            ("ACLineStatus", ctypes.c_ubyte),
            ("BatteryFlag", ctypes.c_ubyte),
            ("BatteryLifePercent", ctypes.c_ubyte),
            ("SystemStatusFlag", ctypes.c_ubyte),
            ("BatteryLifeTime", ctypes.c_ulong),
            ("BatteryFullLifeTime", ctypes.c_ulong),
        ]

    status = Status()
    try:
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return False, 100
    except (AttributeError, OSError) as exc:
        logger.warning("could not read the power status: %s", exc)
        return False, 100
    percent = status.BatteryLifePercent
    return status.ACLineStatus == 0, 100 if percent == 255 else int(percent)


def _windows_idle_seconds() -> float:
    """Seconds since the last keypress or mouse move, from `GetLastInputInfo`.

    The same question `ioreg` answers on macOS and the one thing that decides
    whether a render is allowed to be greedy: a machine somebody is using is a
    machine a render has to keep out of the way of.
    """
    import ctypes

    class LastInput(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_ulong)]

    info = LastInput()
    info.cbSize = ctypes.sizeof(LastInput)
    try:
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return IDLE_SECONDS
        ticks = ctypes.windll.kernel32.GetTickCount64()
    except (AttributeError, OSError):
        return IDLE_SECONDS
    return max(0.0, (ticks - info.dwTime) / 1000.0)


@dataclass(frozen=True)
class Limits:
    """What the owner of this machine has asked for, as opposed to measured.

    The readings above answer "what can this machine give". This answers "what
    is it being lent", which nothing can be asked and only a person can say.

    Kept apart from `Capacity` because they change on different clocks: a
    battery moves by itself and these move when somebody edits a file. The
    worker re-reads them every poll, so a person can pause a render farm from
    a text editor without stopping anything.
    """

    polite: bool = False
    # A hard ceiling on threads. Nought means the policy decides alone.
    threads: int = 0
    # The hours of the day this machine may take work in, `(from, until)` on a
    # 24-hour clock, `until` exclusive. `None` means any hour.
    hours: Optional[tuple[int, int]] = None
    paused: bool = False

    def closed(self, hour: int) -> Optional[str]:
        """Why no work at all right now, or `None` if work is fine.

        Separate from the thread counts because it is a different kind of
        answer: a ceiling shapes a render and this one prevents it. The reason
        travels because it ends up in the farm view, where "paused by its
        owner" and "on battery at 9%" call for different reactions.
        """
        if self.paused:
            return "paused by its owner"
        if self.hours is not None and not within(self.hours, hour):
            start, end = self.hours
            return f"outside its hours ({start:02d}:00–{end:02d}:00)"
        return None


def within(hours: tuple[int, int], hour: int) -> bool:
    """Whether `hour` falls in the window, which may wrap round midnight.

    `22-6` is the useful case and the one a naive comparison gets wrong: it is
    the small hours somebody is asleep through, and it is the whole reason
    anybody would set this.
    """
    start, end = hours
    if start == end:
        # A zero-width window would mean never, which nobody types on purpose.
        # Read as all day, on the grounds that `0-0` looks like "no limit".
        return True
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def parse_hours(text: str) -> Optional[tuple[int, int]]:
    """`0-9`, `22-6`, or `None` for anything this does not understand.

    Unreadable is treated as unset rather than as an error: this is read on
    every poll from a file somebody edits by hand, and a typo should cost the
    limit rather than the worker.
    """
    match = re.match(r"^\s*(\d{1,2})\s*[-–—]\s*(\d{1,2})\s*$", text or "")
    if not match:
        return None
    start, end = int(match.group(1)), int(match.group(2))
    if not (0 <= start <= 24 and 0 <= end <= 24):
        return None
    return start % 24, end % 24


def capacity(cores: int, *, polite: bool = False, ceiling: int = 0) -> Capacity:
    """Ask the machine, then decide.

    Three platforms, one decision. [`decide`] is where the policy lives and it
    takes plain numbers, so what differs per platform is only how those numbers
    are obtained — `pmset` and `ioreg` on macOS, two files under `/sys` on
    Linux, two `ctypes` calls on Windows.

    Two things the machine cannot be asked, so their owner says them instead.
    `polite` is somebody stating that they are using this machine — which is
    the only way to say it on Linux, where there is no reading of "is anyone at
    the keyboard" that holds on a tty, on X and on Wayland alike. This module
    has named that flag in a comment for some time without anything honouring
    it; now it does.

    `ceiling` is a hard cap on threads, for a machine being lent rather than
    given. It is applied last and to both pools, because a cap the policy could
    argue its way past is not a cap.
    """
    # Nought seconds since the last input is the plainest way to say "somebody
    # is here", and it means the flag rides the same branch every platform's
    # own reading does rather than being a second kind of busy.
    said_busy = 0.0 if polite else None

    if sys.platform == "darwin":
        on_battery, percent = parse_battery(_run(("pmset", "-g", "batt")))
        return _capped(decide(
            on_battery=on_battery,
            percent=percent,
            power_mode=parse_power_mode(_run(("pmset", "-g"))),
            idle_seconds=said_busy if said_busy is not None
            else parse_idle_seconds(_run(("ioreg", "-c", "IOHIDSystem"))),
            hot=parse_thermal_pressure(_run(("pmset", "-g", "therm"))),
            cores=cores,
        ), ceiling)

    if sys.platform == "win32":
        on_battery, percent = _windows_battery()
        return _capped(decide(
            on_battery=on_battery,
            percent=percent,
            # No equivalent to macOS's low power mode worth reading: Windows
            # states a power scheme by GUID, and mapping those to "the owner
            # asked for less" is guesswork. Read as "not asked for".
            power_mode=0,
            idle_seconds=said_busy if said_busy is not None
            else _windows_idle_seconds(),
            # And no thermal pressure reading that does not need a driver.
            hot=False,
            cores=cores,
        ), ceiling)

    on_battery, percent = _linux_battery()
    return _capped(decide(
        on_battery=on_battery,
        percent=percent,
        power_mode=0,
        # Linux has no way to ask "is somebody at the keyboard" that works on a
        # tty, on X and on Wayland alike. Read as nobody: the common Linux host
        # for this is a server or a spare box, and one that somebody *is* using
        # says so with `--polite`.
        idle_seconds=said_busy if said_busy is not None else IDLE_SECONDS,
        hot=False,
        cores=cores,
    ), ceiling)


def _capped(got: Capacity, ceiling: int) -> Capacity:
    """The owner's own limit on both thread pools.

    A refusal is left alone: it carries no counts to cap, and giving it some
    would turn "not now" into a job taken.
    """
    if ceiling <= 0 or not got.take:
        return got
    return Capacity(
        got.take,
        f"{got.reason}, capped at {ceiling}",
        min(got.threads, ceiling),
        min(got.encoder_threads, ceiling),
        polite=got.polite,
    )


def should_abort(percent: int, on_battery: bool) -> bool:
    """Whether a render already running should stop and hand its job back."""
    return on_battery and percent < BATTERY_ABORT


def wakeful() -> tuple[str, ...]:
    """A command prefix that keeps the machine awake while a render runs.

    Reported from a real evening: a replay sent from out of the house, rendered
    at home, and the file never arrived. The Mac had gone to sleep partway
    through. A sleeping process is frozen, not killed — so the heartbeats stop,
    the bot's lease runs out and it renders the job itself, and the laptop wakes
    up minutes later, finishes a render nobody is waiting for, and tries to
    upload it into a job that is no longer its own.

    `caffeinate` is macOS's own answer and it is exactly scoped: it holds the
    assertion for as long as the command it wraps is running and drops it the
    moment the render ends, so a worker cannot leave a machine unable to sleep.
    `-i` blocks idle sleep, `-m` keeps the disk spinning for the write, and `-s`
    blocks system sleep — that last one only has an effect on mains power, which
    is the case this is for.

    What it cannot do is override a closed lid. Nothing can, so the worker has
    to survive it happening anyway — see how a lost lease stops a render in
    `scripts/render_worker.py`.

    Linux has `systemd-inhibit`, which is the same idea and the same scoping:
    it holds a lock for as long as the command it wraps runs. `sleep:idle`
    rather than `handle-lid-switch`, because a lid is the owner saying what
    they want and a render is not entitled to argue — the same line macOS's
    `caffeinate` draws, arrived at from the other direction.

    Empty on Windows and on a Linux without systemd. Windows has no wrapper
    command for this at all and is held awake from inside the process instead —
    see [`awake`], which is what a caller should use.
    """
    if sys.platform == "darwin":
        caffeinate = "/usr/bin/caffeinate"
        return (caffeinate, "-i", "-m", "-s") if os.access(caffeinate, os.X_OK) else ()

    if sys.platform.startswith("linux"):
        inhibit = shutil.which("systemd-inhibit")
        if inhibit:
            return (
                inhibit,
                "--what=sleep:idle",
                "--who=dossier",
                "--why=rendering a replay",
                # Block, not delay: a delay lock buys seconds and a render is
                # minutes, so a delayed suspend is a suspend.
                "--mode=block",
                "--",
            )
    return ()


# Windows' own names for "keep the system up". `ES_CONTINUOUS` makes the state
# stick until it is cleared rather than counting as one nudge, and
# `ES_AWAYMODE_REQUIRED` is what keeps a desktop working with the screen off
# rather than merely postponing the idle timer.
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_ES_AWAYMODE_REQUIRED = 0x00000040


@contextlib.contextmanager
def awake() -> Iterator[tuple[str, ...]]:
    """Hold the machine awake for this block, and yield the command prefix.

    One call for the two shapes the answer comes in. macOS and Linux both have
    a wrapper command, which is the better mechanism because the assertion dies
    with the process that holds it — a worker killed mid-render cannot leave a
    machine unable to sleep. Windows has no such command, only a flag set from
    inside a process, so that one is set here and cleared on the way out.

    The flag is per-thread and lives as long as the thread does, so clearing it
    in a `finally` is the whole of the contract. A worker that is killed
    outright loses the flag with the process anyway, which is the same
    end the wrapper commands reach by another road.
    """
    if sys.platform != "win32":
        yield wakeful()
        return

    import ctypes

    def state(flags: int) -> bool:
        try:
            return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))
        except (AttributeError, OSError) as exc:
            logger.warning("could not ask Windows to stay awake: %s", exc)
            return False

    # Away mode needs the machine to allow it and is refused rather than
    # ignored where it does not, so a refusal falls back to plain wakefulness
    # instead of leaving the render unprotected.
    held = state(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_AWAYMODE_REQUIRED)
    if not held:
        held = state(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED)
    try:
        yield ()
    finally:
        if held:
            state(_ES_CONTINUOUS)
