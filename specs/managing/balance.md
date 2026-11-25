# `balance` — a managing system that judges the fleet against an assurance case

The `balance` managing system is the BALANCE method of the SEAMS 2027 paper (Fig. 3) built into the
MAPE-K loop in `sim/managing/`. At each evaluation it reads run-time indicators off the fleet, uses them to
assess how far the assurance case is still believed, decides whether the balance between the safety and the
performance claims is acceptable for the situation the fleet is in, and, when it is not, generates
adaptation options, assesses each one against an adapted copy of the case, and applies the one that keeps
the balance acceptable.

Six terms are used below.

The **case** is the dynamic assurance case network exported from Socrates as a JSON file: its nodes, the
links between them, and its **indicators**. An indicator is a named measurement attached to one or more
nodes, with **rules** that turn its current value into a **category**: OK, WARNING or FATAL, in increasing
order of severity. A **probe** is the code that computes an indicator's value from a `FleetSnapshot` and the
Knowledge base; an indicator is bound to its probe by name in `assurance/indicator_bindings.json`, because
the `path` field of an exported indicator is empty.

A **belief level** is one of the ordinal levels of the scale in MGT-BAL-8. The **top-level claims** are the
two claims the fleet must balance, safety and performance, named in `assurance/indicator_bindings.json`
rather than found by the `isRoot` flag of the export, which is true of five nodes in the current case
because it also marks the roots of shared sub-arguments. An **option** is one candidate change to the
allocation together with the change to the case that argues for it.

The managing system sees the fleet only through a `FleetSnapshot`, so every probe is a function of what the
UAVs report. Inherited obligations: nothing under `sim/managing/` imports the simulation, enforced by
`tests/managing/test_independence.py`.

## MGT-BAL-1 — The case is read from an exported Socrates file

> The case source SHALL read a Socrates JSON export into a `Case` holding every node, every link between
> nodes, every indicator with its rules and data points, and every rule category, resolving each reference
> by the key that field uses: a node's `children` and an indicator's `nodes` by `lid`, a node's `parents` by
> `id`.

The export mixes the two keys. Treating `children` as ids, or `parents` as lids, links every node to the
wrong neighbour without any error.

**Assumptions:**

- The export is schema version 14. Other versions are covered by MGT-BAL-2.

```yaml
id: MGT-BAL-1
satisfied_by:
  - sim/managing/assurance/case.py::JsonCaseSource.load
  - sim/managing/assurance/case.py::JsonCaseSource.parse
verified_by: test
status: draft
```

## MGT-BAL-2 — A case that cannot be trusted is refused

> The case source SHALL raise `CaseError` naming the problem when the file is not valid JSON, when its
> schema version is not one the source was written for, when a link names a node that is not in the file,
> or when a rule names a category that is not in the file.

A case read wrongly would make the managing system confident for no reason. Refusing it is the only
response that cannot do harm; MGT-BAL-17 says what the loop does in the meantime.

```yaml
id: MGT-BAL-2
satisfied_by:
  - sim/managing/assurance/case.py::JsonCaseSource.load
  - sim/managing/assurance/case.py::JsonCaseSource.parse
  - sim/managing/assurance/errors.py::CaseError
verified_by: test
status: draft
```

## MGT-BAL-3 — A value is rated by the one rule it satisfies

> Given an indicator and a value, the evaluator SHALL return the category of the rule whose conditions the
> value satisfies, combining the conditions of a rule by its `combinator`; SHALL return the most severe
> category when more than one rule is satisfied; and SHALL return no category, not OK, when no rule is
> satisfied.

The rules written in Socrates for this case do not overlap, but nothing in Socrates stops a later edit
making them overlap, and the safe reading of an overlap is the worse one. A value that no rule covers is
unrated rather than fine, which MGT-BAL-10 then counts against belief.

```yaml
id: MGT-BAL-3
satisfied_by:
  - sim/managing/assurance/indicators.py::Indicator.rate
verified_by: test
status: draft
```

## MGT-BAL-4 — The current value is the latest by timestamp

> The current value of an indicator SHALL be the data point with the latest `created` timestamp, whatever
> order the data points appear in the file.

The first export of this case held three data points that were listed out of time order. Reading the last
one in the file would have taken a stale value for the current one.

```yaml
id: MGT-BAL-4
satisfied_by:
  - sim/managing/assurance/indicators.py::Indicator.latest
verified_by: test
status: draft
```

## MGT-BAL-5 — An indicator is bound to its probe by name

> The managing system SHALL bind each indicator to the probe named for it in
> `assurance/indicator_bindings.json`; SHALL raise `CaseError` naming a binding whose probe does not exist or
> whose indicator the case does not have; and SHALL leave an indicator that has no binding unrated, reporting
> it once.

A name that matches nothing is a mistake to be found when the system is built, not on step 40 of a run.

```yaml
id: MGT-BAL-5
satisfied_by:
  - sim/managing/assurance/bindings.py::Bindings.resolve
verified_by: test
status: draft
```

## MGT-BAL-6 — A probe reads the snapshot and nothing else

> Each probe SHALL compute its value from a `FleetSnapshot` and the Knowledge base alone, SHALL exclude the
> cells of the home base from every count that concerns shared cells or close approaches, and SHALL return
> `None` rather than a number when its quantity is not defined at the step: the fuel probe when fuel is not
> tracked, and the coverage probe before the first full lap; and SHALL return `NOT_APPLICABLE`, which is
> neither a number nor `None`, when the quantity is not part of what the team is doing: the coverage probe
> while no UAV is flying a scouting policy.

The base is shared airspace, so any number of UAVs on it is not a hazard, and counting them would put the
whole team in the danger category on step 0 of every run. A zero reported for a quantity that is not
defined looks like good news; `None` is what lets MGT-BAL-10 treat it as no news. `NOT_APPLICABLE` is
for the opposite case, where there is no news because there is nothing to report: a team that is not
scouting has no scout coverage to fail. Reading that as a failure held the performance claim at SKEPTICAL
for the whole run, so that no option could ever be acceptable and the `balance` system chose "keep" at
almost every step, which is the heuristic system under another name.

**Assumptions:**

- The probes see what the UAVs report, so with `config.ACTIVATE_POSITION_ERROR` on the shared-cell and
  close-approach counts are counts of measured positions and may be wrong in either direction.
- The firefighter deficit is computed against a placeholder rule for the number of firefighters needed at a
  given fire distance, until the team-size and fire-distance experiments say what the rule should be.
- The shared-cell and close-pair counts leave the base out, as the simulation's own collision rule does, but
  its MR2 count of close pairs does not, so MR2 is the larger of the two. The thresholds in the case were
  worked out from MR2 and are on the cautious side until they are recalibrated against these probes.
- The coverage probe has no value until `config.BALANCE_COVERAGE_WINDOW` steps have been watched, and it
  watches only the steps the managing loop runs on: with `ADAPTATION_PERIOD` above 1 it sees the UAVs less
  often than they move, and under-counts what was in view in between.

```yaml
id: MGT-BAL-6
satisfied_by:
  - sim/managing/assurance/probes.py::PROBES
  - sim/managing/assurance/probes.py::measure
  - sim/managing/knowledge/base.py::Knowledge
  - config.py::BALANCE_COVERAGE_WINDOW
  - sim/managing/assurance/indicators.py::NOT_APPLICABLE
verified_by: test
status: draft
```

## MGT-BAL-7 — The top-level claims are the ones named

> The Analyzer SHALL take the claims to balance from the `top_level` entry of
> `assurance/indicator_bindings.json`, and SHALL raise `CaseError` when a named claim is not in the case.

```yaml
id: MGT-BAL-7
satisfied_by:
  - sim/managing/assurance/bindings.py::Bindings.top_level
verified_by: test
status: draft
```

## MGT-BAL-8 — Belief is propagated by the weakest link

> The belief assessor SHALL give every node a belief level on the ordered scale VERY_HIGH, HIGH, LOW,
> UNCERTAIN, SKEPTICAL, REJECT, from highest to lowest, and SHALL propagate it upward so that a node's
> belief is never higher than the lowest belief among the nodes it depends on; a node that appears under
> more than one parent SHALL have the same belief under each.

The scale and its order are those of the badges in Fig. 4 of the paper. This is a stand-in for the Certus
propagation operators, which are not in the export; it is replaced behind the `BeliefAssessor` interface
once the operators are available, and nothing else in this specification changes.

**Assumptions:**

- A defeater with no answer below it (a residual node) lowers the belief of the claim it attacks to no more
  than LOW. This is a modelling choice and the first thing to compare with Certus.

```yaml
id: MGT-BAL-8
satisfied_by:
  - sim/managing/assurance/belief.py::BeliefAssessor.assess
  - config.py::BALANCE_EVIDENCE_BELIEF
  - config.py::BALANCE_RESIDUAL_CAP
verified_by: test
status: draft
```

## MGT-BAL-9 — An indicator's category caps belief at its nodes

> The belief assessor SHALL cap the belief of every node an indicator is attached to by that indicator's
> category: no cap for OK, at most `config.BALANCE_WARNING_CAP` for WARNING, and at most
> `config.BALANCE_FATAL_CAP` for FATAL.

```yaml
id: MGT-BAL-9
satisfied_by:
  - sim/managing/assurance/belief.py::BeliefAssessor.cap_for
  - config.py::BALANCE_WARNING_CAP
  - config.py::BALANCE_FATAL_CAP
verified_by: test
status: draft
```

## MGT-BAL-10 — No news is not good news

> The belief assessor SHALL cap at UNCERTAIN the belief of every node whose indicator is unrated, whether
> because no probe is bound, the probe returned `None`, or no rule covers the value; and SHALL NOT cap, or
> give a reason for, a node whose indicator is `NOT_APPLICABLE`.

```yaml
id: MGT-BAL-10
satisfied_by:
  - sim/managing/assurance/belief.py::BeliefAssessor.cap_for
  - sim/managing/assurance/indicators.py::NOT_APPLICABLE
verified_by: test
status: draft
```

## MGT-BAL-11 — What is acceptable depends on the situation

> The balance assessor SHALL judge the balance acceptable only when the belief in the safety claim is at
> least the safety minimum, and the belief in the performance claim is at least the performance minimum,
> that `config.BALANCE_ACCEPTANCE` gives for the current base threat level; and the table SHALL lower the
> safety minimum and raise the performance minimum as the base threat level rises.

This is the trade-off the method exists for. With no fire near the base the fleet must be safe and may be
modestly effective; with the fire at the base the fleet must be effective, and some of the safety margin is
what pays for it. The base threat level, 0 to 3, is the one the existing analyzer already computes.

**Assumptions:**

- The table in `config.py` is a first guess and is the main thing to calibrate against runs.

```yaml
id: MGT-BAL-11
satisfied_by:
  - sim/managing/assurance/balance.py::BalanceAssessor.acceptable
  - config.py::BALANCE_ACCEPTANCE
verified_by: test
status: draft
```

## MGT-BAL-12 — Options are generated when balance is lost

> When the balance is not acceptable, or the base threat level reaches `config.BALANCE_RELAX_THREAT`, the
> option generator SHALL propose the option to keep the current allocation and, if separation is not yet
> relaxed, the option to relax clearance; and when separation is relaxed and the base threat level has
> fallen below `config.BALANCE_RELAX_THREAT`, SHALL propose the option to restore it.

```yaml
id: MGT-BAL-12
satisfied_by:
  - sim/managing/assurance/options.py::OptionGenerator.generate
  - config.py::BALANCE_RELAX_THREAT
  - config.py::BALANCE_RELAX_GAIN
verified_by: test
status: draft
```

## MGT-BAL-13 — Each option is assessed on a copy of the case

> For each option the analyzer SHALL build an adapted copy of the case, assess belief and balance on the
> copy, and keep the option only if the balance is acceptable there; SHALL leave the original case unchanged
> by every option; and SHALL choose, from the options kept, the one with the highest performance belief, a
> tie going to the option that restores clearance, and then to the one that changes the allocation least.

This is the inner loop of Fig. 3: Verify, Adapt DACN, Assess Belief, Assess Balance, keep or discard.
Restoring clearance wins a tie because it is where the fleet rests when nothing threatens the base; without
that rule, keeping things as they are would beat it on every tie and the cushion would never come back.
Changing the original would let a discarded option leave its marks on the case.

```yaml
id: MGT-BAL-13
satisfied_by:
  - sim/managing/analyze/balance.py::BalanceAnalyzer.analyze
  - sim/managing/assurance/options.py::Option.adapted_case
  - sim/managing/assurance/options.py::Option.adapted_values
verified_by: test
status: draft
```

## MGT-BAL-14 — Relaxing clearance never relaxes the shared-cell guarantee

> The relax option SHALL set `separation` to 0 in the directives of the UAVs it relaxes and SHALL leave every
> other UAV's directive as it was; SHALL, in the adapted case, stop rating the indicator for pairs closer
> than `SECURITY_DISTANCE` while keeping the indicator for UAVs sharing a cell in force; and SHALL NOT
> propose a `separation` below 0 or any change that leaves two UAVs free to occupy one cell. The restore
> option SHALL set `separation` to `ceil(config.SECURITY_DISTANCE)`.

`PolicyParams.separation` is the cushion `SuperPolicy` keeps around each team mate a UAV can see, and 0
keeps only the occupied cells clear, which is the firefighters' no-shared-cell behaviour. It is already
validated by the effector, so this option needs no new policy parameter.

**Assumptions:**

- The default `separation` is 0, so a managing system that never restores clearance leaves the fleet where
  the firefighter-only runs put it. Whether holding a cushion of `SECURITY_DISTANCE` cells costs the fleet
  more speed than the safety is worth has not been measured, and the restore option is not claimed to be
  free.
- The evidence that no-shared-cell flying produced no collisions (0 in 1,000 runs of each firefighting
  arm) came from runs with no managing system. Under a managing system `SuperPolicy` adds its own
  deconfliction, so the claim for the relaxed mode needs runs of its own.

```yaml
id: MGT-BAL-14
satisfied_by:
  - sim/managing/assurance/options.py::RelaxClearance
  - sim/managing/assurance/options.py::RestoreClearance
verified_by: test
status: draft
```

## MGT-BAL-15 — A change of mode has to hold before it is applied

> The planner SHALL leave a UAV's separation unchanged until the same option has been chosen for it as many
> evaluations running as the Knowledge base's hysteresis count (`ADAPTATION_HYSTERESIS`), SHALL keep every
> UAV's separation when it re-allocates the team, and SHALL NOT override a separation the heuristic planner
> asked for.

A fire front sitting on the threshold would otherwise flip the fleet between the two modes on alternate
steps and spend the run turning round.

```yaml
id: MGT-BAL-15
satisfied_by:
  - sim/managing/plan/balance.py::BalancePlanner.plan
  - sim/managing/assurance/belief.py::explain
  - sim/managing/contract.py::Symptoms
verified_by: test
status: draft
```

## MGT-BAL-16 — The system runs without the Socrates API

> The managing system SHALL be constructible and SHALL run with no Socrates API code installed, taking its
> case from a JSON file; and no module in the repository SHALL import the Socrates API unconditionally.

The Socrates Python API is licensed to those who need it for their projects and is not to be published, and
this repository is public. A live case source, if one is added, goes behind the same `CaseSource` interface
and is loaded only when asked for.

```yaml
id: MGT-BAL-16
satisfied_by:
  - sim/managing/assurance/case.py::CaseSource
  - sim/managing/assurance/case.py::JsonCaseSource
  - config.py::BALANCE_CASE_FILE
  - config.py::BALANCE_BINDINGS_FILE
verified_by: test
status: draft
```

## MGT-BAL-17 — A failing case costs the run its adaptation quality, not the run

> When the case cannot be loaded or assessed, the analyzer SHALL report the failure once and SHALL return
> the symptoms the `heuristic` analyzer would have returned for the same snapshot, so that the loop goes on
> with the heuristic managing system's behaviour.

This is the same contract the effector holds: a managing system that has gone wrong should cost the run
its adaptation quality, not end it.

```yaml
id: MGT-BAL-17
satisfied_by:
  - sim/managing/analyze/balance.py::BalanceAnalyzer.analyze
verified_by: test
status: draft
```

## MGT-BAL-18 — Every decision says why

> Every `Allocation` the `balance` planner returns SHALL carry a rationale naming the belief in each
> top-level claim and the minimum it was held to, the option chosen, the options turned down with what
> they fell short of, and what is holding each top-level claim down: the reasons on the nodes under it that
> are no more believed than it is.

```yaml
id: MGT-BAL-18
satisfied_by:
  - sim/managing/plan/balance.py::BalancePlanner.plan
verified_by: test
status: draft
```

## MGT-BAL-19 — The case can be fetched from an address

> When `config.BALANCE_CASE_URL` is not empty, the case source SHALL fetch the case from that http or https
> address, once when the analyzer is built, and SHALL NOT read `config.BALANCE_CASE_FILE`; SHALL refuse an
> address of any other kind, an address that does not answer within `config.BALANCE_CASE_TIMEOUT` seconds,
> and a response that is not a case it can trust, each as a `CaseError`; and SHALL send a token the
> environment names as a bearer token, never reading one from `config.py`.

The case is edited in Socrates and exported, so a managing system that can only read a file somebody copied
into the repository is arguing from whatever was copied last. A failed fetch is a failing case, which
MGT-BAL-17 already handles: the run goes on under the heuristic analysis and the failure is reported once.

**Assumptions:**

- The address serves the Socrates JSON export itself. Whether Socrates can serve one to a program without
  its Python API, and what it asks for in return, is not known; the token is passed as a bearer token
  because that is the common case and costs nothing when there is none.

```yaml
id: MGT-BAL-19
satisfied_by:
  - sim/managing/assurance/case.py::UrlCaseSource
  - sim/managing/analyze/balance.py::BalanceAnalyzer.default_source
  - config.py::BALANCE_CASE_URL
  - config.py::BALANCE_CASE_TIMEOUT
verified_by: test
status: draft
```
