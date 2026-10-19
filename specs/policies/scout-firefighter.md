# `scout-firefighter` — scout until a fire is seen, then fight it

The team policy for a run that wants fire found *and* fought. Every UAV starts out as a scout: it sweeps a lane
of the map exactly as [`scout`](scout.md) does. The moment a UAV with water aboard can be sent to a fire that some UAV in the team can see and no teammate is
already handling, it stops scouting and fights that fire exactly as [`firefighter`](firefighter.md) does,
and when its water is gone it flies home to refill and goes back to scouting.

The **sightings** are the burning cells that any UAV in the call has in view, pooled. A scout that comes upon a
fire is what tells the rest of the team where to go, which is the hand-off from finding a fire to fighting it.

There is no fixed split of the team into scouts and firefighters, and nothing is remembered between steps. A
UAV's role is a function of what it can see and carry *now*, which is the same constraint the two policies it is
built from already live under: an `Observation` carries no memory and no clock.

The **lane** and the **sweep loop** are the terms defined in [`scout.md`](scout.md). The **fuel test** is
`ScoutPolicy.needs_fuel()`, which asks whether the tank holds no more than the fuel to fly home plus one more step
out and back plus `config.UAV_FUEL_RESERVE` of a full tank.

Inherited obligations: see [`_contract.md`](_contract.md).

## POL-SFF-1 — Lanes are given to the whole team, whatever each UAV is doing

> Within a single call to `select_actions()`, the policy SHALL compute every UAV's lane from all of the UAVs
> in the call, so that a lane depends on `observation.uav_id` and on how many UAVs there are and on nothing
> else.

If lanes were drawn only from the UAVs that happened to be scouting, a UAV that stopped to fight a fire would
widen and shift every other lane on that step and back again on the next, and the whole team would lurch
sideways each time one UAV changed its mind.

**Assumptions:**

- The policy is flown on its own (`--managing none`), for the reason given under POL-SCT-1: under a managing
  system it is called once per group of UAVs and divides the grid by the size of the group.

```yaml
id: POL-SFF-1
satisfied_by:
  - sim/policy/scout_firefighter_policy.py::ScoutFirefighterPolicy.select_actions
  - sim/policy/scout_policy.py::ScoutPolicy.lanes
verified_by: test
status: draft
```

## POL-SFF-2 — A UAV that sees nothing to fight scouts

> When a UAV does not need fuel, carries water, and has been assigned no sighting, the policy SHALL
> return the action `ScoutPolicy.action_for()` returns for that UAV and its lane.

This is the half of the policy that finds fires. It is the scout policy unchanged, so the separation argument
of POL-SCT-2 applies to a UAV in this state for as long as it is on its loop.

```yaml
id: POL-SFF-2
satisfied_by:
  - sim/policy/scout_firefighter_policy.py::ScoutFirefighterPolicy.decide
  - sim/policy/scout_policy.py::ScoutPolicy.action_for
verified_by: test
status: draft
```

## POL-SFF-3 — A sighting hands the UAV to firefighting

> When a UAV does not need fuel, carries water, and has been assigned a sighting, the policy SHALL send it to
> that cell, dropping water when the cell is within `config.WATER_DROP_RADIUS` and flying toward it otherwise,
> and SHALL NOT return a sweep action for it. Sightings SHALL be assigned to the UAVs fit to fight in order of
> distance, the closest UAV and sighting first, each UAV receiving at most one sighting.

This is the hand-off from scouting to fighting. The sighting need not be in this UAV's own window: it is the
team's. Assigning nearest first, across the whole team, is what makes the UAV that found a fire the one that
fights it; claiming in team order instead would hand the fire to whichever UAV came first in the list, however
far away. A fire next to an out building a UAV can see counts as closer than any in the open, as it does for
`FirefighterPolicy`.

**Assumptions:**

- `config.ACTIVATE_FIREFIGHTING` is true. Without it no UAV ever carries water, POL-SFF-5 holds the whole team
  at the base, and the policy never reaches this rung.
- Pooling sightings uses only what the policy is handed, which is every UAV's own observation (POL-GEN-7). It
  does not make a UAV's window any larger, and it does not survive the step: nothing is remembered.
- A UAV that leaves its loop to fight is no longer inside its flying zone, so the separation bound of
  POL-SCT-2 does not cover it. Its separation from the team comes from POL-SFF-7 instead.

```yaml
id: POL-SFF-3
satisfied_by:
  - sim/policy/scout_firefighter_policy.py::ScoutFirefighterPolicy.decide
  - sim/policy/scout_firefighter_policy.py::ScoutFirefighterPolicy.assign_fires
  - sim/policy/firefighter.py::FirefighterPolicy.threatened_fires
  - sim/policy/firefighter.py::FirefighterPolicy.within_drop_range
verified_by: test
status: draft
```

## POL-SFF-4 — The fuel test outranks fighting

> When the fuel test is true for a UAV, the policy SHALL return the action `ScoutPolicy.refuel_action()` returns
> for it and SHALL NOT select a fire as its target, whatever water it carries and whatever is burning in view.

A UAV that runs dry loses every health point it has, and a fighter that has chased a fire away from the base
is exactly the UAV most likely to be out of range of it. The scout's test is used rather than the firefighter's
flat reserve because it counts the distance home, which a fighter ranging across the map needs.

```yaml
id: POL-SFF-4
satisfied_by:
  - sim/policy/scout_firefighter_policy.py::ScoutFirefighterPolicy.decide
  - sim/policy/scout_policy.py::ScoutPolicy.needs_fuel
  - sim/policy/scout_policy.py::ScoutPolicy.refuel_action
verified_by: test
status: draft
```

## POL-SFF-5 — A UAV without water goes home and waits to be refilled

> When the fuel test is false and `observation.has_water` is false, the policy SHALL fly the UAV toward
> `observation.base_pos`, or return `Action.stay()` when it is already on the base, and SHALL NOT return a sweep
> action or select a fire as its target.

A scout that has emptied its tank on a fire is no use to anyone out on its loop: it cannot fight the next fire
and the base serves it only if it stands there. Holding position on the base is what lets the base refill it,
the same way POL-FF-3 holds a firefighter there.

```yaml
id: POL-SFF-5
satisfied_by:
  - sim/policy/scout_firefighter_policy.py::ScoutFirefighterPolicy.decide
  - sim/policy/firefighter.py::FirefighterPolicy.return_to_base
verified_by: test
status: draft
```

## POL-SFF-6 — No two UAVs are sent to the same fire in one step

> Within a single call to `select_actions()`, the policy SHALL NOT assign the same burning cell to two UAVs, and
> a UAV whose nearest fire is assigned to a closer UAV SHALL be offered the next nearest free one.

Dropping water twice on one cell is wasted water, and the second drop is water a teammate could have put on a
different fire.

**Assumptions:**

- The assignment is per step. The policy keeps no memory, so two UAVs may choose the same fire on consecutive steps.

```yaml
id: POL-SFF-6
satisfied_by:
  - sim/policy/scout_firefighter_policy.py::ScoutFirefighterPolicy.assign_fires
verified_by: test
status: draft
```

## POL-SFF-7 — The team is kept apart, whatever each UAV is doing

> Within a single call to `select_actions()`, the policy SHALL trim every flight so that it stops short of any
> cell that another UAV in view is standing on and of any cell a UAV earlier in the team has been sent through
> this step, and SHALL NOT send any UAV further than `config.UAV_OBSERVATION_RADIUS`, whether the UAV is
> scouting, fighting, refuelling or refilling.

A scout and a fighter on neighbouring cells are exactly the mixed team that neither policy deconflicts against
on its own, because neither was ever shown the other. The trim is applied once, over the whole team.

**Assumptions:**

- The cells of the home base are exempt: any number of UAVs may stand on them, which is what lets the team
  queue to be refilled and refuelled.
- With `config.ACTIVATE_POSITION_ERROR` or `config.SMOKE_OCCLUDES_OBSERVATION` on, `observation.uav_positions`
  is not a complete account of the UAVs nearby, so this requirement is a statement about what the policy does
  with what it is told, not a guarantee that no collision happens.

```yaml
id: POL-SFF-7
satisfied_by:
  - sim/policy/scout_firefighter_policy.py::ScoutFirefighterPolicy.select_actions
  - sim/policy/scout_policy.py::ScoutPolicy.deconflict
verified_by: test
status: draft
```

## POL-SFF-8 — A UAV whose fires are all taken carries on scouting

> When a UAV does not need fuel, carries water, and has been assigned no sighting, the policy SHALL return the action `ScoutPolicy.action_for()` returns for
> that UAV and its lane.

A UAV that holds position because nearer teammates are already on every fire anyone can see does nothing for the
run. A
sweeping scout is what finds the next fire.

```yaml
id: POL-SFF-8
satisfied_by:
  - sim/policy/scout_firefighter_policy.py::ScoutFirefighterPolicy.decide
verified_by: test
status: draft
```