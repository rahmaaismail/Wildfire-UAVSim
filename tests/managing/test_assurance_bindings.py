"""Indicators are bound to probes by name, and the claims to balance are the ones the bindings name.

A Socrates export cannot say which code measures an indicator (its 'path' field is empty on every indicator
of this case), nor which two claims the fleet is balancing (its 'isRoot' flag is true of five nodes here,
because it also marks the roots of sub-arguments that are shared). Both are said in a file in the repository
instead, and this checks that file against the case and against the probes, so that a misspelt name is found
when the managing system is built and not on the step it is first needed.
"""

# python libraries

import json
import pathlib

import pytest

# own python modules

from sim.managing.assurance import Bindings, CaseError, JsonCaseSource

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
REAL_CASE = REPO_ROOT / "assurance" / "scout_case.json"
REAL_BINDINGS = REPO_ROOT / "assurance" / "indicator_bindings.json"

# the probes the bindings file names. The probes themselves come later (MGT-BAL-6); resolving needs only
# their names.
PROBES = ["shared_cells", "close_pairs", "uavs_lost", "lowest_fuel", "firefighter_deficit",
          "base_burning_steps", "scout_coverage", "uavs_in_service"]


class RecordingLog:
    def __init__(self):
        self.warnings = []

    def warning(self, message, *arguments):
        self.warnings.append(message % arguments)


@pytest.fixture(scope="module")
def case():
    return JsonCaseSource(REAL_CASE).load()


# --- binding indicators to probes --------------------------------------------


@pytest.mark.verifies("MGT-BAL-5")
def test_every_indicator_of_the_real_case_is_bound_to_a_probe_that_exists(case):
    resolution = Bindings.load(REAL_BINDINGS).resolve(case, PROBES)
    assert resolution.unbound == ()
    assert set(resolution.bound) == {indicator.name for indicator in case.indicators}


@pytest.mark.verifies("MGT-BAL-5")
def test_a_binding_to_a_probe_that_does_not_exist_is_refused_and_names_it(case):
    bindings = Bindings({"Lowest fuel fraction": "lowest_fuell"}, {})
    with pytest.raises(CaseError, match="lowest_fuell"):
        bindings.resolve(case, PROBES)


@pytest.mark.verifies("MGT-BAL-5")
def test_a_binding_for_an_indicator_the_case_does_not_have_is_refused_and_names_it(case):
    # almost always a misspelt name, and the real indicator would otherwise be reported unbound with nothing
    # to say why
    bindings = Bindings({"Lowest fuel fractoin": "lowest_fuel"}, {})
    with pytest.raises(CaseError, match="Lowest fuel fractoin"):
        bindings.resolve(case, PROBES)


@pytest.mark.verifies("MGT-BAL-5")
def test_an_indicator_with_no_binding_is_left_unbound_and_reported_once_each(case):
    log = RecordingLog()
    resolution = Bindings({"UAVs lost": "uavs_lost"}, {}).resolve(case, PROBES, log=log)

    assert "UAVs lost" not in resolution.unbound
    assert len(resolution.unbound) == len(case.indicators) - 1
    assert len(log.warnings) == len(resolution.unbound)
    assert all("will stay unrated" in warning for warning in log.warnings)
    assert sum("Scout coverage" in warning for warning in log.warnings) == 1


@pytest.mark.verifies("MGT-BAL-5")
def test_resolving_with_no_log_is_quiet_and_still_reports_what_is_unbound(case):
    assert Bindings({}, {}).resolve(case, PROBES).unbound == tuple(i.name for i in case.indicators)


@pytest.mark.verifies("MGT-BAL-5")
@pytest.mark.parametrize("payload", [[], {"probes": []}, {"probes": {"x": 1}}, {"top_level": {"safety": "4"}}])
def test_a_bindings_file_of_the_wrong_shape_is_refused(payload):
    with pytest.raises(CaseError):
        Bindings.parse(payload)


@pytest.mark.verifies("MGT-BAL-5")
def test_a_bindings_file_that_is_not_json_or_not_there_is_refused(tmp_path):
    path = tmp_path / "bindings.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(CaseError, match="not valid JSON"):
        Bindings.load(path)
    with pytest.raises(CaseError, match="cannot read"):
        Bindings.load(tmp_path / "missing.json")


# --- the claims to balance ---------------------------------------------------


@pytest.mark.verifies("MGT-BAL-7")
def test_the_top_level_claims_are_the_ones_the_bindings_name(case):
    claims = Bindings.load(REAL_BINDINGS).top_level(case)
    assert set(claims) == {"safety", "performance"}
    assert claims["safety"].lid == 4 and claims["safety"].text.startswith("Safety")
    assert claims["performance"].lid == 5 and claims["performance"].text.startswith("Performance")


@pytest.mark.verifies("MGT-BAL-7")
def test_the_roots_flag_of_the_export_would_have_picked_the_wrong_claims(case):
    # the reason the claims are named at all: five nodes are flagged as roots, not two
    roots = [node.lid for node in case.nodes.values() if node.is_root]
    assert len(roots) > 2, "if this stops being true the bindings may not be needed"
    assert {4, 5} <= set(roots)
    assert {7, 12} & set(roots), "the roots of the shared collision and fuel sub-arguments are flagged too"


@pytest.mark.verifies("MGT-BAL-7")
def test_a_claim_that_is_not_in_the_case_is_refused_and_named(case):
    with pytest.raises(CaseError, match="node 9999"):
        Bindings({}, {"safety": 4, "performance": 9999}).top_level(case)


@pytest.mark.verifies("MGT-BAL-7")
def test_the_bindings_file_in_the_repository_is_valid_json_with_both_sections():
    payload = json.loads(REAL_BINDINGS.read_text(encoding="utf-8"))
    assert set(payload) == {"top_level", "probes"}
