"""The probes measure the fleet the way their indicators' descriptions say, and say nothing when they cannot.

A probe is what turns a FleetSnapshot into the number an indicator is rated on, so a probe that counts the
wrong thing makes the whole assurance case confident or alarmed for no reason, and nothing downstream can
tell. The cases here are the ones where that is easiest to get wrong: the home base, where any number of UAVs
may sit and which counting must not treat as a crowd; the quantities that are not defined at some steps and
must come back as None, not zero; and the coverage probe, which has to remember what was in view and forget
what was seen too long ago to count.

Snapshots are built by hand from the types in sim/managing/contract.py, so nothing here touches the
simulation.
"""

# python libraries

import json
import pathlib

import pytest

# see the note in sim/policy/random_policy.py about importing config as a module
import config

# own python modules

from sim.managing.assurance import probes
from sim.managing.assurance.probes import PROBES, measure
from sim.managing.contract import BaseReport, FleetSnapshot, UavReport
from sim.managing.knowledge import KNOWLEDGE_BASES

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# the base is a 2 x 2 footprint in the corner, so cells (0, 0) to (1, 1) are shared airspace
BASE = (0, 0), (0, 1), (1, 0), (1, 1)


def uav(uav_id, pos, **fields):
    return UavReport(uav_id=uav_id, pos=pos, alive=fields.pop("alive", True), **fields)


def snapshot(uavs, base=True, step=0, grid=(20, 20), **base_fields):
    report = BaseReport(cells=BASE, bhp=4, **base_fields) if base else None
    return FleetSnapshot(step=step, grid_size=grid, uavs=tuple(uavs), base=report)


@pytest.fixture
def knowledge():
    return KNOWLEDGE_BASES.build(None)


@pytest.fixture(autouse=True)
def shipped_settings(monkeypatch):
    # the cases below are written against these, so a run that overrides one cannot change what they mean
    monkeypatch.setattr(config, "SECURITY_DISTANCE", 6)
    monkeypatch.setattr(config, "UAV_OBSERVATION_RADIUS", 2)
    monkeypatch.setattr(config, "BASE_THREAT_RADIUS", 10)
    monkeypatch.setattr(config, "BALANCE_COVERAGE_WINDOW", 5)


# --- shared cells ------------------------------------------------------------


@pytest.mark.verifies("MGT-BAL-6")
def test_two_uavs_on_one_cell_are_both_counted_and_three_are_three(knowledge):
    assert PROBES["shared_cells"](snapshot([uav(0, (9, 9)), uav(1, (9, 9)), uav(2, (5, 5))]), knowledge) == 2
    assert PROBES["shared_cells"](snapshot([uav(0, (9, 9)), uav(1, (9, 9)), uav(2, (9, 9))]), knowledge) == 3


@pytest.mark.verifies("MGT-BAL-6")
def test_the_whole_team_sitting_on_the_base_is_not_a_crowd(knowledge):
    # they all launch from it and queue on it to refill, which is what shared airspace means
    team = [uav(index, (0, 0)) for index in range(8)]
    assert PROBES["shared_cells"](snapshot(team), knowledge) == 0


@pytest.mark.verifies("MGT-BAL-6")
def test_a_uav_that_was_lost_is_not_on_any_cell(knowledge):
    team = [uav(0, (9, 9)), uav(1, (9, 9), alive=False)]
    assert PROBES["shared_cells"](snapshot(team), knowledge) == 0


# --- close pairs -------------------------------------------------------------


@pytest.mark.verifies("MGT-BAL-6")
def test_a_pair_exactly_the_security_distance_apart_is_not_closer_than_it(knowledge):
    # the simulation's MR2 compares squared distances with <, and so does the probe
    assert PROBES["close_pairs"](snapshot([uav(0, (10, 10)), uav(1, (16, 10))]), knowledge) == 0
    assert PROBES["close_pairs"](snapshot([uav(0, (10, 10)), uav(1, (15, 10))]), knowledge) == 1


@pytest.mark.verifies("MGT-BAL-6")
def test_pairs_are_counted_once_each_and_diagonals_use_the_real_distance(knowledge):
    # three UAVs all within a few cells of each other are three pairs; (4, 4) apart is 5.66 cells, inside 6
    cluster = [uav(0, (10, 10)), uav(1, (12, 10)), uav(2, (10, 12))]
    assert PROBES["close_pairs"](snapshot(cluster), knowledge) == 3
    assert PROBES["close_pairs"](snapshot([uav(0, (10, 10)), uav(1, (14, 14))]), knowledge) == 1
    assert PROBES["close_pairs"](snapshot([uav(0, (10, 10)), uav(1, (15, 14))]), knowledge) == 0


@pytest.mark.verifies("MGT-BAL-6")
def test_uavs_on_the_base_are_left_out_of_the_close_pairs(knowledge):
    # one on the pad and one just beside it are not a hazard to each other: the pad is shared airspace
    team = [uav(0, (0, 0)), uav(1, (1, 1)), uav(2, (2, 2))]
    assert PROBES["close_pairs"](snapshot(team), knowledge) == 0


@pytest.mark.verifies("MGT-BAL-6")
def test_close_pairs_follow_the_security_distance_that_is_configured(knowledge, monkeypatch):
    team = [uav(0, (10, 10)), uav(1, (13, 10))]
    assert PROBES["close_pairs"](snapshot(team), knowledge) == 1
    monkeypatch.setattr(config, "SECURITY_DISTANCE", 3)
    assert PROBES["close_pairs"](snapshot(team), knowledge) == 0


# --- losses and service ------------------------------------------------------


@pytest.mark.verifies("MGT-BAL-6")
def test_losses_and_uavs_in_service_add_up_to_the_team_that_started(knowledge):
    team = [uav(0, (9, 9)), uav(1, (5, 5), alive=False), uav(2, (7, 7), alive=False), uav(3, (3, 3))]
    shot = snapshot(team)
    assert PROBES["uavs_lost"](shot, knowledge) == 2
    assert PROBES["uavs_in_service"](shot, knowledge) == 2


# --- fuel --------------------------------------------------------------------


@pytest.mark.verifies("MGT-BAL-6")
def test_the_lowest_fuel_is_the_lowest_share_among_the_uavs_away_from_the_base(knowledge):
    team = [uav(0, (9, 9), fuel=400, fuel_capacity=800), uav(1, (5, 5), fuel=160, fuel_capacity=800),
            uav(2, (0, 0), fuel=8, fuel_capacity=800)]  # on the base, waiting to be refilled
    assert PROBES["lowest_fuel"](snapshot(team), knowledge) == pytest.approx(0.2)


@pytest.mark.verifies("MGT-BAL-6")
def test_fuel_is_not_defined_when_it_is_not_tracked_and_not_reported_as_full(knowledge):
    # fuel_fraction() answers 1.0 for an untracked tank, which would read as a healthy fleet
    assert PROBES["lowest_fuel"](snapshot([uav(0, (9, 9)), uav(1, (5, 5))]), knowledge) is None


@pytest.mark.verifies("MGT-BAL-6")
def test_fuel_is_not_defined_when_every_uav_is_on_the_base(knowledge):
    team = [uav(0, (0, 0), fuel=10, fuel_capacity=800), uav(1, (1, 1), fuel=10, fuel_capacity=800)]
    assert PROBES["lowest_fuel"](snapshot(team), knowledge) is None


# --- the base ----------------------------------------------------------------


@pytest.mark.verifies("MGT-BAL-6")
def test_the_burning_steps_of_the_base_are_reported_and_none_without_a_base(knowledge):
    assert PROBES["base_burning_steps"](snapshot([], burning_steps=2), knowledge) == 2
    assert PROBES["base_burning_steps"](snapshot([], base=False), knowledge) is None


@pytest.mark.verifies("MGT-BAL-6")
def test_a_destroyed_base_reports_at_least_the_steps_it_was_lost_at(knowledge):
    assert PROBES["base_burning_steps"](snapshot([], destroyed=True, burning_steps=1), knowledge) == 4


# --- the firefighter deficit -------------------------------------------------


def team_of(policies, **fields):
    return [uav(index, (9 + index, 9), policy=policy, **fields) for index, policy in enumerate(policies)]


@pytest.mark.verifies("MGT-BAL-6")
def test_with_no_fire_known_nobody_is_needed_so_there_is_no_deficit(knowledge):
    shot = snapshot(team_of(["scout"] * 4 + ["firefighter"] * 4))
    assert PROBES["firefighter_deficit"](shot, knowledge) == 4  # four assigned, none needed


@pytest.mark.verifies("MGT-BAL-6")
@pytest.mark.parametrize(
    "fire, expected_needed",
    [
        ((19, 19), 2),   # far from the base: a quarter of 8
        ((6, 6), 4),     # inside the threat radius of 10: half
        ((2, 3), 8),     # within a third of the radius (3.3 cells): all of them
    ],
)
def test_the_number_of_firefighters_needed_grows_as_the_fire_nears_the_base(knowledge, fire, expected_needed):
    team = [uav(index, (9, 9), policy="scout", sees_fire=(fire,) if index == 0 else ()) for index in range(8)]
    assert PROBES["firefighter_deficit"](snapshot(team), knowledge) == 0 - expected_needed


@pytest.mark.verifies("MGT-BAL-6")
def test_the_deficit_is_negative_when_too_few_are_firefighting_and_the_paper_sign_is_kept(knowledge):
    team = [uav(index, (9, 9), policy="firefighter" if index < 3 else "scout",
                sees_fire=((6, 6),) if index == 0 else ()) for index in range(8)]
    # half of 8 are needed, three are assigned: a deficit of one, and a deficit is negative
    assert PROBES["firefighter_deficit"](snapshot(team), knowledge) == -1


@pytest.mark.verifies("MGT-BAL-6")
def test_fire_on_the_base_needs_every_uav_still_flying_and_lost_uavs_do_not_count(knowledge):
    team = team_of(["firefighter"] * 4 + ["scout"] * 2) + [uav(9, (5, 5), alive=False, policy="firefighter")]
    # the base's own sensor sees fire on its footprint, which is at distance 0
    assert PROBES["firefighter_deficit"](snapshot(team, fire_near_base=((0, 0),)), knowledge) == 4 - 6


@pytest.mark.verifies("MGT-BAL-6")
def test_damage_the_base_took_earlier_does_not_by_itself_demand_every_uav_fight(knowledge):
    # burning_steps is cumulative and never repaid; counting it as "the base is burning now" would pin the
    # whole fleet to fire fighting for the rest of the run after one bad step
    team = team_of(["firefighter"] * 4 + ["scout"] * 4)
    assert PROBES["firefighter_deficit"](snapshot(team, burning_steps=2), knowledge) == 4


@pytest.mark.verifies("MGT-BAL-6")
def test_every_policy_that_drops_water_counts_as_firefighting_and_follow_fire_does_not(knowledge):
    for name in ("firefighter", "scout-firefighter", "defend-base"):
        assert name in probes.FIREFIGHTING_POLICIES
    assert "follow-fire" not in probes.FIREFIGHTING_POLICIES and "scout" not in probes.FIREFIGHTING_POLICIES


@pytest.mark.verifies("MGT-BAL-6")
def test_the_deficit_is_not_defined_without_a_base(knowledge):
    assert PROBES["firefighter_deficit"](snapshot(team_of(["firefighter"]), base=False), knowledge) is None


# --- scout coverage ----------------------------------------------------------


def lap(knowledge, positions, steps, grid=(10, 10), **kwargs):
    """Feeds the coverage probe one snapshot per step, with the UAVs where positions(step) says."""
    value = None
    for step in range(steps):
        shot = snapshot([uav(index, pos, **kwargs) for index, pos in enumerate(positions(step))],
                        step=step, grid=grid)
        value = PROBES["scout_coverage"](shot, knowledge)
    return value


@pytest.mark.verifies("MGT-BAL-6")
def test_coverage_has_no_value_until_a_whole_window_has_been_watched(knowledge):
    # window is 5 steps in these tests: steps 0 to 4 have been watched at step 4, which is not yet a window
    assert lap(knowledge, lambda step: [(5, 5)], steps=5) is None
    assert lap(knowledge, lambda step: [(5, 5)], steps=6) is not None


@pytest.mark.verifies("MGT-BAL-6")
def test_coverage_is_the_share_of_the_grid_inside_a_square_window_around_each_uav(knowledge):
    # radius 2 around (5, 5) is a 5 x 5 square of a 10 x 10 grid
    assert lap(knowledge, lambda step: [(5, 5)], steps=6) == pytest.approx(25 / 100)


@pytest.mark.verifies("MGT-BAL-6")
def test_a_window_at_the_edge_of_the_grid_is_clipped_to_it(knowledge):
    # radius 2 around the corner (0, 0) is a 3 x 3 square, not a 5 x 5 one with cells off the grid
    assert lap(knowledge, lambda step: [(0, 0)], steps=6) == pytest.approx(9 / 100)


@pytest.mark.verifies("MGT-BAL-6")
def test_a_cell_stops_counting_once_it_was_last_seen_longer_ago_than_the_window(knowledge):
    # the UAV watches the left side for the first steps and the right side for the last ones: cells seen on
    # the left only at the start have aged out of a 5 step window by the end
    positions = lambda step: [(2, 5)] if step < 3 else [(7, 5)]
    assert lap(knowledge, positions, steps=9) == pytest.approx(25 / 100)


@pytest.mark.verifies("MGT-BAL-6")
def test_cells_seen_by_different_uavs_in_the_window_are_all_counted_once(knowledge):
    value = lap(knowledge, lambda step: [(2, 2), (7, 7)], steps=6)
    assert value == pytest.approx(50 / 100)
    overlapping = lap(KNOWLEDGE_BASES.build(None), lambda step: [(5, 5), (5, 6)], steps=6)
    assert overlapping == pytest.approx(30 / 100)  # 5 x 5 plus one more row of 5


@pytest.mark.verifies("MGT-BAL-6")
def test_cells_the_smoke_hid_from_a_uav_are_not_counted_as_seen(knowledge):
    hidden = tuple((x, y) for x in range(3, 8) for y in range(3, 4))   # one row of the window, 5 cells
    assert lap(knowledge, lambda step: [(5, 5)], steps=6, sees_occluded=hidden) == pytest.approx(20 / 100)


@pytest.mark.verifies("MGT-BAL-6")
def test_a_lost_uav_sees_nothing(knowledge):
    assert lap(knowledge, lambda step: [(5, 5)], steps=6, alive=False) == pytest.approx(0.0)


@pytest.mark.verifies("MGT-BAL-6")
def test_taking_the_same_snapshot_twice_changes_nothing(knowledge):
    # options are assessed against the same snapshot more than once, so reading it has to be repeatable
    shots = [snapshot([uav(0, (5, 5))], step=step) for step in range(7)]
    once = [PROBES["scout_coverage"](shot, knowledge) for shot in shots]
    again = [PROBES["scout_coverage"](shot, knowledge) for shot in shots]
    assert once[-1] == again[-1]
    assert PROBES["scout_coverage"](shots[-1], knowledge) == once[-1]


@pytest.mark.verifies("MGT-BAL-6")
def test_the_coverage_window_is_the_configured_one(knowledge, monkeypatch):
    monkeypatch.setattr(config, "BALANCE_COVERAGE_WINDOW", 8)
    assert lap(knowledge, lambda step: [(5, 5)], steps=8) is None
    assert lap(KNOWLEDGE_BASES.build(None), lambda step: [(5, 5)], steps=9) is not None


# --- reading them all --------------------------------------------------------


@pytest.mark.verifies("MGT-BAL-6")
def test_measure_reads_every_probe_by_name_and_any_subset_on_request(knowledge):
    shot = snapshot([uav(0, (9, 9)), uav(1, (5, 5))])
    everything = measure(shot, knowledge)
    assert set(everything) == set(PROBES)
    assert measure(shot, knowledge, names=["uavs_lost"]) == {"uavs_lost": 0}


@pytest.mark.verifies("MGT-BAL-6")
def test_every_probe_the_bindings_file_names_exists_and_every_probe_is_bound_to_an_indicator():
    bindings = json.loads((REPO_ROOT / "assurance" / "indicator_bindings.json").read_text(encoding="utf-8"))
    named = set(bindings["probes"].values())
    assert named == set(PROBES), "the bindings file and the probes registry have drifted apart"


@pytest.mark.verifies("MGT-BAL-6")
def test_a_probe_does_not_change_the_snapshot_it_reads(knowledge):
    shot = snapshot([uav(0, (9, 9), fuel=100, fuel_capacity=800), uav(1, (5, 5))])
    before = shot.uavs, shot.base, shot.step
    measure(shot, knowledge)
    assert (shot.uavs, shot.base, shot.step) == before
