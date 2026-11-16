"""Balance: whether safety and performance are both believed enough for the situation the fleet is in.

The method does not ask for the most believed case, it asks for the right trade. With no fire near the base
a fleet that is safe and only modestly effective is what is wanted; with the fire at the base the fleet has
to be effective and some of the safety margin is what pays for it. So "acceptable" is not one bar but a
table, one row for each level of base threat, and the rows move in opposite directions: the least safety
belief accepted goes down as the threat goes up, and the least performance belief accepted goes up.

The table is `config.BALANCE_ACCEPTANCE`. This module only reads it; what is in it is a guess to calibrate.

See specs/managing/balance.md, MGT-BAL-11.
"""

# python libraries

from dataclasses import dataclass

# own python modules

# see the note in sim/policy/random_policy.py about importing config as a module
import config
from .belief import Belief
from .errors import CaseError


@dataclass(frozen=True)
class Verdict:
    """The outcome of judging the balance: the two beliefs, the minimums they were held to, and the result."""

    acceptable: bool
    safety: Belief
    performance: Belief
    safety_minimum: Belief
    performance_minimum: Belief

    # what fell short, in words, for the rationale of a decision
    def shortfalls(self):
        found = []
        if self.safety < self.safety_minimum:
            found.append(f"safety is {self.safety.name}, below the {self.safety_minimum.name} required")
        if self.performance < self.performance_minimum:
            found.append(f"performance is {self.performance.name}, below the "
                         f"{self.performance_minimum.name} required")
        return tuple(found)


class BalanceAssessor:
    """Judges a belief assessment against what the current base threat level calls for."""

    def __init__(self, safety_lid, performance_lid):
        self.safety_lid = safety_lid
        self.performance_lid = performance_lid

    @classmethod
    def for_case(cls, top_level):
        """Built from Bindings.top_level(case): {"safety": Node, "performance": Node}."""
        try:
            return cls(top_level["safety"].lid, top_level["performance"].lid)
        except KeyError as error:
            raise CaseError(f"the bindings name no {error.args[0]} claim") from None

    def minimums(self, threat_level):
        """The (safety, performance) minimum beliefs for a base threat level of 0 to 3."""
        table = config.BALANCE_ACCEPTANCE
        if not isinstance(threat_level, int) or not 0 <= threat_level < len(table):
            raise CaseError(f"base threat level {threat_level!r} is not one of 0 to {len(table) - 1}")
        safety, performance = table[threat_level]
        return Belief.named(safety), Belief.named(performance)

    def acceptable(self, assessment, threat_level):
        """The Verdict on `assessment` (an Assessment) at this base threat level."""
        safety_minimum, performance_minimum = self.minimums(threat_level)
        safety, performance = assessment[self.safety_lid], assessment[self.performance_lid]
        return Verdict(acceptable=safety >= safety_minimum and performance >= performance_minimum,
                       safety=safety, performance=performance,
                       safety_minimum=safety_minimum, performance_minimum=performance_minimum)
