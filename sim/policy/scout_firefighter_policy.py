"""Team policy that scouts until a fire is seen and then fights it."""

# own python modules

from .action import Action
from .base import Policy, flight_path, step_towards
from .firefighter import FirefighterPolicy
from .scout_policy import ScoutPolicy


class ScoutFirefighterPolicy(Policy):
    """Sweep a lane of the map; fight any fire that comes into view; refill at the base and sweep again.

    Every UAV follows the same ladder, highest rung first:

      * the fuel test is true                                 -> fly home and wait for the tank to be full
      * no water aboard                                       -> fly home and wait to be refilled
      * a fire the team can see that no teammate is on        -> fight it: drop water in range, else fly at it
      * otherwise                                             -> sweep the lane

    "The team can see" means any UAV in the call: the sightings of every observation are pooled, so a scout
    that comes upon a fire is what sends the others to it. Nothing is remembered from one step to the next.
    The fuel test is ScoutPolicy.needs_fuel(), which counts the distance home. The lanes are given out once,
    to every UAV in the call, whatever each is doing. It is meant to be flown with --managing none.
    """

    name = "scout-firefighter"

    def __init__(self):
        self.scout = ScoutPolicy()
        self.fighter = FirefighterPolicy()

    def select_actions(self, observations):
        lanes = self.scout.lanes(observations)
        assigned = self.assign_fires(observations)
        reserved = set()

        actions = []
        for observation in observations:
            lane = lanes[observation.uav_id]
            wanted, scouting = self.decide(observation, lane, assigned.get(observation.uav_id))
            action = self.scout.deconflict(observation, wanted, reserved)

            if (scouting and wanted.is_movement() and not action.is_movement()
                    and self.scout.can_step_aside(observation, lane)):
                action = self.scout.deconflict(observation, self.scout.step_aside(observation), reserved)

            reserved.update(flight_path(observation.pos, action))
            actions.append(action)
        return actions

    # which fire each UAV that can fight is sent to, as {uav_id: fire}: nearest pairing first, each UAV and
    # each fire used once, a fire beside an out building counting as closer than any in the open
    def assign_fires(self, observations):
        sighted = sorted({tuple(cell) for observation in observations for cell in observation.burning_positions()})
        if not sighted:
            return {}

        pairs = []
        for order, observation in enumerate(observations):
            if self.scout.needs_fuel(observation) or not observation.has_water:
                continue
            threatened = {tuple(cell) for cell in self.fighter.threatened_fires(observation, sighted)}
            x, y = observation.pos
            for fire in sighted:
                distance = (fire[0] - x) ** 2 + (fire[1] - y) ** 2
                pairs.append((0 if fire in threatened else 1, distance, order, fire))
        pairs.sort()

        assigned = {}
        taken = set()
        for _, _, order, fire in pairs:
            uav_id = observations[order].uav_id
            if uav_id in assigned or fire in taken:
                continue
            assigned[uav_id] = fire
            taken.add(fire)
        return assigned

    # returns (action, whether the UAV is sweeping)
    def decide(self, observation, lane, fire):
        if self.scout.needs_fuel(observation):
            return self.scout.refuel_action(observation), False

        if not observation.has_water:
            return self.fighter.return_to_base(observation), False

        if fire is not None:
            if self.fighter.within_drop_range(observation.pos, fire):
                return Action.dump(), False
            return step_towards(observation.pos, fire), False

        return self.scout.action_for(observation, lane), True