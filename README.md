# Restricted BRP solver

## Requirements

* [Boost (Boost.program_options)](https://www.boost.org/)
* [Gurobi Optimizer](https://www.gurobi.com/)

## Usage

For CV instances:

    rbrp_ip -E 2 data3-3-1.dat (two additional empty tiers)
    rbrp_ip data3-3-1.dat (no height limit)

For ZQLZ instances:

    rbrp_ip -E 0 00001.txt

For other options, type "rbrp_ip -h".

## QUBO export

    rbrp_ip -E 2 -Q problem.qubo data3-3-1.dat

writes the QUBO form of the same sequence model (see `qubomodel.cpp`). It is built
to be as small as the formulation allows while still having its optimum at an
optimal relocation plan:

* penalty weights come from the objective spread `U - S` (an upper bound on the
  model's optimum minus the sum of the cheapest sequence per blocking block),
  which is the quantity a penalty has to dominate, rather than from the largest
  sequence cost;
* capacity constraints that the one-hot groups already imply are dropped, as are
  those implied by another bucket; capacity 0 becomes a linear penalty and
  capacity 1 a pairwise one, so neither needs slack variables;
* conflict pairs and capacity pairs share one term per pair;
* sequences too expensive to appear in any solution as good as the incumbent are
  left out, and columns that end up carrying no coefficient are dropped from the
  file.

Each run also reports `qubo_lb_objective=… ip_objective=…` on stderr: the QUBO's
own optimum next to the IP's, which must agree.

## GUI

    make gui

starts a local web GUI (standard library only) for picking a test case, setting
the parameters and stepping through the solution — see [gui/README.md](gui/README.md).
