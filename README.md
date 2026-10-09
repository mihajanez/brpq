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

starts a local web GUI for picking a test case, setting the parameters and stepping
through the solution, with a second tab that takes the exported QUBO to D-Wave
annealing (embedding, classical samplers, Leap) and to QAOA (Qiskit circuit,
estimates, Aer simulation, IBM Quantum jobs) and compares every run with the IP
optimum — see [gui/README.md](gui/README.md). The classical tab needs only the
standard library; the quantum tab needs the `.venv`.

## HPC (FRIDA)

Classical solves and classical simulations of the quantum algorithms can run as Slurm
array jobs on FRIDA, the UL FRI cluster, from the GUI's **HPC · FRIDA** tab or by hand:
one task per test case or per quantum setting, inside an Enroot image with Gurobi, Ocean,
Qiskit and (optionally) CuPy for GPU statevector simulation. Setup and usage:
[hpc/README.md](hpc/README.md).

    python3 hpc/brpq_job.py run jobs/x/spec.json --task 0   # what each array task runs
    python3 hpc/brpq_job.py summarize jobs/x                # results/summary.csv

## QAOA and re-scoring saved runs

    .venv/bin/python qaoa_qubo.py problem.qubo -p 1 --save run.json
    .venv/bin/python rescore.py problem.qubo --run run.json

`qaoa_qubo.py` measures the logical circuit before transpiling, so the
classical register has one bit per QUBO variable (bit *i* = variable *i*,
Qiskit order: variable 0 is the rightmost character) whatever physical qubits
the transpiler picks. `--save` writes the counts, angles, backend, job id and
circuit size to JSON.

`rescore.py` re-scores samples offline: mean energy, one-hot feasibility,
optimum hits and, for small QUBOs, the uniform-random baseline. It also reads
IBM Quantum jobs downloaded from the platform (`--ibm-job info.json
result.json`), mapping the device's measured bits back to QUBO variables via
the circuit's layout. Jobs submitted before the Ising sign fix (for example
`damhugf8gn2s739lf5e0`) need `--old-sign`.
