"""Reconnaissance policy: every UAV sweeps a lane of its own, so a team covers the map without crowding."""

# python libraries

import math

# own python modules

# see the note in random_policy.py about importing config as a module
import config

from config import ACTION_DOWN, ACTION_LEFT, ACTION_RIGHT, ACTION_UP

from sim import formulas

from .action import Action
from .base import Policy, avoid, flight_path, step_towards


class ScoutPolicy(Policy):
    """Sweep a lane of the map, looking for fire; never fight it.

    The grid is cut into vertical strips, one per UAV, ordered left to right by uav_id. A UAV only ever
    flies inside its own strip, and that is the whole of the separation argument: two UAVs in different
    strips are at least SECURITY_DISTANCE cells apart *by construction*, whatever either of them sees or
    does, so the safety claim does not depend on the observation being complete.

    To make the gap real, the strip is narrowed by half of SECURITY_DISTANCE (rounded up) at both edges,
    giving the UAV's flying zone. Two zones that belong to neighbouring strips are then at least
    2 * ceil(SECURITY_DISTANCE / 2) + 1 columns apart, which is more than SECURITY_DISTANCE.

    Inside the zone the UAV follows a closed loop, the sweep loop, made of

      * sweep columns, spaced 2 * UAV_OBSERVATION_RADIUS apart so that the observation windows of
        neighbouring columns touch, flown up and down alternately;
      * connectors along the top and the bottom of the zone that join one column to the next;
      * a return row along the very bottom of the grid that carries the UAV from the last column back to
        the first, so that the loop closes.

    The loop is closed on purpose. Observation carries no memory and no clock, so the policy keeps none
    either: where a UAV is on the loop is the only thing that says what it does next. A loop with a
    unique next cell for every cell can be entered anywhere and still covers everything within one lap,
    which a one-way sweep with a start and an end cannot do for a UAV that enters it half way.

    A UAV that is not on the loop, which is where every UAV begins, flies along its own row to the
    nearest sweep column and joins the loop there. While it is on its way it can be outside its zone, so
    the separation guarantee starts when it arrives; until then it relies on avoid() like everyone else.

    Collisions are avoided the way the firefighter avoids them: a flight is trimmed to stop short of any
    UAV in view and of any cell a teammate has already been sent through this step, no flight is longer
    than the UAV can see, and the home base is shared airspace. The base matters here because the whole
    team launches on it.

    Fuel comes before everything else. A UAV works out what it would cost to fly home from where it is,
    and turns for the base once its tank holds no more than that plus UAV_FUEL_RESERVE of a full one,
    so the reserve is a margin on top of the real distance rather than a flat share that a UAV far
    from home can use up on the way. Once on the base it holds position until the tank is full, which
    is what lets the base serve it, and then rejoins its loop at the nearest sweep column. The loop
    keeps no memory of where the UAV had got to, so every sortie starts from the base row; that is
    the price of a policy with no state, and it means a lap only completes when one tank is enough
    for it. The rule does nothing when fuel is not being tracked or there is no base to fly to.

    The strips are computed from the UAVs in the call, so a UAV lost mid-run makes the survivors' strips
    wider and shifts them. That is correct but not free: a UAV may have to cross ground its old
    neighbour was working. Run it unmanaged (--managing none) to see the policy on its own, because
    under a managing system the policy is called once per group of UAVs and divides the grid by the
    size of the group.
    """

    name = "scout"

    def select_actions(self, observations):
        lanes = self.lanes(observations)

        # the cells the team has already been sent through this step, so that no two UAVs are given the
        # same piece of airspace
        reserved = set()

        actions = []
        for observation in observations:
            lane = lanes[observation.uav_id]
            wanted = self.action_for(observation, lane)
            action = self.deconflict(observation, wanted, reserved)

            # a UAV that is blocked and has somewhere to be steps out of the way, because two UAVs that meet
            # head on in a single row, one going out and one coming home, would otherwise each hold position
            # for ever waiting for the other, and the one coming home would run dry in sight of the base
            if wanted.is_movement() and not action.is_movement() and self.can_step_aside(observation, lane):
                action = self.deconflict(observation, self.step_aside(observation), reserved)

            reserved.update(flight_path(observation.pos, action))
            actions.append(action)
        return actions

    # --- the geometry ----------------------------------------------------------------------------------

    # the number of columns (x) and rows (y) of the grid. The simulator builds its grid as
    # MultiGrid(HEIGHT, WIDTH), and mesa's first argument is the width, so x runs to config.HEIGHT and y to
    # config.WIDTH. The two are equal in every shipped configuration, but they are not the same setting.
    def extent(self):
        return config.HEIGHT, config.WIDTH

    # gives every UAV in the call a lane, which is the first and last column of its strip, ordered left to
    # right by ascending uav_id. The strips split the columns as evenly as whole numbers allow and never
    # share one.
    def lanes(self, observations):
        columns, _ = self.extent()
        uav_ids = sorted(observation.uav_id for observation in observations)
        count = len(uav_ids)
        return {uav_id: ((rank * columns) // count, ((rank + 1) * columns) // count - 1)
                for rank, uav_id in enumerate(uav_ids)}

    # the part of a lane the UAV flies in: the lane narrowed by half the separation, rounded up, at both
    # edges. Empty (first > last) when the lane is too narrow to give any room.
    def flying_zone(self, lane):
        margin = math.ceil(config.SECURITY_DISTANCE / 2)
        return lane[0] + margin, lane[1] - margin

    # the columns of the sweep loop, left to right, or None when this lane cannot be swept: the UAV cannot
    # see at all, the grid is too short to hold a loop, or the zone has fewer than two columns.
    #
    # There is always an even number of them, because a loop that goes up one column and down the next
    # only comes back to where it started after a whole number of pairs. The spacing never exceeds
    # 2 * UAV_OBSERVATION_RADIUS, and the outermost columns are close enough to the edges of the lane
    # that the lane is covered end to end.
    def sweep_columns(self, lane):
        radius = config.UAV_OBSERVATION_RADIUS
        _, rows = self.extent()
        first, last = self.flying_zone(lane)
        if radius < 1 or rows < 4 or last - first < 1:
            return None

        spacing = 2 * radius
        columns = list(range(first, last + 1, spacing))

        # the last regular column may stop short of the right edge of the lane
        if columns[-1] != last and columns[-1] + radius < lane[1]:
            columns.append(last)

        # an odd count is made even by adding the right edge of the zone, or when that is already a
        # column by splitting the first gap, which is always at least two cells wide
        if len(columns) % 2:
            if columns[-1] != last:
                columns.append(last)
            else:
                columns.insert(1, (columns[0] + columns[1]) // 2)
        return columns

    # --- deciding --------------------------------------------------------------------------------------

    # decides the action for a single UAV, given the lane it was assigned
    def action_for(self, observation, lane):
        columns = self.sweep_columns(lane)
        if columns is None:
            return Action.stay()

        # fuel outranks the sweep: an empty tank costs a UAV every health point it has
        if self.needs_fuel(observation):
            return self.refuel_action(observation)

        action = self.loop_action(observation.pos, columns)
        if action is None:
            action = self.approach_action(observation.pos, columns)
        return action

    # where the loop goes next from 'pos', as an action that runs to the end of the straight piece the UAV
    # is on, or None when 'pos' is not on the loop. Up is increasing y, as everywhere in the simulator.
    #
    #   even sweep column   up to the top row, then right along the top to the next column
    #   odd sweep column    down to the bottom connector row, then right along it to the next column,
    #                       or, on the last column, down onto the return row
    #   return row          left to the first column, then up onto it
    def loop_action(self, pos, columns):
        x, y = pos
        _, rows = self.extent()
        top = rows - 1
        first, last = columns[0], columns[-1]

        if y == 0 and first <= x <= last:
            if x > first:
                return Action(ACTION_LEFT, x - first)
            return Action(ACTION_UP, 1)

        if x in columns and 1 <= y <= top:
            index = columns.index(x)
            if index % 2 == 0:
                if y < top:
                    return Action(ACTION_UP, top - y)
                return Action(ACTION_RIGHT, columns[index + 1] - x)
            if y > 1:
                return Action(ACTION_DOWN, y - 1)
            if index < len(columns) - 1:
                return Action(ACTION_RIGHT, columns[index + 1] - x)
            return Action(ACTION_DOWN, 1)

        # a connector: the cells strictly between two columns on the top row after an even one, and on
        # the bottom connector row after an odd one
        if first < x < last and y in (top, 1):
            index = max(i for i, column in enumerate(columns) if column < x)
            if (y == top and index % 2 == 0) or (y == 1 and index % 2 == 1):
                return Action(ACTION_RIGHT, columns[index + 1] - x)

        return None

    # flies a UAV that is not on the loop toward it: along its own row to the nearest sweep column, or,
    # on the bottom row outside the loop, to the nearer end of the return row. The target is always in
    # the UAV's own row, so there is never a diagonal to break a tie over and no random number is drawn.
    def approach_action(self, pos, columns):
        x, y = pos
        if y == 0:
            target = (columns[0] if x < columns[0] else columns[-1], 0)
        else:
            target = (min(columns, key=lambda column: abs(column - x)), y)
        return step_towards(pos, target)

    # the cells of the sweep loop in the order they are flown, for a lane that can be swept. Built by
    # following loop_action() one cell at a time rather than from the description above, so it can only
    # ever be what the policy would really do. Used by the tests and by anyone checking the geometry.
    def sweep_loop(self, lane):
        columns = self.sweep_columns(lane)
        if columns is None:
            return []

        start = (columns[0], 1)
        cells = [start]
        pos = start
        while True:
            action = self.loop_action(pos, columns)
            step_x, step_y = config.MOVEMENT_VECTORS[action.direction]
            pos = (pos[0] + step_x, pos[1] + step_y)
            if pos == start:
                return cells
            cells.append(pos)

    # --- getting out of each other's way ---------------------------------------------------------------

    # whether a blocked UAV is the one that should give way: it is off its loop, so a step sideways costs it
    # nothing it had been guaranteed, and it is not on its way to refuel, which keeps the right of way
    def can_step_aside(self, observation, lane):
        columns = self.sweep_columns(lane)
        if columns is None or self.needs_fuel(observation):
            return False
        return self.loop_action(observation.pos, columns) is None

    # one cell up, or one cell down when it is already on the top row. The approach to the loop runs along
    # the UAV's own row, so after stepping aside it simply carries on along the next one.
    def step_aside(self, observation):
        _, rows = self.extent()
        return Action(ACTION_UP, 1) if observation.pos[1] < rows - 1 else Action(ACTION_DOWN, 1)

    # --- fuel ------------------------------------------------------------------------------------------

    # the speed the policy flies at when it is going somewhere rather than sweeping: as fast as a UAV can
    # be sent, which within_sight() caps at what it can see
    def cruise_speed(self):
        return min(config.UAV_SPEED, config.UAV_OBSERVATION_RADIUS)

    # the fuel that flying 'distance' cells in a straight line costs at cruise speed, step by step the way
    # the model charges it: whole steps at full speed and one shorter one for what is left over. 'water_load'
    # is the share of a full load the UAV is carrying, because the model multiplies the whole cost of a step
    # by it, and a scout that never drops what it launched with pays that surcharge on every step of every
    # flight, so an estimate that left it out would run short by exactly that much.
    def leg_cost(self, distance, water_load=0.0):
        speed = self.cruise_speed()
        if speed <= 0 or distance <= 0:
            return 0.0
        whole, rest = divmod(distance, speed)
        return (whole * formulas.fuel_burn_cost(speed, water_load=water_load)
                + (formulas.fuel_burn_cost(rest, water_load=water_load) if rest else 0.0))

    # the fuel that one more step away from the base costs, counting the step back as well. A UAV that only
    # asked whether it could get home from where it stands would spend its margin one step at a time, because
    # each step out makes the way back longer by as much as it cost to fly, and would turn for home too late
    # by exactly that much. Looking one step ahead is what keeps a UAV that sets off from the base with a full
    # tank from being left, a step later, with a tank that no longer reaches it.
    def one_more_step(self, water_load=0.0):
        speed = self.cruise_speed()
        return 2 * formulas.fuel_burn_cost(speed, water_load=water_load) if speed > 0 else 0.0

    # the cell of the home base footprint nearest to 'pos', counting distance the way the UAVs fly
    def nearest_base_cell(self, observation):
        x, y = observation.pos
        return min(observation.base_footprint(), key=lambda cell: abs(cell[0] - x) + abs(cell[1] - y))

    # the fuel it would take to fly from here to the base: up or down to its row first, then along it,
    # which is the route refuel_action() flies
    def fuel_to_base(self, observation):
        base_x, base_y = self.nearest_base_cell(observation)
        x, y = observation.pos
        load = observation.water_fraction()
        return self.leg_cost(abs(base_y - y), load) + self.leg_cost(abs(base_x - x), load)

    # whether this UAV has to put refuelling ahead of sweeping: its tank holds no more than the fuel to fly
    # home plus one more step out and back plus the reserve, or it is on the base and the tank is not yet
    # full. False whenever fuel is not being tracked or there is no base to go to, which is what lets the
    # policy predate the fuel extension unchanged.
    def needs_fuel(self, observation):
        if observation.fuel is None or not observation.fuel_capacity or not observation.base_footprint():
            return False
        if observation.at_base():
            return observation.fuel < observation.fuel_capacity
        reserve = config.UAV_FUEL_RESERVE * observation.fuel_capacity
        return (observation.fuel
                <= self.fuel_to_base(observation) + self.one_more_step(observation.water_fraction()) + reserve)

    # flies a UAV that needs fuel to the base, vertically first and then horizontally, so that it stays in
    # its own lane for as long as it can, and holds it there until the base has filled the tank
    def refuel_action(self, observation):
        if observation.at_base():
            return Action.stay()
        base_x, base_y = self.nearest_base_cell(observation)
        x, y = observation.pos
        if y != base_y:
            return Action(ACTION_UP if base_y > y else ACTION_DOWN, abs(base_y - y))
        return Action(ACTION_RIGHT if base_x > x else ACTION_LEFT, abs(base_x - x))

    # --- keeping the team apart ------------------------------------------------------------------------

    # trims an action so that the UAV stops short of a cell another UAV is standing on, and short of one a
    # teammate has already been sent through this step. The cells of the home base are left out: UAVs do
    # not collide there, and the whole team launches on it.
    def deconflict(self, observation, action, reserved):
        action = self.within_sight(action)
        shared = set(observation.base_footprint())
        blocked = ({tuple(cell) for cell in observation.uav_positions} | reserved) - shared
        return avoid(observation.pos, action, blocked)

    # keeps a UAV from being sent further than it can see, for the reason the firefighter does: a flight
    # that ends outside the observation window lands on a cell uav_positions said nothing about
    def within_sight(self, action):
        if not action.is_movement():
            return action
        speed = min(action.speed, config.UAV_OBSERVATION_RADIUS, config.UAV_SPEED)
        return Action(action.direction, speed) if speed > 0 else Action.stay()