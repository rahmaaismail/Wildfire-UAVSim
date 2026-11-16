"""Belief: how far the case can be trusted right now, claim by claim.

The paper's method reads the assurance case at run time and asks, for each claim, how strongly it is
believed. In the paper that is Certus's job, with operators for combining evidence that are not in the
Socrates export, so what is here is a deliberately simple stand-in that behaves in the ways the method
needs and can be replaced behind `BeliefAssessor` without anything else changing:

- belief goes up the argument by the weakest link, so a claim is never believed more than what it rests on;
- an indicator whose current value is in a bad category caps the belief of the nodes it is attached to;
- an indicator nobody can rate caps it too, at UNCERTAIN, because no news is not good news; and
- a defeater nobody has answered caps the belief in what it attacks.

Every cap is a setting in config.py (BALANCE_*), because each is a guess until Simon or Certus says
otherwise. The assessment also says *why* a node is where it is (`Assessment.limits`), because a belief that
cannot explain itself cannot be used to explain a decision (MGT-BAL-18).

The inputs are plain values: the case, and for each indicator the category its probe's value was rated in
(or None). Nothing here reads a snapshot, which is what lets an option be assessed against a modified copy
of the ratings without touching the real ones.

See specs/managing/balance.md, MGT-BAL-8, MGT-BAL-9 and MGT-BAL-10.
"""

# python libraries

from dataclasses import dataclass, field
from enum import IntEnum

# own python modules

# see the note in sim/policy/random_policy.py about importing config as a module
import config
from .errors import CaseError


class Belief(IntEnum):
    """The scale of Fig. 4 of the paper, lowest to highest, so that comparing two is comparing numbers."""

    REJECT = 0
    SKEPTICAL = 1
    UNCERTAIN = 2
    LOW = 3
    HIGH = 4
    VERY_HIGH = 5

    @classmethod
    def named(cls, name):
        try:
            return cls[name]
        except KeyError:
            raise CaseError(f"{name!r} is not a belief level, expected one of "
                            f"{', '.join(level.name for level in cls)}") from None


# the names a category can have that this assessor knows what to do with. A category it does not know is an
# error and not "no cap": a new category added in Socrates would otherwise make every indicator in it look
# harmless without anyone having decided that.
OK, WARNING, FATAL = "OK", "WARNING", "FATAL"


@dataclass(frozen=True)
class Assessment:
    """The belief in every node of a case, and what held each one down.

    'limits' maps a lid to the reasons that node is not simply as believed as what it rests on, each a short
    sentence; a node that is missing from it is limited by nothing but its own support.
    """

    beliefs: dict
    limits: dict = field(default_factory=dict)

    def __getitem__(self, lid):
        return self.beliefs[lid]

    def why(self, lid):
        return tuple(self.limits.get(lid, ()))


class BeliefAssessor:
    """Assesses the belief in every node of a case from the categories its indicators are currently in."""

    def __init__(self, case):
        self.case = case

    # --- what limits a node ---------------------------------------------------

    def cap_for(self, lid, ratings):
        """The highest belief the indicators on node `lid` allow it, with the reasons, as (cap, reasons).

        `ratings` maps an indicator's name to the Category its current value is in, or to None when it is
        unrated; an indicator missing from it is unrated too. The cap is None when no indicator limits the
        node. With several indicators on a node the lowest cap wins.
        """
        warning = Belief.named(config.BALANCE_WARNING_CAP)
        fatal = Belief.named(config.BALANCE_FATAL_CAP)

        cap, reasons = None, []
        for indicator in self.case.indicators_on(lid):
            if indicator.archived:
                continue
            category = ratings.get(indicator.name)
            if category is None:
                limit, why = Belief.UNCERTAIN, f"{indicator.name} is unrated"
            else:
                kind = str(category).upper()
                if kind == OK:
                    continue
                if kind == WARNING:
                    limit = warning
                elif kind == FATAL:
                    limit = fatal
                else:
                    raise CaseError(f"the indicator {indicator.name!r} is in the category {str(category)!r}, "
                                    f"which the belief assessment has no cap for (expected {OK}, {WARNING} "
                                    f"or {FATAL})")
                why = f"{indicator.name} is {str(category)}"
            reasons.append(f"{why}: at most {limit.name}")
            cap = limit if cap is None else min(cap, limit)
        return cap, tuple(reasons)

    # --- the argument ---------------------------------------------------------

    # a defeater is unanswered when nothing below it argues against it, or when anything below it is a
    # residual: an open doubt the case states and does not resolve
    def unanswered(self, lid):
        below = [child for child in self.case.children(lid)
                 if not child.archived and child.node_type != "CONTEXT"]
        return not below or any(self.has_residual(child.lid, set()) for child in below)

    def has_residual(self, lid, seen):
        if lid in seen:
            return False
        seen.add(lid)
        node = self.case.node(lid)
        if node.node_type == "RESIDUAL":
            return True
        return any(self.has_residual(child, seen) for child in node.children
                   if not self.case.node(child).archived)

    def assess(self, ratings):
        """The Assessment of the whole case, given the rating of each indicator.

        A node's belief is the lowest of: what it rests on (the weakest of its supporting children; the
        starting belief of evidence for a leaf), what the defeaters against it leave, and what the
        indicators on it allow. Contexts say what an argument is about and add nothing to what it rests on.
        Each node is worked out once, so a node under two parents has the same belief under both.
        """
        evidence = Belief.named(config.BALANCE_EVIDENCE_BELIEF)
        residual = Belief.named(config.BALANCE_RESIDUAL_CAP)

        beliefs, limits, working = {}, {}, set()

        def believe(lid):
            if lid in beliefs:
                return beliefs[lid]
            if lid in working:
                raise CaseError(f"the case argues in a circle through node {lid}")
            working.add(lid)

            node = self.case.node(lid)
            reasons = []
            support = []
            for child in self.case.children(lid):
                if child.archived or child.node_type in ("CONTEXT", "RESIDUAL"):
                    continue
                if child.node_type == "DEFEATER":
                    if self.unanswered(child.lid):
                        support.append(residual)
                        reasons.append(f"defeater {child.lid} is unanswered: at most {residual.name}")
                    continue
                support.append(believe(child.lid))

            if support:
                level = min(support)
            elif node.node_type == "CLAIM":
                # a claim with nothing under it is an argument not yet made
                level = Belief.UNCERTAIN
                reasons.append("nothing supports it: at most UNCERTAIN")
            else:
                level = evidence

            cap, why = self.cap_for(lid, ratings)
            if cap is not None:
                level = min(level, cap)
                reasons.extend(why)

            working.discard(lid)
            beliefs[lid] = level
            if reasons:
                limits[lid] = tuple(reasons)
            return level

        for lid in self.case.nodes:
            believe(lid)
        return Assessment(beliefs=beliefs, limits=limits)


def rate_indicators(case, bound, values):
    """The category of each indicator's current probe value, as {indicator name: Category or None}.

    `bound` is Resolution.bound (indicator name -> probe name) and `values` is what the probes returned
    ({probe name: value or None}); an indicator with no probe, or whose probe gave None, is None.
    """
    ratings = {}
    for indicator in case.indicators:
        probe = bound.get(indicator.name)
        ratings[indicator.name] = indicator.rate(values.get(probe)) if probe is not None else None
    return ratings
