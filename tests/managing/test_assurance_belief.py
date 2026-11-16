"""Belief is the weakest link, held down by indicators and by doubts nobody has answered.

The small cases here are built by hand so that each rule can be seen on its own; the real case then shows
that the rules do the right thing on the argument the method will actually run against.
"""

# python libraries

import pathlib

import pytest

# own python modules

import config
from sim.managing.assurance import Bindings, Category, CaseError, JsonCaseSource
from sim.managing.assurance.balance import BalanceAssessor
from sim.managing.assurance.belief import Belief, BeliefAssessor, rate_indicators
from sim.managing.assurance.probes import PROBES

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
OK, WARNING, FATAL = Category(1, "OK", 1), Category(2, "WARNING", 2), Category(3, "FATAL", 3)


def case_of(nodes, indicators=(), archived=()):
    """A case from (lid, type, children) triples; ids are lid + 1000 so the two are never confused."""
    parents = {lid: [] for lid, _, _ in nodes}
    for lid, _, children in nodes:
        for child in children:
            parents[child].append(lid + 0)
    entries = [{"lid": lid, "id": lid + 1000, "nodeType": kind, "text": kind.lower(), "archived": lid in archived, "children": children,
                "parents": [parent + 1000 for parent in parents[lid]]} for lid, kind, children in nodes]
    payload = {"schema": {"version": 14}, "name": "t", "nodes": entries, "indicators": [
        {"id": index, "name": name, "nodes": attached, "rules": []}
        for index, (name, attached) in enumerate(indicators)],
        "indicatorRuleCategories": [{"id": 1, "name": "OK", "priority": 1},
                                    {"id": 2, "name": "WARNING", "priority": 2},
                                    {"id": 3, "name": "FATAL", "priority": 3}]}
    return JsonCaseSource.parse(payload)


# a claim over one claim over one piece of evidence
CHAIN = [(1, "CLAIM", [2]), (2, "CLAIM", [3]), (3, "EVIDENCE", [])]


@pytest.mark.verifies("MGT-BAL-8")
def test_evidence_starts_at_the_configured_belief_and_a_claim_is_no_higher_than_it():
    result = BeliefAssessor(case_of(CHAIN)).assess({})
    assert result[3] == Belief.named(config.BALANCE_EVIDENCE_BELIEF)
    assert result[1] == result[2] == result[3]


@pytest.mark.verifies("MGT-BAL-8")
def test_a_claim_is_as_believed_as_its_weakest_support_not_its_average():
    case = case_of([(1, "CLAIM", [2, 3, 4]), (2, "EVIDENCE", []), (3, "EVIDENCE", []), (4, "EVIDENCE", [])],
                   [("low one", [3])])
    result = BeliefAssessor(case).assess({"low one": FATAL})
    assert result[2] == result[4] == Belief.named(config.BALANCE_EVIDENCE_BELIEF)
    assert result[1] == result[3] == Belief.named(config.BALANCE_FATAL_CAP)


@pytest.mark.verifies("MGT-BAL-8")
def test_a_node_under_two_parents_has_one_belief_under_both():
    case = case_of([(1, "CLAIM", [3]), (2, "CLAIM", [3]), (3, "CLAIM", [4]), (4, "EVIDENCE", [])],
                   [("gauge", [3])])
    result = BeliefAssessor(case).assess({"gauge": FATAL})
    cap = Belief.named(config.BALANCE_FATAL_CAP)
    assert result[3] == result[1] == result[2] == cap


@pytest.mark.verifies("MGT-BAL-8")
def test_a_claim_with_nothing_under_it_is_uncertain():
    result = BeliefAssessor(case_of([(1, "CLAIM", [])])).assess({})
    assert result[1] == Belief.UNCERTAIN


@pytest.mark.verifies("MGT-BAL-8")
def test_contexts_add_nothing_and_archived_nodes_are_ignored():
    case = case_of([(1, "CLAIM", [2, 3]), (2, "EVIDENCE", []), (3, "CONTEXT", [])])
    assert BeliefAssessor(case).assess({})[1] == Belief.named(config.BALANCE_EVIDENCE_BELIEF)


@pytest.mark.verifies("MGT-BAL-8")
def test_an_archived_node_no_longer_counts_towards_belief():
    case = case_of([(1, "CLAIM", [2, 3]), (2, "EVIDENCE", []), (3, "DEFEATER", [])], archived=(3,))
    assert BeliefAssessor(case).assess({})[1] == Belief.named(config.BALANCE_EVIDENCE_BELIEF)


@pytest.mark.verifies("MGT-BAL-8")
def test_an_unanswered_defeater_caps_the_claim_it_attacks(monkeypatch):
    monkeypatch.setattr(config, "BALANCE_RESIDUAL_CAP", "SKEPTICAL")
    # a defeater with nothing below it, and one whose answer ends in a residual: both are open doubts
    for tail in ([(4, "DEFEATER", [])], [(4, "DEFEATER", [5]), (5, "CLAIM", [6]), (6, "RESIDUAL", [])]):
        case = case_of([(1, "CLAIM", [2, 4]), (2, "EVIDENCE", [])] + tail)
        result = BeliefAssessor(case).assess({})
        assert result[1] == Belief.SKEPTICAL
        assert "defeater 4" in result.why(1)[0]


@pytest.mark.verifies("MGT-BAL-8")
def test_an_answered_defeater_caps_nothing(monkeypatch):
    monkeypatch.setattr(config, "BALANCE_RESIDUAL_CAP", "SKEPTICAL")
    case = case_of([(1, "CLAIM", [2, 4]), (2, "EVIDENCE", []), (4, "DEFEATER", [5]), (5, "EVIDENCE", [])])
    assert BeliefAssessor(case).assess({})[1] == Belief.named(config.BALANCE_EVIDENCE_BELIEF)


@pytest.mark.verifies("MGT-BAL-8")
def test_a_circular_argument_is_an_error_not_an_endless_loop():
    case = case_of([(1, "CLAIM", [2]), (2, "CLAIM", [1])])
    with pytest.raises(CaseError, match="circle"):
        BeliefAssessor(case).assess({})


@pytest.mark.verifies("MGT-BAL-9")
@pytest.mark.parametrize("category, cap", [(OK, None), (WARNING, "BALANCE_WARNING_CAP"),
                                           (FATAL, "BALANCE_FATAL_CAP")])
def test_the_category_of_an_indicator_caps_its_node(category, cap):
    case = case_of(CHAIN, [("gauge", [2])])
    result = BeliefAssessor(case).assess({"gauge": category})
    expected = Belief.named(config.BALANCE_EVIDENCE_BELIEF) if cap is None else Belief.named(getattr(config, cap))
    assert result[2] == result[1] == expected
    # what is under the indicator's node is not touched by it
    assert result[3] == Belief.named(config.BALANCE_EVIDENCE_BELIEF)


@pytest.mark.verifies("MGT-BAL-9")
def test_the_lowest_of_several_indicators_on_a_node_wins():
    case = case_of(CHAIN, [("a", [2]), ("b", [2])])
    # in either order, so that the answer is not simply whichever indicator comes last
    for ratings in ({"a": WARNING, "b": FATAL}, {"a": FATAL, "b": WARNING}):
        assert BeliefAssessor(case).assess(ratings)[2] == Belief.named(config.BALANCE_FATAL_CAP)


@pytest.mark.verifies("MGT-BAL-9")
def test_a_category_the_assessment_has_no_cap_for_is_an_error():
    case = case_of(CHAIN, [("gauge", [2])])
    with pytest.raises(CaseError, match="no cap"):
        BeliefAssessor(case).assess({"gauge": Category(9, "MYSTERY", 9)})


@pytest.mark.verifies("MGT-BAL-9")
def test_the_caps_follow_the_configuration(monkeypatch):
    monkeypatch.setattr(config, "BALANCE_WARNING_CAP", "HIGH")
    monkeypatch.setattr(config, "BALANCE_EVIDENCE_BELIEF", "VERY_HIGH")
    case = case_of(CHAIN, [("gauge", [2])])
    assert BeliefAssessor(case).assess({"gauge": WARNING})[1] == Belief.HIGH


@pytest.mark.verifies("MGT-BAL-10")
@pytest.mark.parametrize("ratings", [{}, {"gauge": None}])
def test_an_unrated_indicator_caps_at_uncertain(ratings, monkeypatch):
    monkeypatch.setattr(config, "BALANCE_EVIDENCE_BELIEF", "VERY_HIGH")
    case = case_of(CHAIN, [("gauge", [2])])
    result = BeliefAssessor(case).assess(ratings)
    assert result[1] == Belief.UNCERTAIN
    assert "unrated" in result.why(2)[0]


@pytest.mark.verifies("MGT-BAL-10")
def test_unrated_is_worse_than_ok():
    case = case_of(CHAIN, [("gauge", [2])])
    assessor = BeliefAssessor(case)
    assert assessor.assess({"gauge": None})[1] < assessor.assess({"gauge": OK})[1]


@pytest.mark.verifies("MGT-BAL-10")
def test_an_indicator_with_no_probe_or_no_value_is_unrated():
    case = case_of(CHAIN, [("gauge", [2]), ("other", [2])])
    ratings = rate_indicators(case, {"gauge": "p"}, {"p": None})
    assert ratings == {"gauge": None, "other": None}


# --- the real case -----------------------------------------------------------

REAL = JsonCaseSource(REPO_ROOT / "assurance" / "scout_case.json").load()
BINDINGS = Bindings.load(REPO_ROOT / "assurance" / "indicator_bindings.json")
BOUND = BINDINGS.resolve(REAL, PROBES).bound
HEALTHY = {"shared_cells": 0, "close_pairs": 0, "uavs_lost": 0, "lowest_fuel": 0.5, "firefighter_deficit": 0,
           "base_burning_steps": 0, "scout_coverage": 0.95, "uavs_in_service": 8}
SAFETY, PERFORMANCE = 4, 5


def assess(**changes):
    values = dict(HEALTHY, **changes)
    return BeliefAssessor(REAL).assess(rate_indicators(REAL, BOUND, values))


@pytest.mark.verifies("MGT-BAL-8")
@pytest.mark.verifies("MGT-BAL-10")
def test_the_healthy_real_case_is_limited_only_by_its_open_doubts():
    result = assess()
    assert result[SAFETY] == result[PERFORMANCE] == Belief.named(config.BALANCE_RESIDUAL_CAP)
    assert all("unrated" not in reason for reasons in result.limits.values() for reason in reasons)


@pytest.mark.verifies("MGT-BAL-9")
def test_losing_uavs_lowers_safety_and_the_depleted_fleet_lowers_performance_too():
    result = assess(uavs_lost=3, uavs_in_service=5)
    assert result[SAFETY] == Belief.named(config.BALANCE_FATAL_CAP)
    # five in service is performance's own FATAL, so performance falls too, by its own indicator ...
    assert result[PERFORMANCE] == Belief.named(config.BALANCE_FATAL_CAP)
    # ... but the losses alone, with the fleet count unchanged, leave performance where it was
    assert assess(uavs_lost=3)[PERFORMANCE] == assess()[PERFORMANCE]


@pytest.mark.verifies("MGT-BAL-9")
def test_a_fatal_fuel_level_lowers_safety_and_performance_through_the_claim_they_share():
    result = assess(lowest_fuel=0.05)
    assert result[SAFETY] == result[PERFORMANCE] == Belief.named(config.BALANCE_FATAL_CAP)


@pytest.mark.verifies("MGT-BAL-10")
def test_a_probe_with_nothing_to_say_holds_belief_at_uncertain():
    result = assess(lowest_fuel=None)
    assert result[SAFETY] <= Belief.UNCERTAIN and result[PERFORMANCE] <= Belief.UNCERTAIN


# --- balance -----------------------------------------------------------------


@pytest.mark.verifies("MGT-BAL-11")
def test_the_acceptance_table_moves_the_two_minimums_in_opposite_directions():
    judge = BalanceAssessor.for_case(BINDINGS.top_level(REAL))
    minimums = [judge.minimums(level) for level in range(4)]
    safety = [low for low, _ in minimums]
    performance = [need for _, need in minimums]
    assert safety == sorted(safety, reverse=True) and safety[0] > safety[-1]
    assert performance == sorted(performance) and performance[0] < performance[-1]


@pytest.mark.verifies("MGT-BAL-11")
def test_the_same_beliefs_can_be_acceptable_in_one_situation_and_not_in_another(monkeypatch):
    monkeypatch.setattr(config, "BALANCE_ACCEPTANCE",
                        (("HIGH", "UNCERTAIN"), ("LOW", "LOW"), ("UNCERTAIN", "HIGH"), ("SKEPTICAL", "HIGH")))
    judge = BalanceAssessor.for_case(BINDINGS.top_level(REAL))
    result = assess()   # safety LOW, performance LOW
    assert not judge.acceptable(result, 0).acceptable       # safety must be HIGH when the base is calm
    assert judge.acceptable(result, 1).acceptable
    assert not judge.acceptable(result, 3).acceptable       # performance must be HIGH when the base burns
    assert "performance is LOW" in judge.acceptable(result, 3).shortfalls()[0]


@pytest.mark.verifies("MGT-BAL-11")
def test_safety_belief_that_would_fail_a_calm_base_is_accepted_when_the_base_is_threatened(monkeypatch):
    monkeypatch.setattr(config, "BALANCE_ACCEPTANCE",
                        (("LOW", "UNCERTAIN"), ("LOW", "LOW"), ("UNCERTAIN", "LOW"), ("SKEPTICAL", "LOW")))
    judge = BalanceAssessor.for_case(BINDINGS.top_level(REAL))
    # one UAV lost is a WARNING on the losses, which here holds safety at UNCERTAIN
    monkeypatch.setattr(config, "BALANCE_WARNING_CAP", "UNCERTAIN")
    result = assess(uavs_lost=1)
    assert not judge.acceptable(result, 0).acceptable
    assert judge.acceptable(result, 2).acceptable


@pytest.mark.verifies("MGT-BAL-11")
@pytest.mark.parametrize("level", [-1, 4, 1.5, None])
def test_a_base_threat_level_outside_the_table_is_an_error(level):
    judge = BalanceAssessor.for_case(BINDINGS.top_level(REAL))
    with pytest.raises(CaseError):
        judge.minimums(level)

