"""Tests for ScoutPolicy, the lane-sweep reconnaissance policy.

These are pure policy tests: they build Observations directly and never touch the grid or mesa. The ones that
need a team to move over time use a small stand-in for the simulator, fly(), which applies each action exactly
as asked and nothing else. That is deliberate. It is what makes them evidence for the *policy*: a team that
keeps its separation and covers its lanes here is doing it because of what the policy chose, not because the
simulator stopped it flying somewhere.

The geometry is exercised over a handful of grid shapes rather than the default one, including one that is
wider than it is tall and one that is taller than it is wide, because the simulator's grid is built as
MultiGrid(HEIGHT, WIDTH) and a policy that read the two the wrong way round would pass on every square grid.
"""

# python libraries

import math
import random

import pytest

# own python modules

import config

from config import ACTION_DOWN, ACTION_LEFT, ACTION_RIGHT, ACTION_STAY, ACTION_UP

from sim.policy import Action, ScoutPolicy, flight_path

# (columns, rows, UAVs, observation radius, separation): the shapes every geometric test runs over. The
# fourth and fifth are the simulator's own defaults for radius and a deliberately generous separation.
SHAPES = [
    (100, 100, 4, 4, 6),
    (60, 20, 3, 3, 4),
    (20, 60, 2, 3, 4),
    (40, 20, 2, 2, 2),
    (101, 50, 5, 4, 8),
    (50, 50, 1, 4, 6),
    (30, 12, 3, 2, 4),
]


@pytest.fixture
def policy():
    return ScoutPolicy()


@pytest.fixture
def shape(sim_config):
    """Apply one of SHAPES to the configuration. The grid is HEIGHT columns by WIDTH rows."""

    def _apply(columns, rows, radius, separation, speed=5):
        sim_config(HEIGHT=columns, WIDTH=rows, UAV_OBSERVATION_RADIUS=radius, SECURITY_DISTANCE=separation,
                   UAV_SPEED=speed, ACTIVATE_POSITION_ERROR=False)

    return _apply


def start_of(policy, lane):
    """The first cell of a lane's sweep loop, which is where the team-level tests put every UAV."""
    return policy.sweep_loop(lane)[0]


def in_window(cell, pos):
    return abs(cell[0] - pos[0]) <= config.UAV_OBSERVATION_RADIUS and abs(cell[1] - pos[1]) <= config.UAV_OBSERVATION_RADIUS


def fly(policy, observation, positions, steps):
    """Fly a team for 'steps' steps with every action carried out exactly as asked.

    'positions' maps uav_id to a cell. Each UAV is shown the others that fall inside its window, as the
    real UAV.observe() does. Returns the position of every UAV after every step, the first entry being the
    start.
    """
    history = [dict(positions)]
    for _ in range(steps):
        ids = list(positions)
        observations = [observation(pos=positions[i], uav_id=i,
                                    uavs=[positions[j] for j in ids if j != i and in_window(positions[j], positions[i])])
                        for i in ids]
        moved = {}
        for uav_id, action in zip(ids, policy.select_actions(observations)):
            x, y = positions[uav_id]
            if action.is_movement():
                step_x, step_y = config.MOVEMENT_VECTORS[action.direction]
                x, y = x + step_x * action.speed, y + step_y * action.speed
            moved[uav_id] = (x, y)
        positions = moved
        history.append(dict(positions))
    return history


def lane_cells(lane):
    rows = config.WIDTH
    return {(x, y) for x in range(lane[0], lane[1] + 1) for y in range(rows)}


def lanes_for(policy, observation, count):
    return policy.lanes([observation(pos=(0, 0), uav_id=i) for i in range(count)])


# --- the lanes ---------------------------------------------------------------


@pytest.mark.verifies("POL-SCT-1")
@pytest.mark.parametrize("columns, rows, count, radius, separation", SHAPES)
def test_lanes_never_share_a_column_and_run_left_to_right_by_uav_id(policy, observation, shape, columns, rows,
                                                                    count, radius, separation):
    shape(columns, rows, radius, separation)
    # ids handed over out of order, and far from 0..n-1, as the simulator's unique ids are
    ids = [9017, 9019, 9016, 9018, 9020][:count]
    observations = [observation(pos=(0, 0), uav_id=uav_id) for uav_id in ids]

    lanes = policy.lanes(observations)

    ordered = [lanes[uav_id] for uav_id in sorted(ids)]
    for (_, right_edge), (left_edge, _) in zip(ordered, ordered[1:]):
        assert left_edge == right_edge + 1
    assert ordered[0][0] == 0
    assert ordered[-1][1] == columns - 1


# --- staying in the flying zone, which is the separation claim --------------


@pytest.mark.verifies("POL-SCT-2")
@pytest.mark.parametrize("columns, rows, count, radius, separation", SHAPES)
def test_a_uav_on_its_loop_never_flies_outside_its_flying_zone(policy, observation, shape, columns, rows,
                                                                count, radius, separation):
    shape(columns, rows, radius, separation)
    lanes = lanes_for(policy, observation, count)

    for uav_id, lane in lanes.items():
        zone_left, zone_right = policy.flying_zone(lane)
        # every cell of the loop, and the whole of the flight asked for from it
        for cell in policy.sweep_loop(lane):
            observations = [observation(pos=cell if i == uav_id else start_of(policy, other), uav_id=i)
                            for i, other in lanes.items()]
            action = policy.select_actions(observations)[list(lanes).index(uav_id)]
            for x, y in [cell] + list(flight_path(cell, action)):
                assert zone_left <= x <= zone_right
                assert 0 <= y < rows


@pytest.mark.verifies("POL-SCT-2")
@pytest.mark.parametrize("columns, rows, count, radius, separation", SHAPES)
def test_no_two_loops_come_closer_than_the_separation(policy, observation, shape, columns, rows, count, radius,
                                                        separation):
    """The separation claim, checked without going through the flying zone the policy computes.

    Two UAVs can be anywhere on their loops at the same moment, because a UAV trimmed by avoid() or one that
    joined the loop late is out of step with the rest. So the claim is about every pair of cells, one from
    each loop. Written against the loops rather than against flying_zone() because a test that read the zone
    back from the policy would pass whatever margin the policy used: it was caught doing exactly that, passing
    with the margin removed.
    """
    shape(columns, rows, radius, separation)
    loops = [policy.sweep_loop(lane) for lane in lanes_for(policy, observation, count).values()]

    for index, left in enumerate(loops):
        for right in loops[index + 1:]:
            closest = min(math.dist(a, b) for a in left for b in right)
            assert closest >= config.SECURITY_DISTANCE


@pytest.mark.verifies("POL-SCT-2")
@pytest.mark.parametrize("columns, rows, count, radius, separation", SHAPES)
def test_a_team_out_of_step_on_its_loops_keeps_the_separation_at_every_step(policy, observation, shape, columns,
                                                                            rows, count, radius, separation):
    shape(columns, rows, radius, separation)
    lanes = lanes_for(policy, observation, count)
    rng = random.Random(1)
    # every UAV starts at a different, random point of its own loop, so the team is as far out of step as it
    # can be, and the neighbours are as likely as they ever are to be level with each other
    start = {uav_id: rng.choice(policy.sweep_loop(lane)) for uav_id, lane in lanes.items()}
    lap = max(len(policy.sweep_loop(lane)) for lane in lanes.values())

    history = fly(policy, observation, start, steps=lap + 5)

    if count > 1:
        closest = min(math.dist(a, b)
                      for positions in history
                      for index, a in enumerate(positions.values())
                      for b in list(positions.values())[index + 1:])
        assert closest >= config.SECURITY_DISTANCE
    # no two UAVs on one cell either, which is the collision the simulator scores
    assert all(len(set(positions.values())) == len(positions) for positions in history)


# --- joining the loop --------------------------------------------------------


@pytest.mark.verifies("POL-SCT-3")
@pytest.mark.parametrize("columns, rows, count, radius, separation", SHAPES)
def test_a_uav_anywhere_on_the_grid_reaches_its_loop(policy, observation, shape, columns, rows, count, radius,
                                                      separation):
    shape(columns, rows, radius, separation)
    lanes = lanes_for(policy, observation, count)
    rng = random.Random(0)

    for uav_id, lane in lanes.items():
        loop = set(policy.sweep_loop(lane))
        # the rest of the team stays in the call, on its own loops, because the lanes are worked out from
        # whoever is in the call and a UAV flown alone would be given the whole grid
        for _ in range(12):
            pos = (rng.randrange(columns), rng.randrange(rows))
            if pos in loop:
                continue
            positions = {other: start_of(policy, other_lane) for other, other_lane in lanes.items()}
            positions[uav_id] = pos
            reached = False
            for position in fly(policy, observation, positions, steps=columns + rows)[1:]:
                if position[uav_id] in loop:
                    reached = True
                    break
            assert reached, f"UAV {uav_id} starting at {pos} never joined its loop"


# --- following the loop ------------------------------------------------------


@pytest.fixture
def small(shape, observation, policy):
    """A grid 40 columns by 20 rows with one UAV and a radius of 2, small enough to name cells in.

    The lane is the whole grid and the flying zone loses one column at each edge, so the sweep columns are
    1, 5, 9, ... 37, ten of them, and the loop is up 1, down 5, up 9 and so on.
    """
    shape(40, 20, 2, 2)

    def _action(pos):
        return policy.select_actions([observation(pos=pos, uav_id=0)])[0]

    return _action


@pytest.mark.verifies("POL-SCT-4")
@pytest.mark.parametrize("pos, direction, speed", [
    ((1, 5), ACTION_UP, 2),       # an even column goes up
    ((1, 19), ACTION_RIGHT, 2),   # and turns right along the top at the top
    ((3, 19), ACTION_RIGHT, 2),   # a top connector keeps going right
    ((5, 10), ACTION_DOWN, 2),    # an odd column goes down
    ((5, 1), ACTION_RIGHT, 2),    # and turns right along the bottom connector at the bottom
    ((7, 1), ACTION_RIGHT, 2),    # a bottom connector keeps going right
    ((37, 1), ACTION_DOWN, 1),    # the last column drops onto the return row
    ((20, 0), ACTION_LEFT, 2),    # the return row runs left
    ((1, 0), ACTION_UP, 1),       # and turns up onto the first column
])
def test_a_uav_on_the_loop_flies_in_the_direction_of_travel(small, pos, direction, speed):
    action = small(pos)

    assert (action.direction, action.speed) == (direction, speed)


@pytest.mark.verifies("POL-SCT-4")
@pytest.mark.parametrize("columns, rows, count, radius, separation", SHAPES)
def test_the_loop_closes_and_visits_no_cell_twice(policy, observation, shape, columns, rows, count, radius,
                                                   separation):
    shape(columns, rows, radius, separation)

    for lane in lanes_for(policy, observation, count).values():
        loop = policy.sweep_loop(lane)

        # sweep_loop() only returns when the circuit comes back to its start, so a loop that did not
        # close would hang here rather than fail; what can go wrong quietly is a cell visited twice, which
        # would give a UAV two next cells
        assert len(loop) == len(set(loop))


# --- one lap sees the whole lane ---------------------------------------------


@pytest.mark.verifies("POL-SCT-5")
@pytest.mark.parametrize("columns, rows, count, radius, separation", SHAPES)
def test_one_lap_of_the_loop_brings_every_cell_of_the_lane_into_view(policy, observation, shape, columns, rows,
                                                                      count, radius, separation):
    shape(columns, rows, radius, separation)
    lanes = lanes_for(policy, observation, count)
    start = {uav_id: start_of(policy, lane) for uav_id, lane in lanes.items()}
    lap = max(len(policy.sweep_loop(lane)) for lane in lanes.values())

    # one cell per step is the slowest the loop can be flown, so this many steps is a lap whatever the speed
    history = fly(policy, observation, start, steps=lap)

    for uav_id, lane in lanes.items():
        unseen = {cell for cell in lane_cells(lane)
                  if not any(in_window(cell, positions[uav_id]) for positions in history)}
        assert not unseen, f"UAV {uav_id} never saw {sorted(unseen)[:5]} of its lane {lane}"


@pytest.mark.verifies("POL-SCT-5")
@pytest.mark.parametrize("start_index", [0, 7, 100, 211])
def test_a_uav_that_joins_the_loop_half_way_still_sees_the_whole_lane(policy, observation, shape, start_index):
    shape(100, 100, 4, 6)
    lane = policy.lanes([observation(pos=(0, 0), uav_id=0)])[0]
    loop = policy.sweep_loop(lane)

    history = fly(policy, observation, {0: loop[start_index % len(loop)]}, steps=len(loop))

    assert all(any(in_window(cell, positions[0]) for positions in history) for cell in lane_cells(lane))


# --- keeping the team apart --------------------------------------------------


@pytest.mark.verifies("POL-SCT-6")
def test_a_flight_stops_short_of_a_uav_in_the_way(small, policy, observation):
    # going up column 1 from (1, 5), with a UAV two cells ahead: one cell of flight is all there is room for
    obs = observation(pos=(1, 5), uav_id=0, uavs=[(1, 7)])

    assert policy.select_actions([obs]) == [Action(ACTION_UP, 1)]


@pytest.mark.verifies("POL-SCT-6")
def test_a_uav_with_the_next_cell_taken_holds_position(small, policy, observation):
    obs = observation(pos=(1, 5), uav_id=0, uavs=[(1, 6)])

    assert policy.select_actions([obs]) == [Action.stay()]


@pytest.mark.verifies("POL-SCT-6")
def test_a_uav_beside_the_route_costs_it_nothing(small, policy, observation):
    obs = observation(pos=(1, 5), uav_id=0, uavs=[(2, 6), (0, 7)])

    assert policy.select_actions([obs]) == [Action(ACTION_UP, 2)]


@pytest.mark.verifies("POL-SCT-6")
def test_a_flight_stops_short_of_a_cell_a_teammate_was_sent_through(shape, policy, observation):
    shape(40, 20, 2, 2)
    # UAV 1 is going up column 21 of its own lane and will cross (21, 6) and (21, 7). UAV 0 is outside its lane
    # at (23, 7), flying left toward the nearest column of its own, and would cross (22, 7) and (21, 7).
    going_up = observation(pos=(21, 5), uav_id=1)
    crossing = observation(pos=(23, 7), uav_id=0)

    actions = policy.select_actions([going_up, crossing])

    assert actions[0] == Action(ACTION_UP, 2)
    assert actions[1] == Action(ACTION_LEFT, 1)


@pytest.mark.verifies("POL-SCT-7")
def test_a_uav_standing_on_the_base_does_not_block_a_flight(small, policy, observation):
    # the same UAV two cells ahead as above, but it is on the base footprint, which is shared airspace
    obs = observation(pos=(1, 5), uav_id=0, uavs=[(1, 7)], base_pos=(1, 7), base_cells=[(1, 7)])

    assert policy.select_actions([obs]) == [Action(ACTION_UP, 2)]


# --- never further than it can see -------------------------------------------


@pytest.mark.verifies("POL-SCT-8")
@pytest.mark.parametrize("radius, speed, expected", [(2, 5, 2), (4, 3, 3), (4, 5, 4), (1, 5, 1)])
def test_no_flight_is_longer_than_the_radius_or_the_speed(policy, observation, shape, radius, speed, expected):
    shape(100, 100, radius, 2, speed=speed)
    lane = policy.lanes([observation(pos=(0, 0), uav_id=0)])[0]
    pos = start_of(policy, lane)

    # the loop asks for the whole of the straight piece, which is 98 cells up the first column
    action = policy.select_actions([observation(pos=pos, uav_id=0)])[0]

    assert action.speed == expected


@pytest.mark.verifies("POL-SCT-8")
@pytest.mark.parametrize("overrides", [{"UAV_SPEED": 0}, {"UAV_OBSERVATION_RADIUS": 0}])
def test_a_uav_that_cannot_fly_or_cannot_see_holds_position(policy, observation, shape, sim_config, overrides):
    shape(40, 20, 2, 2)
    sim_config(**overrides)

    assert policy.select_actions([observation(pos=(1, 5), uav_id=0)]) == [Action.stay()]


# --- a lane that cannot be swept ---------------------------------------------


@pytest.mark.verifies("POL-SCT-9")
@pytest.mark.parametrize("overrides", [
    {"UAV_OBSERVATION_RADIUS": 0},                       # nothing can be seen
    {"WIDTH": 3},                                        # too few rows to hold a loop
    {"HEIGHT": 10, "SECURITY_DISTANCE": 6},              # lanes of five columns lose six to the margins
])
def test_a_lane_that_cannot_hold_a_loop_is_held(policy, observation, shape, sim_config, overrides):
    shape(40, 20, 2, 2)
    sim_config(**overrides)
    observations = [observation(pos=(1, 1), uav_id=i) for i in range(2)]

    actions = policy.select_actions(observations)

    assert actions == [Action.stay(), Action.stay()]
    assert all(action.direction == ACTION_STAY for action in actions)



# --- fuel ----------------------------------------------------------------------
#
# The numbers below are worked out by hand, so that they check the policy against the arithmetic and not
# against itself. The grid is 40 columns by 20 rows with a radius of 2, so a UAV cruises at 2 cells a step,
# and a step of 2 cells costs 1 + 2 ** 1.5 = 3.83 fuel with the idle burn and the burn per cell at 1. The base
# is the 2 by 2 block at (10, 10), and the UAV is at (5, 6), 4 rows below the base row and 5 columns left of
# it: the way home is two steps up (7.66) and two steps and a short one along (7.66 + 2.0), which is 17.31.
# One more step out and back is 2 * 3.83 = 7.66, and the reserve is a tenth of a tank of 100, which is 10, so
# the UAV has to turn for home once its tank holds 34.97 or less.

BASE = {"base_pos": (10, 10), "base_cells": [(10, 10), (11, 10), (10, 11), (11, 11)]}


@pytest.fixture
def fueled(shape, sim_config):
    shape(40, 20, 2, 2)
    sim_config(UAV_FUEL=100, UAV_FUEL_RESERVE=0.1, UAV_FUEL_IDLE_BURN=1.0, UAV_FUEL_BURN_PER_CELL=1.0,
               UAV_FUEL_SPEED_EXPONENT=1.5, UAV_FUEL_WATER_PENALTY=0.3, ACTIVATE_FUEL=True)


@pytest.mark.verifies("POL-SCT-10")
def test_a_uav_that_can_only_just_get_home_turns_for_the_base(fueled, policy, observation):
    # 34 is under the 34.97 it takes, and the base is above it, so the way home starts vertically
    obs = observation(pos=(5, 6), uav_id=0, fuel=34, **BASE)

    assert policy.select_actions([obs]) == [Action(ACTION_UP, 2)]


@pytest.mark.verifies("POL-SCT-10")
def test_a_uav_with_the_fuel_to_spare_keeps_sweeping(fueled, policy, observation):
    # 36 is over the 34.97, and column 5 is an odd one, which the loop flies down
    obs = observation(pos=(5, 6), uav_id=0, fuel=36, **BASE)

    assert policy.select_actions([obs]) == [Action(ACTION_DOWN, 2)]


@pytest.mark.verifies("POL-SCT-10")
def test_the_one_step_lookahead_turns_a_uav_home_before_the_way_back_is_too_long(fueled, policy, observation):
    # 30 clears the 17.31 to fly home and the 10 reserve, which is 27.31, so a UAV that only asked whether
    # it could get home from where it stands would carry on. It would then be a step further out with 9
    # fewer, and the way back would be 7.66 longer: it would turn for home too late. The lookahead is what
    # makes this one turn now.
    obs = observation(pos=(5, 6), uav_id=0, fuel=30, **BASE)

    assert policy.select_actions([obs]) == [Action(ACTION_UP, 2)]


@pytest.mark.verifies("POL-SCT-10")
def test_the_water_a_uav_still_carries_makes_it_turn_for_home_sooner(fueled, policy, observation):
    # carrying a full load adds 30% to the whole trip: (17.31 + 7.66) * 1.3 + 10 = 42.46. So 40 is enough to
    # carry on without the water and not enough with it
    dry = observation(pos=(5, 6), uav_id=0, fuel=40, **BASE)
    loaded = observation(pos=(5, 6), uav_id=0, fuel=40, has_water=True, **BASE)

    assert policy.select_actions([dry]) == [Action(ACTION_DOWN, 2)]
    assert policy.select_actions([loaded]) == [Action(ACTION_UP, 2)]


@pytest.mark.verifies("POL-SCT-10")
def test_on_the_base_row_a_uav_that_needs_fuel_flies_along_it(fueled, policy, observation):
    # 4 columns left of the nearest base cell, so it is one row-wise step of 2 toward it
    obs = observation(pos=(6, 10), uav_id=0, fuel=20, **BASE)

    assert policy.select_actions([obs]) == [Action(ACTION_RIGHT, 2)]


@pytest.mark.verifies("POL-SCT-10")
def test_a_uav_to_the_right_of_the_base_flies_left_and_one_above_flies_down(fueled, policy, observation):
    right = observation(pos=(16, 10), uav_id=0, fuel=20, **BASE)
    above = observation(pos=(10, 14), uav_id=0, fuel=20, **BASE)

    assert policy.select_actions([right]) == [Action(ACTION_LEFT, 2)]
    assert policy.select_actions([above]) == [Action(ACTION_DOWN, 2)]


@pytest.mark.verifies("POL-SCT-11")
def test_a_uav_on_the_base_with_a_part_empty_tank_waits_to_be_served(fueled, policy, observation):
    obs = observation(pos=(10, 10), uav_id=0, fuel=99, **BASE)

    assert policy.select_actions([obs]) == [Action.stay()]


@pytest.mark.verifies("POL-SCT-11")
def test_a_uav_on_the_base_with_a_full_tank_sets_out(fueled, policy, observation):
    # a full tank is 100, and the nearest sweep column to x = 10 is 9
    obs = observation(pos=(10, 10), uav_id=0, fuel=100, **BASE)

    assert policy.select_actions([obs]) == [Action(ACTION_LEFT, 1)]


@pytest.mark.verifies("POL-SCT-12")
def test_with_the_fuel_extension_off_the_rule_never_fires(fueled, policy, observation):
    # no fuel reading at all, which is what the real observation gives when ACTIVATE_FUEL is off
    obs = observation(pos=(5, 6), uav_id=0, **BASE)

    assert policy.select_actions([obs]) == [Action(ACTION_DOWN, 2)]


@pytest.mark.verifies("POL-SCT-12")
def test_a_capacity_with_no_fuel_reading_is_not_a_reason_to_refuel(fueled, policy, observation):
    # the simulator reports both or neither, but an observation built by hand can carry one without the other,
    # and the rule has to treat "no reading" as "nothing to react to" rather than trip over it
    obs = observation(pos=(5, 6), uav_id=0, fuel=None, fuel_capacity=100.0, **BASE)

    assert policy.select_actions([obs]) == [Action(ACTION_DOWN, 2)]


@pytest.mark.verifies("POL-SCT-12")
@pytest.mark.parametrize("fuel", [1, 50, 100])
def test_with_no_base_to_go_to_the_rule_never_fires(fueled, policy, observation, fuel):
    obs = observation(pos=(5, 6), uav_id=0, fuel=fuel)

    assert policy.select_actions([obs]) == [Action(ACTION_DOWN, 2)]


@pytest.mark.verifies("POL-SCT-13")
def test_a_uav_off_its_loop_that_is_blocked_steps_aside(fueled, policy, observation):
    # at (4, 12) the nearest sweep column is 5, so it wants to go right and finds a UAV on (5, 12)
    obs = observation(pos=(4, 12), uav_id=0, uavs=[(5, 12)], fuel=100, **BASE)

    assert policy.select_actions([obs]) == [Action(ACTION_UP, 1)]


@pytest.mark.verifies("POL-SCT-13")
def test_a_uav_on_the_top_row_steps_aside_downward(fueled, policy, observation):
    # (0, 19) is left of the first sweep column, so off the loop, and the column is the next cell along
    obs = observation(pos=(0, 19), uav_id=0, uavs=[(1, 19)], fuel=100, **BASE)

    assert policy.select_actions([obs]) == [Action(ACTION_DOWN, 1)]


@pytest.mark.verifies("POL-SCT-13")
def test_a_uav_on_its_way_to_refuel_keeps_the_right_of_way(fueled, policy, observation):
    # blocked in the same way, but it needs fuel, so it holds position and the other one gives way
    obs = observation(pos=(14, 10), uav_id=0, uavs=[(13, 10)], fuel=20, **BASE)

    assert policy.select_actions([obs]) == [Action.stay()]


@pytest.mark.verifies("POL-SCT-13")
def test_a_uav_on_its_loop_does_not_give_way(fueled, policy, observation):
    # on column 5 going down, with a UAV in the next cell: it holds, because its guarantee is worth more
    obs = observation(pos=(5, 6), uav_id=0, uavs=[(5, 5)], fuel=100, **BASE)

    assert policy.select_actions([obs]) == [Action.stay()]


@pytest.mark.verifies("POL-SCT-13")
def test_a_head_on_meeting_in_the_base_row_does_not_deadlock(fueled, policy, observation):
    """The bug that put POL-SCT-13 in the specification.

    UAV 0 is coming home to refuel along the base row from the right, UAV 1 has just refuelled and is going
    out to its lane along the same row, and they meet with one cell between them. Before the rule each was
    trimmed to a stop by the other, for ever, and UAV 0 ran dry three cells from the base. It was found by a
    run in which every seed lost a UAV at the same step.
    """
    positions = {0: (14, 10), 1: (13, 10)}
    fuel = {0: 20.0, 1: 100.0}
    for _ in range(12):
        observations = [observation(pos=positions[i], uav_id=i, fuel=fuel[i],
                                    uavs=[positions[j] for j in positions if j != i and in_window(positions[j], positions[i])],
                                    **BASE)
                        for i in positions]
        for uav_id, action in zip(positions, policy.select_actions(observations)):
            x, y = positions[uav_id]
            if action.is_movement():
                step_x, step_y = config.MOVEMENT_VECTORS[action.direction]
                x, y = x + step_x * action.speed, y + step_y * action.speed
            positions[uav_id] = (x, y)

    assert positions[0] in BASE["base_cells"]