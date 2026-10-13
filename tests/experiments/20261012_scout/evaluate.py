"""Evaluates the scout policy: safety, coverage and detection, under the configurations in SCENARIOS.

Run from the repository root:

    python3 experiments/20261012_scout/evaluate.py                 # every scenario, 30 seeds each
    python3 experiments/20261012_scout/evaluate.py --runs 10 --only A "A + position error"

`headless.py` reports collisions and UAVs lost, but not what a scout is for: how much of the map its UAVs
actually looked at, and how soon after ignition one of them saw the fire. Those need the observations of
every UAV on every step, so this builds the model directly and plays it forward itself. Every run is
seeded, and the seeds are the same in every scenario, so two scenarios differ only in their settings.

What is measured, per run:

  collisions     cells found holding more than one UAV (the model's own count)
  lost           UAVs destroyed, by collisions or by an empty tank
  coverage       share of the grid inside at least one UAV's observation window at some step
  detection      steps from ignition to the first step any UAV saw a burning cell, over the runs in which the
                 fire was still burning twenty steps after it was lit. A fire at the edge of the map can go
                 out by itself before any scout arrives, and there is nothing to detect in that run
  separation     the closest two UAVs were while both were on their sweep loops, which is what POL-SCT-2
                 makes a claim about. Not reported with the positioning error on, because the positions it
                 would use are the measured ones

The model is played in this process, so the scenarios share the global config module; every run starts by
putting every setting back to what it was on import.
"""

# python libraries

import argparse
import logging
import math
import os
import random
import statistics
import sys

# own python modules

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import config  # noqa: E402

from sim.agents import Fire  # noqa: E402
from sim.model import WildFireModel  # noqa: E402
from sim.policy import ScoutPolicy  # noqa: E402

DEFAULTS = {name: getattr(config, name) for name in dir(config) if name.isupper()}

# the separation every scenario asks for, and the settings that switch off what is not being studied
IDEAL = {"SECURITY_DISTANCE": 6, "ACTIVATE_POSITION_ERROR": False, "SMOKE_OCCLUDES_OBSERVATION": False}

# a 50 by 50 grid and four UAVs with a tank big enough for a whole lap, and a payload that costs nothing
A = {**IDEAL, "WIDTH": 50, "HEIGHT": 50, "UAV_FUEL": 500, "UAV_FUEL_RESERVE": 0.1, "UAV_FUEL_WATER_PENALTY": 0.0}

# the shipped 100 by 100 grid, with eight UAVs and a tank sized for the longer trips
B = {**IDEAL, "UAV_FUEL": 800, "UAV_FUEL_RESERVE": 0.1, "UAV_FUEL_WATER_PENALTY": 0.0, "NUM_AGENTS": 8}

SCENARIOS = {
    "shipped defaults": {"SECURITY_DISTANCE": 6},
    "defaults, no position error or smoke": IDEAL,
    "A": A,
    "A + position error": {**A, "ACTIVATE_POSITION_ERROR": True},
    "A + smoke occlusion": {**A, "SMOKE_OCCLUDES_OBSERVATION": True},
    "A + payload surcharge": {**A, "UAV_FUEL_WATER_PENALTY": 0.3},
    "A + no reserve": {**A, "UAV_FUEL_RESERVE": 0.0},
    "B": B,
}


def run_once(overrides, seed):
    for name, value in DEFAULTS.items():
        setattr(config, name, value)
    for name, value in overrides.items():
        setattr(config, name, value)
    config.MANAGING_SYSTEM = "none"
    config.SYSTEM_RANDOM = random.Random(seed)

    model = WildFireModel(policy=ScoutPolicy())
    policy = model.policy
    columns, rows = config.HEIGHT, config.WIDTH
    radius = config.UAV_OBSERVATION_RADIUS

    seen = set()
    first_seen = None
    closest = math.inf
    persisted = None

    for step in range(config.BATCH_SIZE):
        observations = model.observations()
        lanes = policy.lanes(observations)

        on_loop = []
        for observation in observations:
            x, y = observation.pos
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    if 0 <= x + dx < columns and 0 <= y + dy < rows:
                        seen.add((x + dx, y + dy))
            if first_seen is None and observation.burning_positions():
                first_seen = step

            sweep = policy.sweep_columns(lanes[observation.uav_id])
            on_loop.append(sweep is not None and policy.loop_action(observation.pos, sweep) is not None)

        for i in range(len(observations)):
            for j in range(i + 1, len(observations)):
                if on_loop[i] and on_loop[j]:
                    closest = min(closest, math.dist(observations[i].pos, observations[j].pos))

        if step == model.fire_start_step + 20:
            persisted = any(agent.burning for agent in model.schedule.agents if isinstance(agent, Fire))
        model.step()

    detected = first_seen is not None and first_seen >= model.fire_start_step
    return {
        "collisions": model.collisions,
        "lost": model.uavs_lost,
        "coverage": len(seen) / (columns * rows),
        "persisted": bool(persisted),
        "detect_steps": first_seen - model.fire_start_step if detected else None,
        "closest_on_loops": None if math.isinf(closest) else closest,
    }


def summarise(overrides, runs):
    results = [run_once(overrides, seed) for seed in range(1, runs + 1)]
    detect = [r["detect_steps"] for r in results if r["detect_steps"] is not None]
    persisted = [r for r in results if r["persisted"]]
    loops = [r["closest_on_loops"] for r in results if r["closest_on_loops"] is not None]
    return {
        "collisions": f"{statistics.mean(r['collisions'] for r in results):.2f} (max {max(r['collisions'] for r in results)})",
        "lost": f"{statistics.mean(r['lost'] for r in results):.2f}",
        "coverage": f"{100 * statistics.mean(r['coverage'] for r in results):.1f}%",
        "detected": f"{sum(r['detect_steps'] is not None for r in persisted)}/{len(persisted)}",
        "steps": f"{statistics.mean(detect):.1f}" if detect else "-",
        "separation": ("n/a" if overrides.get("ACTIVATE_POSITION_ERROR", DEFAULTS["ACTIVATE_POSITION_ERROR"])
                       else (f"{min(loops):.1f}" if loops else "-")),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--runs", type=int, default=30, help="seeds per scenario, 1..RUNS")
    parser.add_argument("--only", nargs="+", metavar="NAME", help="scenario names to run; --list shows them")
    parser.add_argument("--list", action="store_true", help="print the scenarios and stop")
    args = parser.parse_args()

    if args.list:
        for name, overrides in SCENARIOS.items():
            print(f"{name}: {overrides}")
        return

    unknown = [name for name in (args.only or []) if name not in SCENARIOS]
    if unknown:
        parser.error(f"unknown scenario(s) {unknown}; available: {list(SCENARIOS)}")

    logging.disable(logging.CRITICAL)
    columns = ["collisions", "lost", "coverage", "detected", "steps", "separation"]
    header = ["scenario"] + ["collisions/run", "lost/run", "coverage", "fires detected", "steps to detect", "closest on loops"]
    rows = [header]
    for name, overrides in SCENARIOS.items():
        if args.only and name not in args.only:
            continue
        summary = summarise(overrides, args.runs)
        rows.append([name] + [summary[column] for column in columns])
        print(" | ".join(rows[-1]), flush=True)

    print()
    widths = [max(len(row[i]) for row in rows) for i in range(len(header))]
    for row in rows:
        print("  ".join(cell.ljust(width) for cell, width in zip(row, widths)))


if __name__ == "__main__":
    main()