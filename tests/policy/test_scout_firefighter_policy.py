"""Tests for ScoutFirefighterPolicy, the team policy that scouts until a fire is seen and then fights it.

These are pure policy tests: they build Observations directly and never touch the grid or mesa. The policy is
two policies joined by a rule about which of them a UAV is flying, so most of what is tested here is the joint:
that a UAV that has nothing to fight does exactly what ScoutPolicy would have told it, that one that has a fire
to fight does what FirefighterPolicy would, and that the fuel test and an empty tank outrank both. The rungs
are compared against the scout policy's own answer rather than against a hand-computed cell wherever they can
be, so that these tests keep checking the hand-off if the sweep geometry is ever changed.

The grid is 40 columns by 20 rows, with an observation radius of 2, a speed of 5, a drop radius of 2 and a
separation of 2, and three UAVs unless a test says otherwise.
"""

# python libraries

import pytest

# own python modules

import config

from config import ACTION_DOWN, ACTION_LEFT, ACTION_RIGHT, ACTION_STAY, ACTION_UP

from sim.policy import Action, ScoutFirefighterPolicy, ScoutPolicy, flight_path

BASE = {"base_pos": (10, 10), "base_cells": [(10, 10), (11, 10), (10, 11), (11, 11)]}


@pytest.fixture
def policy():
    return ScoutFirefighterPolicy()


@pytest.fixture(autouse=True)
def grid(sim_config):
    sim_config(HEIGHT=40, WIDTH=20, UAV_OBSERVATION_RADIUS=2, SECURITY_DISTANCE=2, UAV_SPEED=5,
               WATER_DROP_RADIUS=2, ACTIVATE_POSITION_ERROR=False, ACTIVATE_FUEL=False)


@pytest.fixture
def fueled(sim_config):
    sim_config(UAV_FUEL=100, UAV_FUEL_RESERVE=0.1, UAV_FUEL_IDLE_BURN=1.0, UAV_FUEL_BURN_PER_CELL=1.0,
               UAV_FUEL_SPEED_EXPONENT=1.5, UAV_FUEL_WATER_PENALTY=0.3, ACTIVATE_FUEL=True)


def team(observation, *uavs):
    """Build the observations of a team from (uav_id, kwargs) pairs, each UAV shown the others in its window."""
    return [observation(uav_id=uav_id, **kwargs) for uav_id, kwargs in uavs]


def scout_answer(observations, index):
    """What ScoutPolicy would tell UAV number 'index' to do, with the lanes given to the whole team."""
    scout = ScoutPolicy()
    lanes = scout.lanes(observations)
    return scout.action_for(observations[index], lanes[observations[index].uav_id])


# --- the lanes ------------------------------------------------------------------


@pytest.mark.verifies("POL-SFF-1")
def test_lanes_are_given_out_to_every_uav_in_the_call_including_the_one_that_is_fighting(policy, observation):
    seen = []
    original = policy.scout.lanes
    policy.scout.lanes = lambda observations: seen.append(sorted(o.uav_id for o in observations)) or original(observations)
    observations = team(observation,
                        (1, dict(pos=(5, 5), has_water=True, burning=[(6, 5)])),
                        (2, dict(pos=(20, 5), has_water=True)),
                        (3, dict(pos=(33, 5), has_water=True)))

    policy.select_actions(observations)

    assert seen == [[1, 2, 3]]


@pytest.mark.verifies("POL-SFF-1")
def test_a_uav_that_stops_to_fight_does_not_move_the_lane_of_the_ones_still_scouting(policy, observation):
    scouting = dict(pos=(20, 5), has_water=True)
    others = [(1, dict(pos=(5, 5), has_water=True)), (3, dict(pos=(33, 5), has_water=True))]

    calm = policy.select_actions(team(observation, others[0], (2, scouting), others[1]))
    fighting = policy.select_actions(team(observation, (1, dict(pos=(5, 5), has_water=True, burning=[(6, 5)])),
                                          (2, scouting), others[1]))

    assert calm[1] == fighting[1]


# --- scouting ---------------------------------------------------------------------


@pytest.mark.verifies("POL-SFF-2")
def test_a_uav_with_water_and_nothing_to_fight_flies_the_scouts_action(policy, observation):
    observations = team(observation,
                        (1, dict(pos=(5, 5), has_water=True)),
                        (2, dict(pos=(20, 5), has_water=True)),
                        (3, dict(pos=(33, 5), has_water=True)))

    actions = policy.select_actions(observations)

    for index, action in enumerate(actions):
        expected = scout_answer(observations, index)
        assert action.direction == expected.direction
        assert 0 < action.speed <= expected.speed or action == expected


@pytest.mark.verifies("POL-SFF-8")
def test_a_uav_whose_only_sighting_is_taken_by_a_teammate_carries_on_scouting(policy, observation):
    # both can see the single fire; the first is sent to it, so the second has nothing left to claim
    observations = team(observation,
                        (1, dict(pos=(10, 5), has_water=True, burning=[(11, 5)])),
                        (2, dict(pos=(12, 5), has_water=True, burning=[(11, 5)])),
                        (3, dict(pos=(33, 5), has_water=True)))

    actions = policy.select_actions(observations)

    assert actions[0] == Action.dump()
    assert actions[1] != Action.dump()
    assert actions[1].direction == scout_answer(observations, 1).direction


# --- fighting ---------------------------------------------------------------------


@pytest.mark.verifies("POL-SFF-3")
def test_a_uav_with_a_fire_in_drop_range_dumps_water(policy, observation):
    observations = team(observation,
                        (1, dict(pos=(5, 5), has_water=True, burning=[(6, 5)])),
                        (2, dict(pos=(20, 5), has_water=True)))

    assert policy.select_actions(observations)[0] == Action.dump()


@pytest.mark.verifies("POL-SFF-3")
def test_a_uav_with_a_fire_in_view_out_of_drop_range_flies_toward_it(policy, observation):
    observations = team(observation,
                        (1, dict(pos=(5, 5), has_water=True, burning=[(5, 8)])),
                        (2, dict(pos=(20, 5), has_water=True)))

    action = policy.select_actions(observations)[0]

    assert action.direction == ACTION_UP
    assert action.speed >= 1


@pytest.mark.verifies("POL-SFF-3")
def test_a_uav_that_sees_nothing_is_sent_to_a_fire_a_teammate_found(policy, observation):
    # the first UAV has a clear window; the second is the scout that came upon two fires, far to its right.
    # It is nearest to the first and takes it, which leaves the second for the UAV that saw nothing
    observations = team(observation,
                        (1, dict(pos=(5, 5), has_water=True)),
                        (2, dict(pos=(20, 5), has_water=True, burning=[(20, 8), (21, 9)])))

    first, second = policy.select_actions(observations)

    assert first.direction == ACTION_RIGHT and first.speed >= 1
    assert second.direction == ACTION_UP


@pytest.mark.verifies("POL-SFF-3")
def test_the_uav_that_found_a_fire_fights_it_however_late_it_comes_in_the_team(policy, observation):
    # the first UAV in the list is 15 cells away; the one beside the fire is last, and is the one sent to it
    observations = team(observation,
                        (1, dict(pos=(5, 5), has_water=True)),
                        (2, dict(pos=(20, 5), has_water=True, burning=[(20, 6)])))

    first, second = policy.select_actions(observations)

    assert second == Action.dump()
    assert first != Action.dump()


@pytest.mark.verifies("POL-SFF-3")
def test_the_nearest_free_fire_is_chosen(policy, observation):
    observations = team(observation,
                        (1, dict(pos=(5, 5), has_water=True, burning=[(5, 2), (5, 9)])))

    assert policy.select_actions(observations)[0].direction == ACTION_DOWN


@pytest.mark.verifies("POL-SFF-3")
def test_a_fire_beside_an_out_building_outranks_a_nearer_one_in_the_open(policy, observation):
    observations = team(observation,
                        (1, dict(pos=(5, 5), has_water=True, burning=[(5, 4), (5, 8)],
                                 building_positions=[(5, 9)])))

    assert policy.select_actions(observations)[0].direction == ACTION_UP


# --- fuel ----------------------------------------------------------------------------


@pytest.mark.verifies("POL-SFF-4")
def test_a_uav_that_can_only_just_get_home_turns_for_the_base_whatever_is_burning(fueled, policy, observation):
    # 34 is under the 34.97 it takes, as worked out in test_scout_policy.py: the base is above the UAV
    obs = observation(pos=(5, 6), uav_id=0, fuel=34, has_water=True, burning=[(5, 3)], **BASE)

    action = policy.select_actions([obs])[0]

    # the flight is the scout's, held to what a UAV can see, and it is toward the base and not the fire
    expected = ScoutPolicy().refuel_action(obs)
    assert action.direction == expected.direction == ACTION_UP
    assert action.speed == min(expected.speed, config.UAV_OBSERVATION_RADIUS)


@pytest.mark.verifies("POL-SFF-4")
def test_a_uav_with_fuel_to_spare_still_goes_for_the_fire(fueled, policy, observation):
    obs = observation(pos=(5, 6), uav_id=0, fuel=90, has_water=True, burning=[(5, 3)], **BASE)

    assert policy.select_actions([obs])[0].direction == ACTION_DOWN


@pytest.mark.verifies("POL-SFF-4")
def test_a_uav_on_the_base_waits_there_until_the_tank_is_full_even_with_a_fire_in_view(fueled, policy, observation):
    obs = observation(pos=(10, 10), uav_id=0, fuel=60, has_water=True, burning=[(10, 8)], **BASE)

    assert policy.select_actions([obs])[0].direction == ACTION_STAY


# --- an empty tank -----------------------------------------------------------------------


@pytest.mark.verifies("POL-SFF-5")
def test_a_uav_without_water_flies_home_even_with_a_fire_in_view(policy, observation):
    obs = observation(pos=(5, 10), uav_id=0, has_water=False, burning=[(5, 8)], **BASE)

    action = policy.select_actions([obs])[0]

    assert action.direction == ACTION_RIGHT
    assert action.speed >= 1


@pytest.mark.verifies("POL-SFF-5")
def test_a_uav_without_water_waits_on_the_base_to_be_refilled(policy, observation):
    obs = observation(pos=(10, 10), uav_id=0, has_water=False, burning=[(10, 8)], **BASE)

    assert policy.select_actions([obs])[0].direction == ACTION_STAY


@pytest.mark.verifies("POL-SFF-5")
def test_with_no_base_a_uav_without_water_holds_position(policy, observation):
    obs = observation(pos=(5, 10), uav_id=0, has_water=False)

    assert policy.select_actions([obs])[0].direction == ACTION_STAY


# --- one fire, one UAV ---------------------------------------------------------------------


@pytest.mark.verifies("POL-SFF-6")
def test_two_uavs_are_not_sent_to_the_same_fire(policy, observation):
    # one fire in range of both: only one of them is sent to drop on it
    observations = team(observation,
                        (1, dict(pos=(10, 5), has_water=True, burning=[(11, 5)])),
                        (2, dict(pos=(12, 5), has_water=True, burning=[(11, 5)])))

    actions = policy.select_actions(observations)

    assert sum(action == Action.dump() for action in actions) == 1


@pytest.mark.verifies("POL-SFF-6")
def test_a_uav_whose_nearest_fire_is_taken_is_offered_the_next_nearest(policy, observation):
    # two fires, each within drop range of both UAVs, so both drop; had they both gone for the nearer one,
    # the second would have been left with nothing and would not have dropped
    observations = team(observation,
                        (1, dict(pos=(10, 5), has_water=True, burning=[(11, 5), (11, 6)])),
                        (2, dict(pos=(12, 5), has_water=True, burning=[(11, 5), (11, 6)])))

    actions = policy.select_actions(observations)

    assert actions == [Action.dump(), Action.dump()]


# --- keeping the team apart -------------------------------------------------------------------


@pytest.mark.verifies("POL-SFF-7")
def test_a_flight_stops_short_of_a_uav_in_view(policy, observation):
    # the fire is 4 cells away, the UAV can see 2, and a teammate stands 2 cells along the way
    observations = team(observation,
                        (1, dict(pos=(5, 5), has_water=True, burning=[(9, 5)], uavs=[(7, 5)])),
                        (2, dict(pos=(7, 5), has_water=False, uavs=[(5, 5)])))

    action = policy.select_actions(observations)[0]

    assert action.direction == ACTION_RIGHT
    assert action.speed == 1
    assert (7, 5) not in flight_path((5, 5), action)


@pytest.mark.verifies("POL-SFF-7")
def test_no_flight_is_longer_than_a_uav_can_see(policy, observation):
    observations = team(observation,
                        (1, dict(pos=(5, 5), has_water=True, burning=[(5, 7)])),
                        (2, dict(pos=(20, 5), has_water=True)))

    for action in policy.select_actions(observations):
        assert action.speed <= config.UAV_OBSERVATION_RADIUS


@pytest.mark.verifies("POL-SFF-7")
def test_a_crowded_team_of_scouts_and_fighters_never_shares_a_cell_off_the_base(policy, observation):
    # four UAVs start almost on top of one another, some with fire in view and some without, and are flown for
    # twenty steps with every action carried out exactly as asked. None of them drops its water, so the fires
    # stay where they are and the team keeps converging on them, which is the crowding this is about
    fires = {(14, 6), (15, 8), (9, 9)}
    positions = {1: (8, 5), 2: (9, 6), 3: (10, 4), 4: (12, 7)}
    base = set(BASE["base_cells"])

    def window(cell, pos):
        return abs(cell[0] - pos[0]) <= config.UAV_OBSERVATION_RADIUS and abs(cell[1] - pos[1]) <= config.UAV_OBSERVATION_RADIUS

    for _ in range(20):
        ids = list(positions)
        observations = [observation(pos=positions[i], uav_id=i, has_water=True,
                                    burning=[cell for cell in fires if window(cell, positions[i])],
                                    uavs=[positions[j] for j in ids if j != i and window(positions[j], positions[i])],
                                    **BASE)
                        for i in ids]
        moved = {}
        for uav_id, action in zip(ids, policy.select_actions(observations)):
            x, y = positions[uav_id]
            if action.is_movement():
                step_x, step_y = config.MOVEMENT_VECTORS[action.direction]
                x, y = x + step_x * action.speed, y + step_y * action.speed
            moved[uav_id] = (x, y)
        positions = moved

        cells = [cell for cell in positions.values() if cell not in base]
        assert len(cells) == len(set(cells)), positions


@pytest.mark.verifies("POL-SFF-7")
def test_the_home_base_is_shared_airspace(policy, observation):
    # the second UAV is standing on a base cell and the first is flying onto it: the base is never blocked
    observations = team(observation,
                        (1, dict(pos=(10, 8), has_water=False, uavs=[(10, 10)], **BASE)),
                        (2, dict(pos=(10, 10), has_water=False, uavs=[(10, 8)], **BASE)))

    first, second = policy.select_actions(observations)

    assert first == Action(ACTION_UP, 2)
    assert second.direction == ACTION_STAY
