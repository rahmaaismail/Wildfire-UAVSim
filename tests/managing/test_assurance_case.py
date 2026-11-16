"""The assurance case is read correctly, and refused when it cannot be trusted.

The BALANCE method reasons over a Socrates export, and an export names its nodes by two different numbers:
a node's 'parents' are internal ids while its 'children' and an indicator's 'nodes' are lids, the numbers
shown next to a node in the tree. A reader that takes one for the other links every node to the wrong
neighbour and raises nothing, so most of what is here is about that, and about the other ways a case can be
wrong that would otherwise make the managing system confident for no reason.

Two kinds of case are used. The real export committed under assurance/ shows the loader copes with what
Socrates actually writes; a few lines of hand built JSON show it refusing what Socrates should not.
"""

# python libraries

import copy
import json
import pathlib

import pytest

# own python modules

from sim.managing.assurance import CaseError, JsonCaseSource

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
REAL_CASE = REPO_ROOT / "assurance" / "scout_case.json"


# a case small enough to read at a glance, in which every node's id differs from its lid so that taking the
# one for the other cannot pass by coincidence. Node 1 (id 101) is a claim with one child, node 2 (id 102).
def small_case():
    return {
        "schema": {"version": 14},
        "name": "small",
        "indicatorRuleCategories": [
            {"id": 1, "name": "OK", "priority": 1, "color": "00FF00"},
            {"id": 2, "name": "WARNING", "priority": 2, "color": "FFFF00"},
            {"id": 3, "name": "FATAL", "priority": 3, "color": "FF0000"},
        ],
        "nodes": [
            {"lid": 1, "id": 101, "nodeType": "CLAIM", "text": "the claim", "children": [2], "parents": [],
             "isRoot": True},
            {"lid": 2, "id": 102, "nodeType": "EVIDENCE", "text": "the evidence", "children": [],
             "parents": [101], "isRoot": False},
        ],
        "indicators": [
            {"id": 7, "name": "things", "nodes": [2], "archived": False, "creator": "someone",
             "rules": [{"name": "Some", "combinator": "OR", "category": 2,
                        "conditions": [{"comparator": "GTE", "value": 1.0}]}],
             "indicatorData": [{"created": "2026-11-02T22:53:59.000+0000", "value": 3.0}]},
        ],
    }


def broken(change):
    payload = copy.deepcopy(small_case())
    change(payload)
    return payload


# --- reading a case ----------------------------------------------------------


@pytest.mark.verifies("MGT-BAL-1")
def test_a_nodes_parents_are_resolved_from_ids_and_its_children_from_lids():
    case = JsonCaseSource.parse(small_case())
    # 101 is the id of node 1: if 'parents' were read as lids this would be a node that does not exist
    assert [node.lid for node in case.parents(2)] == [1]
    assert [node.lid for node in case.children(1)] == [2]


@pytest.mark.verifies("MGT-BAL-1")
def test_an_indicators_nodes_are_lids():
    case = JsonCaseSource.parse(small_case())
    assert [indicator.name for indicator in case.indicators_on(2)] == ["things"]
    assert case.indicators_on(1) == ()
    assert case.indicators_on(102) == (), "102 is the id of node 2, not its lid"


@pytest.mark.verifies("MGT-BAL-1")
def test_every_part_of_an_indicator_survives_the_trip_through_the_file():
    indicator = JsonCaseSource.parse(small_case()).indicator("things")
    assert indicator.id == 7 and indicator.nodes == (2,)
    assert [(rule.name, rule.category.name, rule.combinator) for rule in indicator.rules] == \
        [("Some", "WARNING", "OR")]
    assert [point.value for point in indicator.data] == [3.0]


@pytest.mark.verifies("MGT-BAL-1")
def test_the_real_export_loads_with_every_link_pointing_at_a_node_that_exists():
    case = JsonCaseSource(REAL_CASE).load()
    raw = json.loads(REAL_CASE.read_text(encoding="utf-8"))

    assert len(case.nodes) == len(raw["nodes"])
    assert {category.name for category in case.categories.values()} == {"OK", "WARNING", "FATAL"}
    for node in case.nodes.values():
        for child in node.children:
            assert child in case.nodes
            assert node.lid in [parent for parent in case.nodes[child].parents]
        for parent in node.parents:
            assert node.lid in case.nodes[parent].children


@pytest.mark.verifies("MGT-BAL-1")
def test_the_real_export_has_the_shared_fuel_claim_under_both_of_its_parents():
    # C0012 hangs under the safety argument's strategy (S0006) and under the fleet-in-service claim (C0044),
    # which is the point of a network rather than a tree. Read with the keys mixed up it has other parents.
    case = JsonCaseSource(REAL_CASE).load()
    assert sorted(node.lid for node in case.parents(12)) == [6, 44]


@pytest.mark.verifies("MGT-BAL-1")
def test_the_real_export_carries_its_indicators_on_the_claims_they_were_attached_to():
    case = JsonCaseSource(REAL_CASE).load()
    attached = {indicator.name: indicator.nodes for indicator in case.indicators}
    assert attached["Num UAVs Sharing Cell"] == (98,)
    assert attached["Lowest fuel fraction"] == (12,)
    assert attached["Base burning steps (of 4)"] == (5,)


# --- refusing a case ---------------------------------------------------------


@pytest.mark.verifies("MGT-BAL-2")
def test_a_file_that_is_not_json_is_refused(tmp_path):
    path = tmp_path / "case.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(CaseError, match="not valid JSON"):
        JsonCaseSource(path).load()


@pytest.mark.verifies("MGT-BAL-2")
def test_a_file_that_is_not_there_is_refused(tmp_path):
    with pytest.raises(CaseError, match="cannot read"):
        JsonCaseSource(tmp_path / "missing.json").load()


@pytest.mark.verifies("MGT-BAL-2")
def test_a_schema_version_the_reader_was_not_written_for_is_refused():
    with pytest.raises(CaseError, match="schema version 15"):
        JsonCaseSource.parse(broken(lambda payload: payload["schema"].update(version=15)))


@pytest.mark.verifies("MGT-BAL-2")
@pytest.mark.parametrize(
    "change, message",
    [
        (lambda p: p["nodes"][0].update(children=[2, 999]), "child 999"),
        (lambda p: p["nodes"][1].update(parents=[101, 5555]), "parent 5555"),
        (lambda p: p["nodes"][1].update(parents=[1]), "parent 1"),            # a lid where an id belongs
        (lambda p: p["nodes"][0].update(children=[102]), "child 102"),        # an id where a lid belongs
        (lambda p: p["indicators"][0].update(nodes=[102]), "node 102"),
        (lambda p: p["indicators"][0]["rules"][0].update(category=9), "category 9"),
        (lambda p: p["nodes"][1].update(lid=1), "share the lid"),
        (lambda p: p["nodes"][0].update(children=[]), "does not list it as a child"),
        (lambda p: p["nodes"][1].update(parents=[]), "does not name it as a parent"),
    ],
)
def test_a_link_to_something_that_is_not_there_is_refused(change, message):
    with pytest.raises(CaseError, match=message):
        JsonCaseSource.parse(broken(change))


@pytest.mark.verifies("MGT-BAL-2")
def test_a_comparator_or_combinator_nobody_defined_is_refused_rather_than_never_matching():
    with pytest.raises(CaseError, match="comparator 'NEAR'"):
        JsonCaseSource.parse(broken(
            lambda p: p["indicators"][0]["rules"][0]["conditions"][0].update(comparator="NEAR")))
    with pytest.raises(CaseError, match="combinator 'XOR'"):
        JsonCaseSource.parse(broken(lambda p: p["indicators"][0]["rules"][0].update(combinator="XOR")))


@pytest.mark.verifies("MGT-BAL-2")
@pytest.mark.parametrize("key", ["schema", "nodes", "indicators", "indicatorRuleCategories"])
def test_an_export_missing_a_whole_section_is_refused(key):
    payload = small_case()
    del payload[key]
    with pytest.raises(CaseError, match=key):
        JsonCaseSource.parse(payload)
