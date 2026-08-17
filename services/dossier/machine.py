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

import os
import re
import subprocess
import sys
from dataclasses import dataclass

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


def capacity(cores: int) -> Capacity:
    """Ask the machine, then decide.

    Three platforms, one decision. [`decide`] is where the policy lives and it
    takes plain numbers, so what differs per platform is only how those numbers
    are obtained — `pmset` and `ioreg` on macOS, two files under `/sys` on
    Linux, two `ctypes` calls on Windows.
    """
    if sys.platform == "darwin":
        on_battery, percent = parse_battery(_run(("pmset", "-g", "batt")))
        return decide(
            on_battery=on_battery,
            percent=percent,
            power_mode=parse_power_mode(_run(("pmset", "-g"))),
            idle_seconds=parse_idle_seconds(_run(("ioreg", "-c", "IOHIDSystem"))),
            hot=parse_thermal_pressure(_run(("pmset", "-g", "therm"))),
            cores=cores,
        )

    if sys.platform == "win32":
        on_battery, percent = _windows_battery()
        return decide(
            on_battery=on_battery,
            percent=percent,
            # No equivalent to macOS's low power mode worth reading: Windows
            # states a power scheme by GUID, and mapping those to "the owner
            # asked for less" is guesswork. Read as "not asked for".
            power_mode=0,
            idle_seconds=_windows_idle_seconds(),
            # And no thermal pressure reading that does not need a driver.
            hot=False,
            cores=cores,
        )

    on_battery, percent = _linux_battery()
    return decide(
        on_battery=on_battery,
        percent=percent,
        power_mode=0,
        # Linux has no way to ask "is somebody at the keyboard" that works on a
        # tty, on X and on Wayland alike. Read as nobody: the common Linux host
        # for this is a server or a spare box, and one that somebody *is* using
        # can be told so with `--polite`.
        idle_seconds=IDLE_SECONDS,
        hot=False,
        cores=cores,
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

    Empty everywhere else: Linux has no single equivalent, and a server has no
    business being asleep.
    """
    if sys.platform != "darwin":
        return ()
    caffeinate = "/usr/bin/caffeinate"
    if not os.access(caffeinate, os.X_OK):
        return ()
    return (caffeinate, "-i", "-m", "-s")
