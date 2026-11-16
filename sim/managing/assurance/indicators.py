"""Indicators: named measurements attached to the assurance case, and the rules that rate them.

An indicator is how a run-time number gets a say in the argument. Socrates stores one as a name, the nodes
it is attached to, a set of rules, and a list of timestamped data points; each rule says "when the value
satisfies these conditions, the indicator is in this category", and the categories (OK, WARNING and FATAL
in the case this was written for) are what the belief assessment then reads. Nothing here knows what any
indicator measures. A probe computes the value and this module decides what the value means.

The types are frozen, like the ones in contract.py, because a case is read once and then shared by every
option the Analyzer assesses: an option that could change an indicator in place would leave its marks on
the case the next option is assessed against. An option that wants a different case builds a different one.

See specs/managing/balance.md, MGT-BAL-3 and MGT-BAL-4.
"""

# python libraries

import operator
from dataclasses import dataclass, field
from datetime import datetime

# own python modules

from .errors import CaseError


# --- what a value is compared with ------------------------------------------


# the comparators Socrates offers, as the names it writes in an export. A name that is not here is an error
# when the case is loaded rather than a condition that quietly never matches, because a rule that never
# matches leaves a value unrated and the case then looks less certain than it is for no visible reason.
COMPARATORS = {
    "LT": operator.lt,
    "LTE": operator.le,
    "GT": operator.gt,
    "GTE": operator.ge,
    "EQ": operator.eq,
}

COMBINATORS = ("AND", "OR")


# the timestamp format of an export, which is ISO 8601 with milliseconds and an offset written without a
# colon ("2026-11-02T22:54:20.000+0000"). datetime.fromisoformat takes that from Python 3.11 onwards and
# not before, and a case should read the same on whichever Python the machine has.
TIMESTAMP_FORMATS = ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z")


def parse_timestamp(text):
    for form in TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(str(text), form)
        except ValueError:
            continue
    raise CaseError(f"cannot read {text!r} as a timestamp")


# --- the pieces of an indicator ----------------------------------------------


@dataclass(frozen=True)
class Category:
    """What a rating is called, and how severe it is.

    'priority' is Socrates's own ordering of the categories and the only thing that says which is worse:
    a higher number is more severe. It is what resolves a value that two rules both claim, and it is read
    from the file instead of being assumed from the names, because the names are the case author's.
    """

    id: int
    name: str
    priority: int
    color: str = ""

    def __str__(self):
        return self.name


@dataclass(frozen=True)
class Condition:
    """One comparison of a value with a number."""

    comparator: str
    value: float

    def __post_init__(self):
        if self.comparator not in COMPARATORS:
            raise CaseError(f"unknown comparator {self.comparator!r}, expected one of "
                            f"{', '.join(sorted(COMPARATORS))}")

    def holds(self, value):
        return COMPARATORS[self.comparator](value, self.value)


@dataclass(frozen=True)
class Rule:
    """When an indicator's value is in a category: its conditions, combined by AND or by OR."""

    name: str
    category: Category
    conditions: tuple
    combinator: str = "OR"

    def __post_init__(self):
        if self.combinator not in COMBINATORS:
            raise CaseError(f"rule {self.name!r} has the combinator {self.combinator!r}, "
                            f"expected one of {', '.join(COMBINATORS)}")
        object.__setattr__(self, "conditions", tuple(self.conditions))

    def holds(self, value):
        results = [condition.holds(value) for condition in self.conditions]
        # a rule with no conditions says nothing, and must not count as satisfied by every value
        if not results:
            return False
        return all(results) if self.combinator == "AND" else any(results)


@dataclass(frozen=True)
class DataPoint:
    """One reading of an indicator, with when it was taken."""

    created: str
    value: float

    def when(self):
        return parse_timestamp(self.created)


@dataclass(frozen=True)
class Indicator:
    """A measurement attached to the nodes of the case it bears on.

    'nodes' are the lids of those nodes, which is the number Socrates shows next to a node and the key an
    export uses for an indicator's nodes; see case.py for how that differs from the key it uses elsewhere.
    """

    id: int
    name: str
    nodes: tuple
    rules: tuple
    data: tuple = ()
    description: str = ""
    path: str = None
    archived: bool = False
    creator: str = field(default="", compare=False)

    def __post_init__(self):
        object.__setattr__(self, "nodes", tuple(self.nodes))
        object.__setattr__(self, "rules", tuple(self.rules))
        object.__setattr__(self, "data", tuple(self.data))

    # the data point with the latest timestamp, or None when there are none. Not the last in the list: the
    # first export of this case held three points listed out of time order, and taking the last of them
    # would have read a value that had long been replaced as the current one.
    def latest(self):
        if not self.data:
            return None
        return max(self.data, key=lambda point: point.when())

    # the category a value falls in, or None when no rule covers it.
    #
    # Two things here are decisions rather than mechanics. A value that two rules both claim takes the more
    # severe of the two, because the rules in a case are written by hand and an edit can make them overlap,
    # and the safe reading of an overlap is the worse one. And a value that no rule covers is rated None
    # and not the mildest category: an unrated indicator is a gap in what the case knows, and the belief
    # assessment counts it as one rather than as good news.
    def rate(self, value):
        if value is None:
            return None
        matching = [rule.category for rule in self.rules if rule.holds(value)]
        if not matching:
            return None
        return max(matching, key=lambda category: category.priority)

    # the category of the current value, None when there is no value to rate or no rule covers it
    def current(self):
        point = self.latest()
        return None if point is None else self.rate(point.value)
