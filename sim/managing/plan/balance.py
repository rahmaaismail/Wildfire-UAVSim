"""The balance planner: carries out the option the balance analyser chose.

Which UAVs fly which policy is still the heuristic planner's decision, and is left to it. What this adds is
the one thing the BALANCE options change, each UAV's `separation`. Every directive the heuristic planner
issues is given a separation, so that re-allocating the team does not quietly put every UAV back on the
default clearance (a directive's params replace a UAV's settings rather than adding to them), and a UAV
the option changes is given the option's separation once the change has held.

"Held" means that the same option has been chosen for that UAV `Knowledge.hysteresis` evaluations running.
The count is kept in the Knowledge base's memory and not in its streaks, which the heuristic planner uses
for policy names and which a separation would overwrite. A fire front sitting on the relax threshold would
otherwise flip the fleet between the two modes on alternate steps and spend the run turning round
(MGT-BAL-15).

On a step where the only thing wanted is a change of separation, the heuristic planner is not consulted at
all: it would answer a quiet snapshot by re-issuing the default policy to everybody.

See specs/managing/balance.md, MGT-BAL-15 and MGT-BAL-18.
"""

# python libraries

from dataclasses import replace

# own python modules

from ..assurance.options import separation_of
from ..contract import Allocation, UavDirective
from .base import Planner
from .heuristic import HeuristicPlanner


class BalancePlanner(Planner):
    """HeuristicPlanner's allocation, with the chosen option's separation applied once it has held."""

    name = "balance"

    def __init__(self, base=None):
        self.base = base if base is not None else HeuristicPlanner()

    def plan(self, snapshot, symptoms, knowledge=None):
        memory = knowledge.memory.get("balance", {}) if knowledge is not None else {}
        option = memory.get("option")

        quiet = not replace(symptoms, requested=False).requires_adaptation()
        if quiet:
            allocation = Allocation(step=snapshot.step)
        else:
            allocation = self.base.plan(snapshot, symptoms, knowledge)

        applied = self.settled(snapshot, option, knowledge, memory)

        directives = {directive.uav_id: directive for directive in allocation.directives}
        for report in snapshot.alive():
            directive = directives.get(report.uav_id)
            if directive is None and report.uav_id not in applied:
                continue

            # a separation the heuristic planner asked for (a UAV opening a gap) is not overridden
            asked = directive is not None and "separation" in directive.params
            if directive is None:
                directive = UavDirective(report.uav_id, report.policy, dict(report.params))

            params = dict(directive.params)
            if not asked:
                params["separation"] = applied.get(report.uav_id, separation_of(report))
            directives[report.uav_id] = replace(directive, params=params)

        rationale = memory.get("rationale", "") or allocation.rationale
        if not quiet and allocation.rationale and memory.get("rationale"):
            rationale = f"{allocation.rationale} | {memory['rationale']}"
        return Allocation(step=snapshot.step, directives=tuple(directives.values()), rationale=rationale)

    # {uav id: separation} for the UAVs whose change has been wanted long enough to be made now
    def settled(self, snapshot, option, knowledge, memory):
        if option is None or option.separation() is None or knowledge is None:
            return {}
        streaks = memory.setdefault("streaks", {})
        target, applied = option.separation(), {}
        for report in snapshot.alive():
            if report.uav_id not in option.uav_ids or separation_of(report) == target:
                continue
            wanted, count = streaks.get(report.uav_id, (None, 0))
            count = count + 1 if wanted == target else 1
            streaks[report.uav_id] = (target, count)
            if count >= knowledge.hysteresis:
                applied[report.uav_id] = target
        return applied
