# Solver backend

## Production solver

Simulator version 0.2.0 and later use the open-source **HiGHS** solver through
the `highspy` Python package. All optimized dispatch cases are continuous
linear programs (LPs); none uses integer or binary variables.

The optimized cases are:

1. Self-consumption first
2. Peak reduction first
3. Fixed-tariff revenue maximisation
4. Dynamic-injection revenue maximisation

The no-battery and rule-based cases do not call an LP solver.

## Installation

`highspy>=1.8,<2` is a required dependency and is installed by `setup.cmd`.
Normal installation and production runs do not need Gurobi, `gurobipy`, or a
Gurobi licence.

HiGHS is distributed under the MIT licence. See the installed `highspy`
package metadata for the licence text and exact version.

## Status and diagnostics

Only a HiGHS `kOptimal` solution is accepted. Any other solver status raises an
`OptimizerError`; the application does not silently fall back to another
solver.

Detailed solver output is off by default. Enable it from Review or with the
`--detailed-solver-output` CLI option when diagnostic output is needed. Worker
runs append that output to `run.log`.

New comparison and sweep artifacts record the solver name, version, status,
runtime, and model dimensions. Historical artifact folders keep their stored
solver provenance and remain readable.

## Degenerate solutions

An LP can have several interval-by-interval schedules with the same objective
value. HiGHS may choose a different optimal schedule than another solver while
preserving the objective hierarchy, energy balances, physical constraints, and
reported aggregates. Tests therefore require physical and aggregate
consistency; they do not require bit-identical traces unless a fixture has a
unique solution.

## Optional differential backend

The source tree retains an internal Gurobi backend for developers who want to
run solver-differential tests. It is not a public runtime selector and is not
imported by production paths. Developers must install and license `gurobipy`
separately if they choose to use it.
