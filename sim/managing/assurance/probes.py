"""Probes: the code that measures the fleet for each indicator of the assurance case.

An indicator is a gauge on a claim, and a probe is what reads it. Each probe is a function of a
FleetSnapshot and the Knowledge base and nothing else -- it cannot reach the simulation, which is what keeps
the managing system independent of it -- so what a probe can measure is exactly what the UAVs and the base
sensor report, and a probe that needs the past reads it from the Knowledge base rather than keeping any of
its own.

A probe returns None, not a number, when its quantity is not defined at this step. The difference matters:
a zero looks like good news and a None is no news, and the belief assessment treats the two differently
(MGT-BAL-10). That is why the fuel probe says nothing when fuel is not being tracked, and the coverage
probe says nothing until a whole lap has been watched.

The name a probe is registered under is the name the bindings file uses for it (assurance/
indicator_bindings.json), and tests/managing/test_assurance_probes.py checks that every probe the file
names exists.

Three things here are not what they might look like.

**The home base is left out of every count about UAVs being close.** Its footprint is shared airspace, so
any number of UAVs may sit on it without a collision, and counting them would put the whole team in the
danger category on step 0 of every run, when they are simply all still on the pad. The simulation's own MR2
counts them, so the pairs this reports are fewer than MR2's; the thresholds in the case were worked out from
MR2 and are therefore on the cautious side.

**The firefighter deficit uses a placeholder for how many firefighters are needed.** Nothing yet says how
many are needed for a fire at a given distance from the base -- the case says as much in the defeater that
only one team size was tried -- so the rule below is a first guess, to be replaced by one the team-size and
fire-distance experiments support.

**Positions are what the UAVs report.** With the positioning error extension on, a reported position is a
measurement and can be wrong in either direction, so the counts of shared cells and close pairs are counts
of measured positions.

See specs/managing/balance.md, MGT-BAL-6.
"""

# python libraries

import itertools
import math
from collections import Counter

# own python modules

# see the note in sim/policy/random_policy.py about importing config as a module
import config

# the policies that put water on a fire, by the names they travel under in a directive. The managing system
# never imports the policies (see tests/managing/test_independence.py); a name is all it has of them.
# follow-fire is left out on purpose: it flies at fire and never drops anything.
FIREFIGHTING_POLICIES = ("firefighter", "scout-firefighter", "defend-base")

# every probe, by the name the bindings file refers to it by. Filled in by the registration below.
PROBES = {}


def probe(name):
    """Registers a function as the probe called `name`."""

    def register(function):
        PROBES[name] = function
        return function

    return register


# --- helpers ----------------------------------------------------------------


# the cells of the home base, or none at all when there is no base
def base_cells(snapshot):
    return set(snapshot.base.cells) if snapshot.base is not None else set()


# the UAVs still flying that are off the home base and have a position to count: the ones a collision or a
# close approach can actually be about
def flying_off_base(snapshot):
    pad = base_cells(snapshot)
    return [report for report in snapshot.alive() if report.pos is not None and report.pos not in pad]


# --- the probes -------------------------------------------------------------


@probe("shared_cells")
def shared_cells(snapshot, knowledge=None):
    """How many UAVs are on a cell that another UAV is also on, this step.

    This is the condition a collision is charged for in the simulation, counted by UAV and not by cell: two
    UAVs on one cell are 2, and three on a cell are 3.
    """
    occupied = Counter(report.pos for report in flying_off_base(snapshot))
    return sum(count for count in occupied.values() if count > 1)


@probe("close_pairs")
def close_pairs(snapshot, knowledge=None):
    """How many pairs of UAVs are closer to each other than SECURITY_DISTANCE, this step.

    Strictly closer, compared squared as the simulation's MR2 does, so that a pair exactly
    SECURITY_DISTANCE apart is not counted.
    """
    limit_squared = config.SECURITY_DISTANCE ** 2
    pairs = 0
    for one, other in itertools.combinations(flying_off_base(snapshot), 2):
        gap_squared = (one.pos[0] - other.pos[0]) ** 2 + (one.pos[1] - other.pos[1]) ** 2
        if gap_squared < limit_squared:
            pairs += 1
    return pairs


@probe("uavs_lost")
def uavs_lost(snapshot, knowledge=None):
    """How many of the UAVs the run started with have been removed, over the run so far."""
    return len(snapshot.uavs) - len(snapshot.alive())


@probe("uavs_in_service")
def uavs_in_service(snapshot, knowledge=None):
    """How many UAVs are still flying."""
    return len(snapshot.alive())


@probe("lowest_fuel")
def lowest_fuel(snapshot, knowledge=None):
    """The lowest share of a full tank among the UAVs that are away from the base.

    A UAV on the base is waiting to be refuelled, so a low tank there is a UAV being looked after and not
    one in trouble. None when fuel is not being tracked at all, and also when no UAV is away from the base,
    because then there is nothing in the air for the fuel to run out on.
    """
    fractions = [report.fuel_fraction() for report in flying_off_base(snapshot) if report.fuel is not None]
    return min(fractions) if fractions else None


@probe("base_burning_steps")
def base_burning_steps(snapshot, knowledge=None):
    """How many steps the home base has had at least one burning cell, over the run so far.

    Cumulative, and never repaid: the base is lost when it reaches its health points, so this only goes up.
    None without a base, which is a run with firefighting switched off.
    """
    base = snapshot.base
    if base is None:
        return None
    return max(base.burning_steps, base.bhp) if base.destroyed else base.burning_steps


@probe("firefighter_deficit")
def firefighter_deficit(snapshot, knowledge=None):
    """UAVs assigned to fire fighting minus UAVs needed for it, so that a shortage is below zero.

    The sign is the paper's: a deficit of firefighters is negative (Fig. 4, the indicator on G2110).

    How many are needed is the placeholder this module's docstring mentions. None of the fleet when no fire
    is known; a quarter of it for a fire further from the base than BASE_THREAT_RADIUS; half of it inside
    that radius; and all of it once the fire is within a third of the radius, which includes a fire on the
    base itself.

    Damage the base has already taken is deliberately not part of this. It is cumulative and never repaid,
    so reading "the base was once alight" as "everyone must fight" would hold the whole fleet to fire
    fighting for the rest of the run after the first burning step. The analyzer in analyze/heuristic.py
    makes the same distinction between how bad the threat is now and how much margin is left.
    """
    base = snapshot.base
    if base is None:
        return None

    flying = snapshot.alive()
    assigned = sum(1 for report in flying if report.policy in FIREFIGHTING_POLICIES)

    fires = snapshot.known_fire()
    if not fires:
        needed = 0
    else:
        distance = min(base.distance_to(cell) for cell in fires)
        radius = config.BASE_THREAT_RADIUS
        if base.destroyed or distance <= radius / 3:
            needed = len(flying)
        elif distance <= radius:
            needed = math.ceil(len(flying) / 2)
        else:
            needed = math.ceil(len(flying) / 4)

    return assigned - needed


@probe("scout_coverage")
def scout_coverage(snapshot, knowledge):
    """The share of the grid that has been inside some UAV's observation window in the last window of steps.

    The window is BALANCE_COVERAGE_WINDOW steps, one lap of the sweep. It is measured the way the scout's own
    evaluation measures it (a square of UAV_OBSERVATION_RADIUS around the UAV, clipped at the edge of the
    grid) but over a moving window instead of the whole run, because a gap that opened half way through the
    run is exactly what this indicator is for. Cells that smoke hides from a UAV are not counted for it.

    It needs a memory, which is kept in the Knowledge base: for each cell, the step it was last in view.
    Recording a snapshot twice does no harm, because it only ever writes the step of that snapshot. None
    until a whole window has been watched, since before that a cell is unseen only because nobody has had
    time to look.
    """
    memory = knowledge.memory.setdefault("scout_coverage", {"last_seen": {}, "first_step": None})
    last_seen = memory["last_seen"]
    if memory["first_step"] is None or snapshot.step < memory["first_step"]:
        memory["first_step"] = snapshot.step

    width, height = snapshot.grid_size if snapshot.grid_size else (0, 0)
    radius = config.UAV_OBSERVATION_RADIUS

    for report in snapshot.alive():
        if report.pos is None:
            continue
        hidden = set(report.sees_occluded)
        x, y = report.pos
        for column in range(max(0, x - radius), min(width, x + radius + 1)):
            for row in range(max(0, y - radius), min(height, y + radius + 1)):
                if (column, row) not in hidden:
                    last_seen[(column, row)] = snapshot.step

    window = config.BALANCE_COVERAGE_WINDOW
    if width * height == 0 or snapshot.step - memory["first_step"] < window:
        return None

    recent = sum(1 for step in last_seen.values() if snapshot.step - step < window)
    return recent / (width * height)


# --- reading them -----------------------------------------------------------


def measure(snapshot, knowledge, names=None):
    """The current value of each named probe (every probe, by default), as {name: value or None}."""
    chosen = PROBES if names is None else {name: PROBES[name] for name in names}
    return {name: function(snapshot, knowledge) for name, function in chosen.items()}
