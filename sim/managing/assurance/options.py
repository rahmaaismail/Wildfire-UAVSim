"""Options: the changes the managing system can weigh against each other, and the case each one argues from.

An option in the BALANCE method is a candidate change to the system *together with* the change to the
assurance case that justifies it. Relaxing the clearance between fire fighters near the base is not only a
different order to the UAVs; it is also the case no longer asking for something it has chosen to give up, and
that is what makes it possible to assess the change before making it: build the case the option argues for,
assess belief on that, and see whether the balance still holds.

Three options exist, and they differ in one thing, which is each UAV's `separation`, the cushion of cells the
fleet's traffic rules keep around a team mate. 0 keeps only occupied cells clear; `ceil(SECURITY_DISTANCE)`
keeps the cushion the safety argument was made for. Relaxing sets the first for the fire fighters near the
base, restoring sets the second, and keeping sets nothing.

What an option may *not* do is as important: it never gives up the guarantee that no two UAVs share a cell,
which is the claim the collisions argument actually rests on. Relaxing archives the indicator on pairs
closer than SECURITY_DISTANCE in the adapted case, because that is the cushion being traded, and leaves the
indicator on UAVs sharing a cell exactly where it was (MGT-BAL-14).

Nothing here changes a case, a snapshot or a directive in place. An option produces new ones.

See specs/managing/balance.md, MGT-BAL-12, MGT-BAL-13 and MGT-BAL-14.
"""

# python libraries

import math
from dataclasses import dataclass, replace

# own python modules

# see the note in sim/policy/random_policy.py about importing config as a module
import config
from ..contract import UavDirective
from .probes import FIREFIGHTING_POLICIES

# the probe whose indicator is the cushion that relaxing gives up, and the probe whose indicator is the
# guarantee that it never does. Named by probe and not by indicator because the indicators are the case
# author's to rename and the probes are the code's; the bindings say which is which.
CUSHION_PROBE = "close_pairs"
GUARANTEE_PROBE = "shared_cells"
EFFORT_PROBE = "firefighter_deficit"


def cushion():
    """The separation that keeps the clearance the safety argument was made for."""
    return math.ceil(config.SECURITY_DISTANCE)


def separation_of(report):
    """The separation a UAV is flying under. A UAV never told one is at 0, the default."""
    return int(report.params.get("separation", 0))


@dataclass(frozen=True)
class Option:
    """One candidate: which UAVs it changes, and whether the clearance is waived if it is taken."""

    uav_ids: tuple = ()
    # whether the case the option argues from has given up the cushion indicator
    waived: bool = False

    # what it is called in a rationale
    name = "keep"

    def __post_init__(self):
        object.__setattr__(self, "uav_ids", tuple(self.uav_ids))

    # the separation it sets on the UAVs it changes, or None when it sets none
    def separation(self):
        return None

    # how it ranks among options that are equal on belief: lower goes first. Restoring clearance is where
    # the fleet rests when nothing threatens the base, so it wins a tie over leaving things as they are.
    def preference(self):
        return 1

    # how many UAVs it changes, which is what a tie between two options is otherwise settled by
    def changes(self):
        return len(self.uav_ids) if self.separation() is not None else 0

    def directives(self, snapshot):
        """The orders that carry it out: each UAV it names keeps its policy and its other settings, with
        a different separation. A UAV that is no longer flying is left out."""
        separation = self.separation()
        if separation is None:
            return ()
        return tuple(UavDirective(report.uav_id, report.policy, {**report.params, "separation": separation})
                     for report in snapshot.alive() if report.uav_id in self.uav_ids)

    def adapted_case(self, case, bound):
        """A copy of `case` as this option argues it. `bound` is Resolution.bound, which says which
        indicator belongs to which probe. The original is not touched: indicators are frozen, and the copy
        is made of replaced ones."""
        if not self.waived:
            return case
        waived = {name for name, probe in bound.items() if probe == CUSHION_PROBE}
        indicators = tuple(replace(indicator, archived=True) if indicator.name in waived else indicator
                           for indicator in case.indicators)
        return replace(case, indicators=indicators)

    def adapted_values(self, values):
        """What the probes would read if this option were in force, as a copy of `values`. Only the effort
        is adapted: the others are measured from where the UAVs are, which an option does not move."""
        return dict(values)

    def effort(self, values, shift):
        """`values` with the firefighter deficit moved by `shift` whole fire fighters, never past zero in
        the good direction: relaxing is not credited with a surplus."""
        adapted = dict(values)
        deficit = adapted.get(EFFORT_PROBE)
        if deficit is None or shift == 0:
            return adapted
        if shift > 0:
            adapted[EFFORT_PROBE] = deficit if deficit >= 0 else min(0, deficit + shift)
        else:
            adapted[EFFORT_PROBE] = deficit + shift
        return adapted

    def __str__(self):
        return self.name if not self.uav_ids else f"{self.name} {list(self.uav_ids)}"


@dataclass(frozen=True)
class KeepCurrent(Option):
    name = "keep"


@dataclass(frozen=True)
class RelaxClearance(Option):
    """Fly without the cushion: separation 0, the shared-cell guarantee untouched."""

    waived: bool = True
    name = "relax clearance"

    def separation(self):
        return 0

    def adapted_values(self, values):
        return self.effort(values, math.floor(config.BALANCE_RELAX_GAIN * len(self.uav_ids)))


@dataclass(frozen=True)
class RestoreClearance(Option):
    """Put the cushion back: separation of `ceil(SECURITY_DISTANCE)`."""

    waived: bool = False
    name = "restore clearance"

    def separation(self):
        return cushion()

    def preference(self):
        return 0

    def adapted_values(self, values):
        return self.effort(values, -math.floor(config.BALANCE_RELAX_GAIN * len(self.uav_ids)))


class OptionGenerator:
    """Proposes the options worth assessing for a snapshot."""

    def fighters(self, snapshot):
        return [report for report in snapshot.alive()
                if report.policy in FIREFIGHTING_POLICIES and report.pos is not None]

    # whether the fleet is flying the relaxed clearance: the base is threatened and somebody near it is
    # flying without the cushion
    def relaxed(self, snapshot, threat):
        base = snapshot.base
        return (threat >= config.BALANCE_RELAX_THREAT and base is not None and any(
            separation_of(report) == 0 and base.distance_to(report.pos) <= config.BASE_THREAT_RADIUS
            for report in self.fighters(snapshot)))

    def keep(self, snapshot, threat):
        """Leaving things as they are, which is itself an option and the one every other is compared with."""
        return KeepCurrent(waived=self.relaxed(snapshot, threat))

    def generate(self, snapshot, threat, acceptable):
        """The options for this step, always starting with keeping things as they are.

        Relaxing is offered when the balance has been lost or the base threat has reached
        `config.BALANCE_RELAX_THREAT`, for the fire fighters that are near the base and still hold the
        cushion. Restoring is offered below that threat for every fire fighter that has no cushion.
        """
        fighters = self.fighters(snapshot)
        base = snapshot.base
        threatened = threat >= config.BALANCE_RELAX_THREAT

        options = [self.keep(snapshot, threat)]

        if (threatened or not acceptable) and base is not None:
            candidates = [report.uav_id for report in fighters if separation_of(report) > 0
                          and base.distance_to(report.pos) <= config.BASE_THREAT_RADIUS]
            if candidates:
                options.append(RelaxClearance(uav_ids=tuple(candidates)))

        loose = [report.uav_id for report in fighters if separation_of(report) == 0]
        if not threatened and loose:
            options.append(RestoreClearance(uav_ids=tuple(loose)))

        return tuple(options)
