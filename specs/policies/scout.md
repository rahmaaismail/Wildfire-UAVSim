# `scout` — sweep a lane of the map, looking for fire

Every UAV flying this policy is given a vertical strip of the grid, its **lane**, and sweeps it, so that a
team covers the map without two UAVs working the same ground or crowding each other. It carries no water and
never fights fire: its job is to see.

Three terms are used below. The **flying zone** is the lane narrowed by half of `config.SECURITY_DISTANCE`
(rounded up) at each edge; it is the only ground the UAV is supposed to be over once it is sweeping. The
**sweep loop** is a closed circuit inside the zone: sweep columns flown up and down in turn, joined along the
top and the bottom of the zone, with a return row along the bottom of the grid that closes it. The sweep
columns are `2 * config.UAV_OBSERVATION_RADIUS` apart, so the observation windows of neighbouring columns
touch.

The loop is closed on purpose. An `Observation` carries no memory and no clock, so the policy keeps none: what
a UAV does next is a function of where it is. Every cell of a closed loop has exactly one next cell, so the
loop can be joined anywhere and still covers everything within one lap, which a one-way sweep with a start and
an end cannot do for a UAV that joins it half way.

Inherited obligations: see [`_contract.md`](_contract.md).

## POL-SCT-1 — Lanes do not overlap

> Within a single call to `select_actions()`, the policy SHALL give each UAV a lane, a contiguous range of
> grid columns, such that no two lanes share a column, and SHALL order the lanes left to right by ascending
> `observation.uav_id`.

Disjoint lanes are what make separation hold by construction rather than by luck. Ordering by `uav_id` means
the assignment depends on nothing a UAV could mis-measure about itself.

**Assumptions:**

- The lanes split the columns of the grid evenly among the UAVs present in the call. When a UAV is lost the
  survivors' lanes widen and shift, so every other requirement here holds for the new lanes, but a UAV may
  have to cross ground its old neighbour was working to reach its new lane.
- The policy is flown on its own (`--managing none`). Under a managing system the policy is called once per
  group of UAVs, and it divides the grid by the size of the group, so two groups would be given overlapping
  lanes.
- The grid is `config.HEIGHT` columns by `config.WIDTH` rows, because the simulator builds it as
  `MultiGrid(HEIGHT, WIDTH)` and mesa's first argument is the width. The two are equal in every shipped
  configuration.

```yaml
id: POL-SCT-1
satisfied_by:
  - sim/policy/scout_policy.py::ScoutPolicy.lanes
verified_by: test
status: draft
```

## POL-SCT-2 — A UAV sweeping its lane stays in its flying zone

> When `observation.pos` is on the sweep loop of the UAV's lane, the policy SHALL return an action whose
> flight path contains no cell outside the UAV's flying zone.

This is the separation claim. Two UAVs that are each at least `ceil(SECURITY_DISTANCE / 2)` columns inside
their own lane are at least `2 * ceil(SECURITY_DISTANCE / 2) + 1` columns apart, which is more than
`SECURITY_DISTANCE`, so their Euclidean distance is at least `SECURITY_DISTANCE` whatever either of them sees
or does. The guarantee does not rest on `observation.uav_positions` being complete, which is why the policy is
built on lanes rather than on watching its neighbours.

**Assumptions:**

- `config.SECURITY_DISTANCE` is at most `2 * config.UAV_OBSERVATION_RADIUS`, so the margin at the edge of a
  lane is still inside the observation window of the nearest sweep column and no strip of the lane goes
  unseen.
- `config.ACTIVATE_POSITION_ERROR` is off. With it on, `observation.pos` is a measurement and a UAV that is
  really on the loop is told it is somewhere else, so it can leave the zone; the guarantee then holds for the
  measured position only.
- The guarantee covers UAVs on their loops. A UAV on its way to the loop (POL-SCT-3) can be outside its zone
  and is protected by POL-SCT-6 alone, so separation can be below `SECURITY_DISTANCE` for the first steps of a
  run, while the team leaves the base.

```yaml
id: POL-SCT-2
satisfied_by:
  - sim/policy/scout_policy.py::ScoutPolicy.flying_zone
  - sim/policy/scout_policy.py::ScoutPolicy.loop_action
  - sim/policy/scout_policy.py::ScoutPolicy.sweep_columns
verified_by: test
status: draft
```

## POL-SCT-3 — A UAV off the loop flies toward it

> When `observation.pos` is not on the sweep loop of the UAV's lane, the policy SHALL fly the UAV along its
> own row toward the nearest sweep column or, on the bottom row outside the loop, toward the nearer end of the
> return row, so that the UAV is on the loop after a bounded number of steps.

Every UAV begins off the loop, on the home base, and nothing else puts it on. Staying in its own row keeps the
approach free of diagonals, so there is never a tie to break and the policy draws no random number.

```yaml
id: POL-SCT-3
satisfied_by:
  - sim/policy/scout_policy.py::ScoutPolicy.approach_action
  - sim/policy/base.py::step_towards
verified_by: test
status: draft
```

## POL-SCT-4 — On the loop, the UAV follows it

> When `observation.pos` is on the sweep loop, the policy SHALL fly the UAV along the loop in its direction of
> travel: up an even sweep column and down an odd one, right along the top of the zone after an even column
> and along the bottom connector after an odd one, down onto the return row from the last column, left along
> the return row, and up onto the first column from the end of the return row.

The direction of travel is what makes the loop a loop. It is stated cell by cell because the whole policy is
this table: change one entry and the circuit no longer closes, and a UAV that follows it forever still
covers only part of its lane.

```yaml
id: POL-SCT-4
satisfied_by:
  - sim/policy/scout_policy.py::ScoutPolicy.loop_action
verified_by: test
status: draft
```

## POL-SCT-5 — One lap of the loop covers the whole lane

> When a UAV follows the policy's actions from any cell of its sweep loop without being obstructed, the policy
> SHALL bring every cell of that UAV's lane inside its observation window at some step within one lap of the
> loop.

This is the effectiveness claim at the level of one policy: given the room to fly, the sweep leaves nothing
unseen. It is what the macro-level simulation runs are then evidence *about*: how long a lap takes, and how
much of a run's budget it uses, are properties of a whole run that this requirement cannot state.

**Assumptions:**

- `config.SMOKE_OCCLUDES_OBSERVATION` is off. With it on a cell inside the window can still be hidden, so the
  window covers the lane and the UAV does not necessarily see all of it.
- `config.ACTIVATE_POSITION_ERROR` is off, for the reason given under POL-SCT-2.
- `config.ACTIVATE_FUEL` is off. The policy does not manage fuel, and at the default settings a tank lasts
  about twenty steps of flying at full speed, which is far short of a lap. This is the largest gap between
  this requirement and a shipped configuration, and it is a reason to specify a fuel rung before the policy is
  flown under the defaults.

```yaml
id: POL-SCT-5
satisfied_by:
  - sim/policy/scout_policy.py::ScoutPolicy.sweep_columns
  - sim/policy/scout_policy.py::ScoutPolicy.loop_action
verified_by: test
status: draft
```

## POL-SCT-6 — A flight is trimmed to stop short of another UAV

> The policy SHALL reduce the speed of a movement action so that its flight path contains no position in
> `observation.uav_positions` and no position already on the flight path of a UAV earlier in the same call,
> returning `Action.stay()` when even the first cell is taken.

Two UAVs that end a step on the same cell collide and both roll for damage. Giving up a cell or two of speed
costs a fraction of a step; a collision costs health points that do not come back.

**Assumptions:**

- `config.SMOKE_OCCLUDES_OBSERVATION` is off. A UAV standing in a smoke-hidden cell is absent from
  `observation.uav_positions` however close it is, and this rule cannot trim against a UAV it was not told
  about.

```yaml
id: POL-SCT-6
satisfied_by:
  - sim/policy/scout_policy.py::ScoutPolicy.deconflict
  - sim/policy/base.py::avoid
  - sim/policy/base.py::flight_path
verified_by: test
status: draft
```

## POL-SCT-7 — The home base is shared airspace

> The policy SHALL NOT trim a flight on account of a UAV standing on a position in
> `observation.base_footprint()`.

The whole team launches on the base. A policy that avoided it as it avoids everything else would leave the
team blocking itself at the first step.

```yaml
id: POL-SCT-7
satisfied_by:
  - sim/policy/scout_policy.py::ScoutPolicy.deconflict
  - sim/policy/observation.py::Observation.base_footprint
verified_by: test
status: draft
```

## POL-SCT-8 — No UAV is sent further than it can see

> The policy SHALL reduce the speed of a movement action to at most `config.UAV_OBSERVATION_RADIUS` and at
> most `config.UAV_SPEED`, returning `Action.stay()` when either is zero.

A flight that ends outside the observation window lands on a cell `observation.uav_positions` said nothing
about, so POL-SCT-6 cannot protect it. The policy builds its actions from the length of a straight piece of the
loop, not from `step_towards()`, so the cap on `UAV_SPEED` that POL-GEN-3 asks for has to be applied here
rather than inherited.

```yaml
id: POL-SCT-8
satisfied_by:
  - sim/policy/scout_policy.py::ScoutPolicy.within_sight
verified_by: test
status: draft
```

## POL-SCT-9 — A lane that cannot be swept is held

> When the UAV's lane cannot hold a sweep loop, that is when `config.UAV_OBSERVATION_RADIUS` is below one,
> when the grid has fewer than four rows, or when the flying zone has fewer than two columns, the policy SHALL
> return `Action.stay()`.

There are more UAVs than the grid can give a lane to, or the UAV cannot see, and a loop through a single
column would have to go up and down the same cells, which the policy has no memory to tell apart. Holding is
the safe answer and says so loudly in a run, because a team that holds position covers nothing.

```yaml
id: POL-SCT-9
satisfied_by:
  - sim/policy/scout_policy.py::ScoutPolicy.action_for
  - sim/policy/scout_policy.py::ScoutPolicy.sweep_columns
verified_by: test
status: draft
```