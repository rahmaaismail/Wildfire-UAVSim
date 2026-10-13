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
- The guarantee covers UAVs on their loops. A UAV on its way to the loop (POL-SCT-3), or on its way to the
  base to refuel (POL-SCT-10), can be outside its zone and is protected by POL-SCT-6 alone, so separation can
  be below `SECURITY_DISTANCE` for the first steps of a run, while the team leaves the base, and on every
  refuelling trip.

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
- A tank is enough for the lap. The policy keeps no memory of where a UAV had got to, so after refuelling it
  rejoins its loop at the sweep column nearest the base row and starts again from there. A lane whose loop
  needs more fuel than one tank holds is therefore never finished: the UAV sweeps the part of it nearest the
  base, refuels, and sweeps the same part again. With the shipped defaults (`UAV_FUEL` of 150 on a 100 by 100
  grid with the base a quarter of the way in) this is the case, and the policy neither runs dry nor covers
  more than a few percent of the map. It is a limit on what the policy can achieve, not on its safety.

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

## POL-SCT-10 — Fuel outranks the sweep

> When `observation.fuel` is not `None` and the home base exists, and `observation.fuel` is at most the fuel
> to fly from `observation.pos` to the nearest cell of `observation.base_footprint()` plus the fuel for one
> more step out and the step back, plus `config.UAV_FUEL_RESERVE` of `observation.fuel_capacity`, the policy
> SHALL fly the UAV toward that cell, up or down to its row first and then along it, and SHALL NOT continue
> its sweep.

A UAV that runs dry loses every health point it has. The reserve of the firefighter is a flat share of the
tank, which a UAV far from home can use up on the way back, so this rule prices the way home instead.

The fuel to fly home is the cost the model will charge, step by step, at cruise speed (the smaller of
`config.UAV_SPEED` and `config.UAV_OBSERVATION_RADIUS`, which is what POL-SCT-8 allows), multiplied by the
payload surcharge for the water the UAV still carries. A scout never drops what it launched with, so leaving
the surcharge out makes the estimate short by `config.UAV_FUEL_WATER_PENALTY` of the whole trip.

One more step out and back is included because each step away from the base makes the way back longer by as
much as it cost to fly. A UAV that only asked whether it could get home from where it stands would spend its
margin one step at a time and turn for home too late by exactly that much.

**Assumptions:**

- The lane is within reach of the base: the fuel to fly from the base to the start of the lane and back,
  plus one more step and the reserve, is at most `config.UAV_FUEL`. When it is not, the UAV turns back before
  it arrives, refuels and sets out again, and sweeps nothing.
- A flight trimmed by POL-SCT-6 burns the idle rate while it waits, which the estimate does not count. The
  reserve is what pays for it, so a reserve of zero is not safe in a crowded run.
- `config.ACTIVATE_POSITION_ERROR` is off. With it on, the distance to the base is measured from a wrong
  position, and the margin is as wide as the reserve and no wider.

```yaml
id: POL-SCT-10
satisfied_by:
  - sim/policy/scout_policy.py::ScoutPolicy.needs_fuel
  - sim/policy/scout_policy.py::ScoutPolicy.refuel_action
  - sim/policy/scout_policy.py::ScoutPolicy.fuel_to_base
  - sim/policy/scout_policy.py::ScoutPolicy.leg_cost
  - sim/policy/scout_policy.py::ScoutPolicy.one_more_step
verified_by: test
status: draft
```

## POL-SCT-11 — A UAV on the base waits for a full tank

> When `observation.fuel` is not `None` and `observation.at_base()` is true and `observation.fuel` is below
> `observation.fuel_capacity`, the policy SHALL return `Action.stay()`.

Refuelling is not an action: standing on the base is what triggers it, and the base serves one UAV at a time
over `config.BASE_REFUEL_STEPS`. A UAV that left as soon as it landed would never stay long enough to be
served. Waiting for a full tank, and not merely a tank that clears POL-SCT-10, is what makes a sortie as long
as it can be.

```yaml
id: POL-SCT-11
satisfied_by:
  - sim/policy/scout_policy.py::ScoutPolicy.needs_fuel
  - sim/policy/scout_policy.py::ScoutPolicy.refuel_action
  - sim/policy/observation.py::Observation.at_base
verified_by: test
status: draft
```

## POL-SCT-12 — Without fuel to track or a base to go to, the rule is silent

> When `observation.fuel` is `None`, or when `observation.base_footprint()` is empty, the policy SHALL select
> the same action it would select if POL-SCT-10 and POL-SCT-11 did not exist.

With the fuel extension off there is nothing to run out of, and with the firefighting extension off there is
no base to refuel at. A policy that acted on either would fly to a base that is not there, or hold position
for a tank it cannot fill. It is also what keeps every run recorded before the fuel rule existed unchanged.

```yaml
id: POL-SCT-12
satisfied_by:
  - sim/policy/scout_policy.py::ScoutPolicy.needs_fuel
verified_by: test
status: draft
```

## POL-SCT-13 — A blocked UAV off its loop steps aside

> When the action the policy chose for a UAV is a movement and POL-SCT-6 has trimmed it to `Action.stay()`,
> and the UAV is not on its sweep loop and POL-SCT-10 does not apply to it, the policy SHALL return a one-cell
> flight up, or down when the UAV is on the top row, trimmed by POL-SCT-6 in the same way.

Two UAVs that meet head on in a single row, one going out to its lane and one coming home, are each trimmed to
a stop by the other. Neither can ever move again, and the one coming home runs dry in sight of the base. It
happened: it is the reason this requirement exists. The UAV that is refuelling keeps the right of way, and a
UAV on its loop does not give way, so that its guarantee under POL-SCT-2 is not traded for a cell.

```yaml
id: POL-SCT-13
satisfied_by:
  - sim/policy/scout_policy.py::ScoutPolicy.select_actions
  - sim/policy/scout_policy.py::ScoutPolicy.can_step_aside
  - sim/policy/scout_policy.py::ScoutPolicy.step_aside
verified_by: test
status: draft
```