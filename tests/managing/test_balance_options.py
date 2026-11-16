"""Options, the analyser that weighs them, and the planner that carries the winner out.

The scenarios are small and built by hand, on a three-indicator case of their own, so that the arithmetic of
each decision can be followed on the page: the shipped case has too many open doubts for one UAV's separation
to move its beliefs, and a test that cannot show why an option won is not testing the choice.

The case: safety rests on evidence with the cushion indicator (pairs closer than SECURITY_DISTANCE) and the
guarantee indicator (UAVs sharing a cell); performance rests on evidence with the firefighter deficit. The
settings that decide what is acceptable are set in the fixture, so that what these tests mean does not move
when the shipped guesses do.
"""

# python libraries

import pytest

# see the note in sim/policy/random_policy.py about importing config as a module
import config

# own python modules

from sim.managing.analyze.balance import BalanceAnalyzer
from sim.managing.assurance import Bindings, Case, JsonCaseSource
from sim.managing.assurance.options import (KeepCurrent, OptionGenerator, RelaxClearance, RestoreClearance,
                                            cushion)
from sim.managing.contract import BaseReport, FleetSnapshot, UavReport
from sim.managing.knowledge import KNOWLEDGE_BASES
from sim.managing.plan.balance import BalancePlanner

BASE = (0, 0), (0, 1), (1, 0), (1, 1)

CATEGORIES = [{"id": 1, "name": "OK", "priority": 1}, {"id": 2, "name": "WARNING", "priority": 2},
              {"id": 3, "name": "FATAL", "priority": 3}]


def rule(name, category, *conditions, combinator="AND"):
    return {"name": name, "category": category, "combinator": combinator,
            "conditions": [{"comparator": c, "value": v} for c, v in conditions]}


PAYLOAD = {
    "schema": {"version": 14}, "name": "small", "indicatorRuleCategories": CATEGORIES,
    "nodes": [
        {"lid": 1, "id": 101, "nodeType": "CLAIM", "text": "safety", "children": [3], "parents": []},
        {"lid": 2, "id": 102, "nodeType": "CLAIM", "text": "performance", "children": [4], "parents": []},
        {"lid": 3, "id": 103, "nodeType": "EVIDENCE", "text": "s", "children": [], "parents": [101]},
        {"lid": 4, "id": 104, "nodeType": "EVIDENCE", "text": "p", "children": [], "parents": [102]},
    ],
    "indicators": [
        {"id": 1, "name": "Cushion", "nodes": [3], "rules": [
            rule("ok", 1, ("LTE", 2)), rule("eroding", 2, ("GT", 2), ("LT", 6)), rule("lost", 3, ("GTE", 6))]},
        {"id": 2, "name": "Guarantee", "nodes": [3], "rules": [
            rule("ok", 1, ("EQ", 0)), rule("shared", 2, ("GT", 0), ("LT", 3)), rule("crowded", 3, ("GTE", 3))]},
        {"id": 3, "name": "Deficit", "nodes": [4], "rules": [
            rule("ok", 1, ("GTE", 0)), rule("short", 2, ("LT", 0), ("GTE", -2)), rule("severe", 3, ("LT", -2))]},
    ],
}
BINDINGS = {"top_level": {"safety": 1, "performance": 2},
            "probes": {"Cushion": "close_pairs", "Guarantee": "shared_cells", "Deficit": "firefighter_deficit"}}


class Source:
    def __init__(self, case):
        self.case = case

    def load(self):
        return self.case


def analyzer():
    return BalanceAnalyzer(source=Source(JsonCaseSource.parse(PAYLOAD)), bindings=Bindings.parse(BINDINGS))


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    for name, value in (("SECURITY_DISTANCE", 6), ("BASE_THREAT_RADIUS", 10), ("BALANCE_RELAX_THREAT", 1),
                        ("BALANCE_RELAX_GAIN", 1), ("BALANCE_EVIDENCE_BELIEF", "VERY_HIGH"),
                        ("BALANCE_RESIDUAL_CAP", "VERY_HIGH"), ("BALANCE_WARNING_CAP", "LOW"),
                        ("BALANCE_FATAL_CAP", "SKEPTICAL"),
                        ("BALANCE_ACCEPTANCE", (("LOW", "LOW"), ("LOW", "HIGH"), ("SKEPTICAL", "HIGH"),
                                                ("SKEPTICAL", "HIGH")))):
        monkeypatch.setattr(config, name, value)


def uav(uav_id, pos, policy="firefighter", separation=cushion(), **fields):
    params = {} if separation is None else {"separation": separation}
    return UavReport(uav_id=uav_id, pos=pos, policy=policy, params=params, hp=3, **fields)


def fleet(uavs, fire=((3, 3),)):
    return FleetSnapshot(step=1, grid_size=(20, 20), uavs=tuple(uavs),
                         base=BaseReport(cells=BASE, bhp=4, fire_near_base=fire))


# two firefighters and two scouts around a base with a fire at its door. Everyone holds the cushion, so
# the firefighter deficit is two short, which is performance in WARNING, which is not acceptable here
def threatened():
    return fleet([uav(1, (4, 4)), uav(2, (4, 7)), uav(3, (7, 4), policy="scout"), uav(4, (7, 7), policy="scout")])


# --- the options -------------------------------------------------------------


@pytest.mark.verifies("MGT-BAL-12")
def test_keeping_is_always_offered_first_and_relaxing_only_to_those_holding_a_cushion():
    options = OptionGenerator().generate(threatened(), threat=2, acceptable=True)
    assert isinstance(options[0], KeepCurrent)
    relax = [option for option in options if isinstance(option, RelaxClearance)]
    assert len(relax) == 1 and relax[0].uav_ids == (1, 2)       # the scouts are not fire fighters


@pytest.mark.verifies("MGT-BAL-12")
def test_relaxing_is_not_offered_to_a_uav_that_is_already_relaxed_or_far_from_the_base():
    snapshot = fleet([uav(1, (4, 4), separation=0), uav(2, (18, 18))])
    options = OptionGenerator().generate(snapshot, threat=2, acceptable=True)
    assert not [option for option in options if isinstance(option, RelaxClearance)]


@pytest.mark.verifies("MGT-BAL-12")
def test_relaxing_is_offered_below_the_threat_level_only_when_the_balance_has_been_lost():
    generator = OptionGenerator()
    snapshot = threatened()
    assert not [o for o in generator.generate(snapshot, 0, acceptable=True) if isinstance(o, RelaxClearance)]
    assert [o for o in generator.generate(snapshot, 0, acceptable=False) if isinstance(o, RelaxClearance)]


@pytest.mark.verifies("MGT-BAL-12")
def test_restoring_is_offered_only_below_the_relax_threat_and_only_for_uavs_without_a_cushion():
    generator = OptionGenerator()
    snapshot = fleet([uav(1, (4, 4), separation=0), uav(2, (4, 7))])
    calm = [o for o in generator.generate(snapshot, 0, acceptable=True) if isinstance(o, RestoreClearance)]
    assert len(calm) == 1 and calm[0].uav_ids == (1,)
    assert not [o for o in generator.generate(snapshot, 2, acceptable=True) if isinstance(o, RestoreClearance)]


@pytest.mark.verifies("MGT-BAL-14")
def test_relaxing_sets_separation_zero_for_the_named_uavs_only_and_keeps_their_other_settings():
    snapshot = fleet([UavReport(uav_id=1, pos=(4, 4), policy="firefighter", hp=3,
                                params={"separation": 6, "speed_cap": 1}),
                      uav(2, (4, 7))])
    directives = RelaxClearance(uav_ids=(1,)).directives(snapshot)
    assert [(d.uav_id, d.policy, d.params) for d in directives] == [
        (1, "firefighter", {"separation": 0, "speed_cap": 1})]


@pytest.mark.verifies("MGT-BAL-14")
def test_restoring_sets_the_cushion_the_safety_argument_was_made_for_and_keeping_sets_nothing(monkeypatch):
    monkeypatch.setattr(config, "SECURITY_DISTANCE", 5.5)
    snapshot = fleet([uav(1, (4, 4), separation=0)])
    assert RestoreClearance(uav_ids=(1,)).directives(snapshot)[0].params["separation"] == 6
    assert KeepCurrent(uav_ids=(1,)).directives(snapshot) == ()


@pytest.mark.verifies("MGT-BAL-14")
def test_relaxing_gives_up_the_cushion_indicator_and_never_the_shared_cell_one():
    case = JsonCaseSource.parse(PAYLOAD)
    bound = Bindings.parse(BINDINGS).probes
    adapted = RelaxClearance(uav_ids=(1,)).adapted_case(case, bound)
    assert {i.name: i.archived for i in adapted.indicators} == {"Cushion": True, "Guarantee": False,
                                                                "Deficit": False}


@pytest.mark.verifies("MGT-BAL-13")
def test_an_option_leaves_the_original_case_and_values_untouched():
    case = JsonCaseSource.parse(PAYLOAD)
    bound = Bindings.parse(BINDINGS).probes
    values = {"firefighter_deficit": -2}
    relax = RelaxClearance(uav_ids=(1, 2))
    relax.adapted_case(case, bound)
    adapted = relax.adapted_values(values)
    assert not any(i.archived for i in case.indicators)
    assert values == {"firefighter_deficit": -2} and adapted == {"firefighter_deficit": 0}


@pytest.mark.verifies("MGT-BAL-13")
def test_relaxing_is_never_credited_with_a_surplus_and_restoring_costs_what_relaxing_gained():
    relax, restore = RelaxClearance(uav_ids=(1, 2, 3)), RestoreClearance(uav_ids=(1, 2))
    assert relax.adapted_values({"firefighter_deficit": -1})["firefighter_deficit"] == 0
    assert relax.adapted_values({"firefighter_deficit": 2})["firefighter_deficit"] == 2
    assert restore.adapted_values({"firefighter_deficit": 1})["firefighter_deficit"] == -1
    assert relax.adapted_values({"firefighter_deficit": None}) == {"firefighter_deficit": None}


# --- the analyser ------------------------------------------------------------


def decide(snapshot, balance=None):
    balance = balance or analyzer()
    knowledge = KNOWLEDGE_BASES.build(None, hysteresis=2)
    knowledge.record(snapshot)
    symptoms = balance.analyze(snapshot, knowledge)
    return symptoms, knowledge


@pytest.mark.verifies("MGT-BAL-13")
def test_the_option_that_restores_the_balance_is_chosen_and_the_one_that_does_not_is_discarded():
    symptoms, knowledge = decide(threatened())
    memory = knowledge.memory["balance"]
    assert isinstance(memory["option"], RelaxClearance)
    assert symptoms.requested and symptoms.requires_adaptation()
    # the rationale says what won, on what beliefs, and what lost and why
    assert "relax clearance" in memory["rationale"]
    assert "discarded: keep" in memory["rationale"] and "performance is LOW" in memory["rationale"]


@pytest.mark.verifies("MGT-BAL-13")
def test_the_choice_is_made_on_belief_not_on_how_many_uavs_an_option_moves(monkeypatch):
    # with no gain from relaxing, relaxing cannot make performance acceptable, so nothing is changed
    monkeypatch.setattr(config, "BALANCE_RELAX_GAIN", 0)
    symptoms, knowledge = decide(threatened())
    memory = knowledge.memory["balance"]
    assert isinstance(memory["option"], KeepCurrent) and not symptoms.requested
    assert "no option keeps the balance acceptable" in memory["rationale"]


@pytest.mark.verifies("MGT-BAL-13")
def test_among_acceptable_options_the_best_performance_wins(monkeypatch):
    # keep is acceptable on safety alone, and relaxing has the better performance, so relaxing wins
    monkeypatch.setattr(config, "BALANCE_ACCEPTANCE", (("LOW", "LOW"),) * 4)
    symptoms, knowledge = decide(threatened())
    assert isinstance(knowledge.memory["balance"]["option"], RelaxClearance)


@pytest.mark.verifies("MGT-BAL-13")
def test_a_tie_goes_to_restoring_clearance_and_then_to_changing_least():
    # nothing burns, nobody holds a cushion: keeping and restoring are equally believed, and restoring wins
    snapshot = fleet([uav(1, (4, 4), separation=0), uav(2, (4, 7), separation=0)], fire=())
    symptoms, knowledge = decide(snapshot)
    assert isinstance(knowledge.memory["balance"]["option"], RestoreClearance)
    assert symptoms.requested


@pytest.mark.verifies("MGT-BAL-14")
def test_crowding_beside_a_deliberately_relaxed_base_is_not_a_symptom():
    crowded = fleet([uav(1, (4, 4), sees_uavs=((4, 5),)), uav(2, (4, 5), sees_uavs=((4, 4),)),
                     uav(3, (7, 4), policy="scout"), uav(4, (7, 7), policy="scout")])
    heuristic = analyzer().heuristic.analyze(crowded)
    assert heuristic.crowding
    symptoms, _ = decide(crowded)
    assert not symptoms.crowding


@pytest.mark.verifies("MGT-BAL-17")
def test_an_unreadable_case_falls_back_to_the_heuristic_analysis_and_says_so(monkeypatch):
    monkeypatch.setattr(config, "BALANCE_CASE_FILE", "assurance/does_not_exist.json")
    balance = BalanceAnalyzer()
    assert balance.failure and "does_not_exist" in balance.failure
    snapshot = threatened()
    symptoms, knowledge = decide(snapshot, balance)
    expected = balance.heuristic.analyze(snapshot)
    assert (symptoms.base_threat, symptoms.requested) == (expected.base_threat, False)
    assert "heuristic analysis only" in knowledge.memory["balance"]["rationale"]


@pytest.mark.verifies("MGT-BAL-17")
def test_the_shipped_case_and_bindings_load_and_the_analyser_runs_on_them():
    balance = BalanceAnalyzer()
    assert balance.failure is None and balance.case is not None
    _, knowledge = decide(threatened(), balance)
    assert knowledge.memory["balance"]["rationale"].startswith("balance at base threat")


@pytest.mark.verifies("MGT-BAL-18")
def test_every_decision_carries_a_rationale_through_to_the_allocation():
    snapshot = threatened()
    symptoms, knowledge = decide(snapshot)
    knowledge.hysteresis = 1
    allocation = BalancePlanner().plan(snapshot, symptoms, knowledge)
    assert "balance at base threat" in allocation.rationale


# --- the planner -------------------------------------------------------------


@pytest.mark.verifies("MGT-BAL-15")
def test_a_change_of_separation_is_applied_only_after_it_has_held_for_the_hysteresis_count():
    snapshot = threatened()
    balance, planner = analyzer(), BalancePlanner()
    knowledge = KNOWLEDGE_BASES.build(None, hysteresis=2)
    separations = []
    for _ in range(3):
        knowledge.record(snapshot)
        symptoms = balance.analyze(snapshot, knowledge)
        allocation = planner.plan(snapshot, symptoms, knowledge)
        separations.append({d.uav_id: d.params.get("separation") for d in allocation.directives})
    # the first evaluation changes no UAV's separation, the second does, for the two the option names
    assert all(value == cushion() for value in separations[0].values())
    assert separations[1][1] == separations[1][2] == 0


@pytest.mark.verifies("MGT-BAL-15")
def test_an_option_that_stops_being_chosen_loses_its_streak():
    snapshot = threatened()
    balance, planner = analyzer(), BalancePlanner()
    knowledge = KNOWLEDGE_BASES.build(None, hysteresis=2)
    knowledge.record(snapshot)
    planner.plan(snapshot, balance.analyze(snapshot, knowledge), knowledge)     # streak of one for relaxing
    # spread wider than SECURITY_DISTANCE, so that the cushion indicator is fine and nothing needs to change
    quiet = fleet([uav(1, (4, 4)), uav(2, (11, 4)), uav(3, (4, 11)), uav(4, (11, 11))], fire=())
    knowledge.record(quiet)
    balance.analyze(quiet, knowledge)                                           # nothing to change
    knowledge.record(snapshot)
    allocation = planner.plan(snapshot, balance.analyze(snapshot, knowledge), knowledge)
    assert all(d.params.get("separation") == cushion() for d in allocation.directives)


@pytest.mark.verifies("MGT-BAL-15")
def test_a_step_that_only_changes_separation_does_not_reissue_policies_to_the_whole_team():
    snapshot = fleet([uav(1, (4, 4), separation=0), uav(2, (4, 7), separation=0, policy="scout-firefighter")],
                     fire=())
    balance, planner = analyzer(), BalancePlanner()
    knowledge = KNOWLEDGE_BASES.build(None, hysteresis=1)
    knowledge.record(snapshot)
    symptoms = balance.analyze(snapshot, knowledge)
    allocation = planner.plan(snapshot, symptoms, knowledge)
    assert {d.uav_id: (d.policy, d.params["separation"]) for d in allocation.directives} == {
        1: ("firefighter", cushion()), 2: ("scout-firefighter", cushion())}


@pytest.mark.verifies("MGT-BAL-15")
def test_reallocating_the_team_keeps_every_uavs_separation_rather_than_resetting_it():
    snapshot = fleet([uav(1, (4, 4), separation=0), uav(2, (4, 7))])
    planner = BalancePlanner()
    knowledge = KNOWLEDGE_BASES.build(None, hysteresis=1)
    knowledge.record(snapshot)
    symptoms = analyzer().heuristic.analyze(snapshot, knowledge)
    allocation = planner.plan(snapshot, symptoms, knowledge)
    assert allocation.directives
    assert {d.uav_id: d.params["separation"] for d in allocation.directives} == {1: 0, 2: cushion()}


@pytest.mark.verifies("MGT-BAL-15")
def test_a_separation_the_heuristic_planner_asked_for_is_not_overridden_by_the_option():
    from sim.managing.contract import Symptoms
    snapshot = fleet([uav(1, (4, 4)), uav(2, (4, 7))])
    knowledge = KNOWLEDGE_BASES.build(None, hysteresis=1)
    knowledge.record(snapshot)
    knowledge.memory["balance"] = {"option": RelaxClearance(uav_ids=(1,)), "rationale": "test"}
    allocation = BalancePlanner().plan(snapshot, Symptoms(crowding={1: 1}, requested=True), knowledge)
    directive = allocation.for_uav(1)
    # the UAV is told to open a gap, and keeps the room it was asked to make
    assert directive.policy == "disperse" and directive.params["separation"] == cushion()


@pytest.mark.verifies("MGT-BAL-16")
def test_nothing_in_the_repository_imports_the_socrates_api():
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parents[2]
    importing = re.compile(r"^\s*(?:import|from)\s+socrates", re.IGNORECASE | re.MULTILINE)
    offenders = [str(path.relative_to(root)) for path in root.rglob("*.py")
                 if ".git" not in path.parts and importing.search(path.read_text(encoding="utf-8"))]
    assert offenders == []


@pytest.mark.verifies("MGT-BAL-16")
def test_the_balance_system_is_constructible_from_a_json_case_alone():
    from sim.managing.systems import REGISTERED
    assert "balance" in {spec.name for spec in REGISTERED}
    assert BalanceAnalyzer().failure is None
