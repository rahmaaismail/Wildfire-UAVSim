"""An indicator's value is rated by the rule it satisfies, and the current value is the latest in time.

The rules of a case are written by hand in Socrates, and the data points are entered or posted over time, so
neither can be assumed tidy. Two things in the first export of this case motivate most of what is here: the
three data points of the first indicator were listed out of time order, so the last in the file was not the
latest taken; and a rule written as a range ("at least 3 and below 6") only works if AND really means both.

The boundaries of every indicator in the real export are checked, one value either side of each, because a
threshold off by one in a rule is the kind of mistake that sits unnoticed until the run it matters in.
"""

# python libraries

import pathlib

import pytest

# own python modules

from sim.managing.assurance import Category, Condition, DataPoint, Indicator, JsonCaseSource, Rule

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
REAL_CASE = REPO_ROOT / "assurance" / "scout_case.json"

OK, WARNING, FATAL = Category(1, "OK", 1), Category(2, "WARNING", 2), Category(3, "FATAL", 3)


def condition(comparator, value):
    return Condition(comparator, value)


def indicator(rules, data=()):
    return Indicator(id=1, name="x", nodes=(1,), rules=rules, data=data)


def at(timestamp, value):
    return DataPoint(created=timestamp, value=value)


# --- rating a value ----------------------------------------------------------


# one value either side of every threshold the indicators of the case were given. A value is a whole number
# for the counts and a fraction for the two shares.
BOUNDARIES = [
    ("Num UAVs Sharing Cell", 0, "OK"), ("Num UAVs Sharing Cell", 1, "WARNING"),
    ("Num UAVs Sharing Cell", 2, "WARNING"), ("Num UAVs Sharing Cell", 3, "FATAL"),
    ("UAV pairs closer than SECURITY_DISTANCE", 2, "OK"),
    ("UAV pairs closer than SECURITY_DISTANCE", 3, "WARNING"),
    ("UAV pairs closer than SECURITY_DISTANCE", 5, "WARNING"),
    ("UAV pairs closer than SECURITY_DISTANCE", 6, "FATAL"),
    ("UAVs lost", 0, "OK"), ("UAVs lost", 1, "WARNING"), ("UAVs lost", 2, "FATAL"),
    ("Lowest fuel fraction", 0.2, "OK"), ("Lowest fuel fraction", 0.19, "WARNING"),
    ("Lowest fuel fraction", 0.1, "WARNING"), ("Lowest fuel fraction", 0.099, "FATAL"),
    ("Firefighter deficit", 0, "OK"), ("Firefighter deficit", -1, "WARNING"),
    ("Firefighter deficit", -2, "WARNING"), ("Firefighter deficit", -3, "FATAL"),
    ("Base burning steps (of 4)", 0, "OK"), ("Base burning steps (of 4)", 1, "WARNING"),
    ("Base burning steps (of 4)", 2, "WARNING"), ("Base burning steps (of 4)", 3, "FATAL"),
    ("Scout coverage", 0.9, "OK"), ("Scout coverage", 0.89, "WARNING"),
    ("Scout coverage", 0.7, "WARNING"), ("Scout coverage", 0.69, "FATAL"),
    ("UAVs in service", 8, "OK"), ("UAVs in service", 7, "WARNING"),
    ("UAVs in service", 6, "WARNING"), ("UAVs in service", 5, "FATAL"),
]


@pytest.mark.verifies("MGT-BAL-3")
@pytest.mark.parametrize("name, value, expected", BOUNDARIES)
def test_each_threshold_of_the_real_case_rates_the_value_either_side_of_it(name, value, expected):
    case = JsonCaseSource(REAL_CASE).load()
    rated = case.indicator(name).rate(value)
    assert rated is not None and rated.name == expected


@pytest.mark.verifies("MGT-BAL-3")
def test_a_rule_written_as_a_range_needs_both_of_its_conditions():
    # "at least 3 and below 6", as the separation indicator's WARNING rule is
    rated = indicator([Rule("range", WARNING, [condition("GTE", 3), condition("LT", 6)], "AND")])
    assert rated.rate(3) is WARNING and rated.rate(5.9) is WARNING
    assert rated.rate(2.9) is None and rated.rate(6) is None


@pytest.mark.verifies("MGT-BAL-3")
def test_a_rule_combined_by_or_needs_only_one_of_its_conditions():
    rated = indicator([Rule("either", FATAL, [condition("LT", 0), condition("GT", 10)], "OR")])
    assert rated.rate(-1) is FATAL and rated.rate(11) is FATAL and rated.rate(5) is None


@pytest.mark.verifies("MGT-BAL-3")
def test_a_value_two_rules_both_claim_takes_the_more_severe_category():
    # the rules are hand written and an edit can make them overlap; the safe reading of an overlap is the worse
    overlapping = indicator([Rule("mild", WARNING, [condition("GTE", 1)]),
                             Rule("bad", FATAL, [condition("GTE", 2)])])
    assert overlapping.rate(2) is FATAL
    assert overlapping.rate(1) is WARNING
    # and the order the rules are listed in makes no difference
    reversed_rules = indicator(list(reversed(overlapping.rules)))
    assert reversed_rules.rate(2) is FATAL


@pytest.mark.verifies("MGT-BAL-3")
def test_a_value_no_rule_covers_is_unrated_and_not_ok():
    # a gap in the rules is a gap in what the case knows, and the belief assessment has to be able to see it
    gap = indicator([Rule("low", OK, [condition("LTE", 2)]), Rule("high", FATAL, [condition("GTE", 6)])])
    assert gap.rate(4) is None


@pytest.mark.verifies("MGT-BAL-3")
def test_no_value_at_all_is_unrated():
    assert indicator([Rule("any", OK, [condition("GTE", 0)])]).rate(None) is None


@pytest.mark.verifies("MGT-BAL-3")
@pytest.mark.parametrize("combinator", ["OR", "AND"])
def test_a_rule_with_no_conditions_matches_nothing_rather_than_everything(combinator):
    # "all of nothing" is true, so without a guard an empty AND rule would claim every value there is
    assert indicator([Rule("empty", OK, [], combinator)]).rate(5) is None


# --- the current value -------------------------------------------------------


@pytest.mark.verifies("MGT-BAL-4")
def test_the_current_value_is_the_latest_by_timestamp_and_not_the_last_in_the_list():
    # the first export of this case listed its three points in this order; the file's last is not the latest
    points = [at("2026-11-02T22:53:59.000+0000", 0.0),
              at("2026-11-02T22:54:20.000+0000", 5.0),
              at("2026-11-02T22:54:05.000+0000", 1.0)]
    assert indicator([], points).latest().value == 5.0


@pytest.mark.verifies("MGT-BAL-4")
def test_timestamps_in_different_offsets_are_compared_as_instants():
    # 23:00 at UTC-7 is 06:00 the next day in UTC, which is later than 01:00 UTC
    points = [at("2026-11-03T01:00:00.000+0000", 1.0), at("2026-11-02T23:00:00.000-0700", 2.0)]
    assert indicator([], points).latest().value == 2.0


@pytest.mark.verifies("MGT-BAL-4")
def test_an_indicator_with_no_data_has_no_current_value_and_no_current_rating():
    empty = indicator([Rule("any", OK, [condition("GTE", 0)])])
    assert empty.latest() is None and empty.current() is None


@pytest.mark.verifies("MGT-BAL-4")
def test_the_current_rating_is_the_rating_of_the_latest_value():
    rules = [Rule("fine", OK, [condition("EQ", 0)]), Rule("bad", FATAL, [condition("GTE", 3)])]
    points = [at("2026-11-02T22:54:20.000+0000", 5.0), at("2026-11-02T22:53:59.000+0000", 0.0)]
    assert indicator(rules, points).current() is FATAL
