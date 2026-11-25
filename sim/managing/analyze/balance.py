"""The balance analyser: the Analyzer of the BALANCE method (Fig. 3 of the paper).

Each evaluation it does what the figure's left hand side does. It assesses belief in the assurance case from
the indicators the probes feed, and assesses whether the balance between safety and performance is
acceptable for the situation the base is in. Where it is not, or the base is threatened enough that a trade
is worth considering, it generates options, assesses each on the case that option argues for, discards the
ones that leave the balance unacceptable, and chooses among the rest the one with the best performance.

The choice is left in the Knowledge base for the planner (`BalancePlanner`) to carry out, and the
analyser's reading of the *situation* is still the heuristic one: base threat and crowding come from
`HeuristicAnalyzer`, because what counts as a threat to the base is not what this method is about. What it
changes is that crowding next to a base that is being fought for is not a symptom to act on while clearance
has been deliberately relaxed there.

If the case cannot be read or trusted the analyser says so once and carries on as the heuristic one,
because a managing system that stops managing when its argument is unreadable is worse than one that
manages without it (MGT-BAL-17).

See specs/managing/balance.md, MGT-BAL-12, MGT-BAL-13, MGT-BAL-17 and MGT-BAL-18.
"""

# python libraries

import logging
import os
import pathlib
from dataclasses import replace

# own python modules

# see the note in sim/policy/random_policy.py about importing config as a module
import config
from ..assurance import Bindings, CaseError, JsonCaseSource, UrlCaseSource
from ..assurance.balance import BalanceAssessor
from ..assurance.belief import BeliefAssessor, explain, rate_indicators
from ..assurance.options import OptionGenerator, separation_of
from ..assurance.probes import PROBES, measure
from .base import Analyzer
from .heuristic import HeuristicAnalyzer

LOG = logging.getLogger(__name__)


class BalanceAnalyzer(Analyzer):
    """Chooses between keeping, relaxing and restoring clearance by assessing each against the case."""

    name = "balance"

    def __init__(self, source=None, bindings=None, generator=None, heuristic=None):
        self.heuristic = heuristic if heuristic is not None else HeuristicAnalyzer()
        self.generator = generator if generator is not None else OptionGenerator()
        self.case = self.bound = self.beliefs = self.judge = None
        # why the case could not be used, or None when it can
        self.failure = None

        try:
            root = pathlib.Path(config.__file__).resolve().parent
            source = source if source is not None else self.default_source(root)
            bindings = bindings if bindings is not None else Bindings.load(root / config.BALANCE_BINDINGS_FILE)
            self.case = source.load()
            self.bound = bindings.resolve(self.case, PROBES, log=LOG).bound
            self.beliefs = BeliefAssessor(self.case)
            self.judge = BalanceAssessor.for_case(bindings.top_level(self.case))
        except CaseError as error:
            self.fail(error)

    # where the case comes from when nobody said: the address in config if there is one, else the file. The
    # token for an address that needs one comes from the environment, because config.py is a public file.
    @staticmethod
    def default_source(root):
        if config.BALANCE_CASE_URL:
            return UrlCaseSource(config.BALANCE_CASE_URL, timeout=config.BALANCE_CASE_TIMEOUT,
                                 token=os.environ.get("BALANCE_CASE_TOKEN"))
        return JsonCaseSource(root / config.BALANCE_CASE_FILE)

    def fail(self, error):
        if self.failure is None:
            LOG.warning("the assurance case cannot be used, so the balance analyser falls back to the "
                        "heuristic one: %s", error)
        self.failure = str(error)
        self.case = None

    # --- the loop -----------------------------------------------------------

    def analyze(self, snapshot, knowledge=None):
        symptoms = self.heuristic.analyze(snapshot, knowledge)
        if knowledge is None:
            return symptoms
        memory = knowledge.memory.setdefault("balance", {})
        memory["option"] = None

        if self.case is None:
            memory["rationale"] = f"assurance case unavailable ({self.failure}): heuristic analysis only"
            return symptoms

        try:
            return self.assess(snapshot, symptoms, knowledge, memory)
        except CaseError as error:
            self.fail(error)
            memory["rationale"] = f"assurance case unusable ({error}): heuristic analysis only"
            return symptoms

    # --- one option ---------------------------------------------------------

    def evaluate(self, option, values, threat):
        """Belief and balance for the case this option argues from. Returns (verdict, assessment)."""
        case = option.adapted_case(self.case, self.bound)
        ratings = rate_indicators(case, self.bound, option.adapted_values(values))
        assessment = BeliefAssessor(case).assess(ratings)
        return self.judge.acceptable(assessment, threat), assessment, case

    def assess(self, snapshot, symptoms, knowledge, memory):
        threat = symptoms.base_threat
        values = measure(snapshot, knowledge, sorted(set(self.bound.values())))

        keep = self.generator.keep(snapshot, threat)
        keep_verdict, keep_assessment, keep_case = self.evaluate(keep, values, threat)
        options = self.generator.generate(snapshot, threat, keep_verdict.acceptable)

        kept, discarded = [], []
        for option in options:
            verdict, _, _ = self.evaluate(option, values, threat)
            if verdict.acceptable:
                kept.append((option, verdict))
            else:
                discarded.append((option, verdict))

        if kept:
            # the best performance wins; among equals restoring clearance, then the fewest changes
            chosen, verdict = min(kept, key=lambda pair: (-pair[1].performance, pair[0].preference(),
                                                          pair[0].changes()))
            reason = "balance acceptable"
        else:
            chosen, verdict = keep, keep_verdict
            reason = "no option keeps the balance acceptable, so nothing is changed"

        # what is holding the two top-level claims down in the situation as it stands, which is the thing a
        # reader of the log most needs and the thing the beliefs alone do not say
        held = {role: explain(keep_case, keep_assessment, lid)
                for role, lid in (("safety", self.judge.safety_lid), ("performance", self.judge.performance_lid))}
        if LOG.isEnabledFor(logging.DEBUG):
            ratings = rate_indicators(keep_case, self.bound, values)
            LOG.debug("step %d indicators: %s", snapshot.step, "; ".join(
                f"{name}={values.get(probe)} ({ratings[name]})" for name, probe in sorted(self.bound.items())))
        memory["option"] = chosen
        memory["verdict"] = verdict
        memory["readings"] = values
        memory["held"] = held
        memory["rationale"] = self.rationale(threat, chosen, verdict, discarded, reason, held)

        pending = [report.uav_id for report in snapshot.alive()
                   if report.uav_id in chosen.uav_ids and chosen.separation() is not None
                   and separation_of(report) != chosen.separation()]
        if not pending:
            # nothing to change this evaluation, so no run of agreeing evaluations is under way; a streak
            # left over from earlier would let two evaluations that did not follow one another count as two
            memory["streaks"] = {}

        # crowding next to a base whose clearance has been deliberately relaxed is the trade, not a symptom
        crowding = symptoms.crowding
        if chosen.waived:
            near = {report.uav_id for report in self.generator.fighters(snapshot)
                    if snapshot.base is not None
                    and snapshot.base.distance_to(report.pos) <= config.BASE_THREAT_RADIUS}
            crowding = {uav_id: count for uav_id, count in crowding.items() if uav_id not in near}

        return replace(symptoms, crowding=crowding, requested=bool(pending))

    # one line a reader can follow: what was chosen, the beliefs it rests on, and what was turned down and why
    def rationale(self, threat, chosen, verdict, discarded, reason, held=None):
        text = (f"balance at base threat {threat}: {chosen} ({reason}); safety {verdict.safety.name} "
                f"(needs {verdict.safety_minimum.name}), performance {verdict.performance.name} "
                f"(needs {verdict.performance_minimum.name})")
        turned_down = [f"{option} ({'; '.join(v.shortfalls())})" for option, v in discarded
                       if option is not chosen]
        text += f"; discarded: {', '.join(turned_down)}" if turned_down else ""
        for role, reasons in (held or {}).items():
            if reasons:
                text += f"; {role} held by {' & '.join(reasons)}"
        return text
