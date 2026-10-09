# RBRP solver GUI

A small browser front end for `rbrp_ip`: pick a test case from `data/`, set the
solver parameters, run the classical (Gurobi) IP model and step through the
resulting move plan — then take the same test case through its QUBO form to a
D-Wave annealer and to QAOA on IBM hardware, simulated here or run for real.

The results column has three tabs: **Classical solution** (the IP model, as
before), **Quantum** (everything built on the exported QUBO) and **HPC · FRIDA**
(the same work as Slurm batch jobs on a cluster).

## Run

    make gui                       # builds rbrp_ip if needed, then opens a browser

or directly:

    .venv/bin/python -m gui.server           # http://127.0.0.1:8765
    .venv/bin/python -m gui.server --port 9000 --open

If the port is already taken the server moves to the next free one and prints the
URL it ended up on. Under WSL, note that a *Windows* program listening on the same
port wins the `localhost` forward: the browser then shows that program instead of
this GUI, even though the server is running fine. Check with

    powershell.exe -NoProfile -Command "Get-NetTCPConnection -LocalPort 8765 -State Listen"

and pick another `--port` if something answers.

The classical tab uses only the Python standard library, so the system `python3`
(>= 3.8) runs it too. The **Quantum** tab needs the project's `.venv` (dimod,
dwave-samplers, minorminer, dwave-system, qiskit, qiskit-aer, qiskit-ibm-runtime,
matplotlib and pylatexenc for Qiskit's circuit drawer); `make gui` picks the venv
when it exists. Started without them, the tab says which modules are missing.

## What it shows

* **Test case** — every `.dat`/`.txt` instance in `data/` (plus `.dat` files in
  the project root), with stacks / tiers / blocks and a live preview of the bay.
* **Parameters** — one control per `rbrp_ip` flag (`-E -T -t -m -s -g -u -v -Q`),
  each with a short explanation. A blank field means the flag is not passed.
  *Also export QUBO* (on by default) writes `qubo/<test case>[-E..][-T..].qubo` —
  never `problem.qubo` — plus a `.qubo.json` sidecar recording the test case,
  height limit and IP objective, and loads the export into the Quantum tab.
* **Design your own** — the second tab of *Test case* swaps the file list for an
  **instance designer**: set the number of stacks and tiers, type a priority into each
  slot (1 leaves the bay first), or hit *Random*, *Renumber*, *Copy selected file* and
  *Clear*. Blocks settle to the bottom of their stack, duplicates are flagged, and the
  slots are shaded like the bay. It runs like any other test case, and *Save to
  data/custom/* writes a `.dat` file that then shows up in the file list as
  `custom/<name>.dat`.
* **QUBO model** (first part of the Quantum tab) — loads a `.qubo` file exported
  with `-Q` (from `qubo/`, the project root or `data/`) and shows what is in it:
  a heatmap of the coefficient matrix (hover any cell for the two columns and their
  bias, with a line marking where the sequence columns end and the slack columns
  begin), the column count split into sequence and slack variables, the one-hot
  groups, how many selections the penalties admit, and a plain-language reading of
  what minimising the energy means. From there you can score a selection three ways:
  *cheapest column per block*, *find the best replayable selection* (walks every
  one-per-block selection, replaying each against the chosen instance), or by pasting
  column names or a sampler bitstring. Any selection that replays can be shown as a
  move plan in the same animated player as a solver result.
* **Quantum annealing · D-Wave**
  * *Embed* the QUBO's interaction graph with minorminer into an ideal Advantage
    (Pegasus P16), Advantage2 (Zephyr Z12) or 2000Q (Chimera C16) graph: physical
    qubits, chain lengths, chain strength, and a zoomable chip picture where
    hovering a qubit traces its chain.
  * *Sample* with simulated annealing on the logical QUBO, simulated annealing on
    the **embedded** problem (chains, majority-vote unembedding, chain-break
    fraction — what the embedding costs), tabu search, steepest descent, tree
    decomposition (exact), exhaustive enumeration (exact, ≤ 24 variables) or a
    random baseline; or, with a Leap token, the real QPU or the Leap hybrid solver.
  * *Estimates*: QPU access time from D-Wave's published timing model
    (programming + reads × (anneal + readout + delay)) for the embedding's qubit
    count, and time to solution at 99 % confidence, both on this machine and for a
    QPU with the same per-read success rate.
* **QAOA · IBM Qiskit**
  * *Circuit*: `qaoa_ansatz` over the Ising form from `qaoa_qubo.py` (same sign
    convention), drawn with Qiskit's matplotlib drawer as the logical circuit, in
    basis gates, or transpiled for an IBM device snapshot; the cost Hamiltonian
    term by term. Exports: OpenQASM 3 (parameterised or with tuned angles),
    OpenQASM 2 for IBM Quantum Composer, QPY, the Hamiltonian, and a ready-to-run
    Qiskit Runtime script.
  * *Estimates*, offline against fake-provider snapshots (Heron r1/r2/r3, Eagle,
    Falcon): qubits used, depth and two-qubit gates after routing, circuit time,
    estimated success probability (product of 1 − error over all gates and
    readouts), QPU time for one sampling job and for optimising on hardware, and
    the cost of simulating it here.
  * *Simulate*: angles tuned by a SciPy optimiser on Qiskit Aer's statevector
    (optionally on sampled shots, or CVaR), then the tuned circuit sampled ideally
    or through a device noise model. Live convergence chart, the p = 1 energy
    landscape (click a cell to start from it), probability per energy level
    against random guessing, approximation ratio, P(optimum), P(feasible), the
    most probable bitstrings and the sampled shots. Statevector simulation stops
    at 24 qubits.
  * *IBM Quantum Platform*: uses the saved account (or an API key and instance
    pasted in, optionally saved), lists backends with their queues, checks the
    circuit against a live backend's target without sending anything, and submits
    the tuned circuit as one `SamplerV2` job (after a confirmation). Job ids are
    kept in `quantum_runs/ibm-jobs.json`, so results can be fetched later; the
    decoded shots are scored like every other run. The `local:` backends run the
    same Runtime path on this machine against a device snapshot, without an account.
* **Results & comparison** — every run on the loaded QUBO in one table and dot
  plot against the IP optimum, with an interpretation and a JSON report.
* **Pipeline strip** — at the top of the Quantum tab: test case → classical IP →
  QUBO → annealing → QAOA simulation → hardware → comparison, each step's status,
  each a shortcut to its section.

Any sample that is feasible can be played as a move plan in the classical player
(a banner says where it came from, with a way back). A selection that includes a
*provisional* sequence — one whose destination the QUBO leaves open — is completed
greedily (onto a stack the block does not block, if there is one), and the plan
says how many moves were filled in, since it can need more relocations than the
energy promises.

* **HPC · FRIDA** — runs the classical solver and the classical simulations of the
  quantum algorithms as Slurm array jobs on FRIDA (UL FRI) or any Slurm cluster over SSH;
  see [hpc/README.md](../hpc/README.md).
  * *Connect*: an ssh host alias (FRIDA: from `tsh config`, after `tsh login` — the GUI never
    handles passwords or MFA), the remote base directory, Slurm account and the Gurobi
    licence path on the cluster. *Copy project to cluster* sends the project as a tar over
    ssh; *Build CPU/GPU image* submits `hpc/build_image.sbatch`, which saves an Enroot image
    with Gurobi, Ocean, Qiskit/Aer (and CuPy).
  * *Build a job*: classical IP solves or full pipelines (solve, export, samplers, QAOA) for
    any number of test cases, or quantum runs on the loaded QUBO — one array task each;
    resources per task (partition, time, CPUs, memory, GPU type, QAOA simulator: Aer on
    CPU, CuPy on GPU or numpy, maximum qubits). *Preview* shows `job.sbatch`, `step.sh` and
    `spec.json`.
  * *Jobs*: Slurm state per array, result files present, per-task logs, cancel; *Fetch*
    copies the job back to `quantum_runs/hpc/<name>/` and summarises it. Classical results
    open in the player, exported QUBOs load into the Quantum tab, samples play as plans and
    quantum runs join the comparison.
  * Transport *This machine* runs the same job directories locally (sbatch if present,
    else one task after another), for testing or when the GUI runs on a login node.
* **Settings** — light / dark / follow-system appearance.
* **Header** — the exact command that will run, the solver-binary status and the
  Run button. While a solve is in flight that button turns into **Stop solver**
  (the busy panel carries a second one, next to the elapsed time). Stopping
  signals the whole process group — `rbrp_ip` and anything it started — with
  SIGTERM, then SIGKILL if it does not go, and the GUI reports the best bounds
  the solver had printed before it died.
* **Results** — relocation count, lower bound, greedy upper bound, solve time and
  model size; the move plan as an animated bay (play / step / scrub); the
  relocation table; a plain-language interpretation; and the raw solver log.

Blocks are shaded by retrieval order — the darkest block leaves first. The red
outline marks the current target block, the orange outline the block in motion, and
a diagonal hatch marks the **blocking** blocks: those sitting above a more urgent
block, which must therefore be relocated. Their count is recomputed at every step
(shown beside the caption) and the initial count is a lower bound on the number of
relocations, so it is reported as its own stat tile.

## Deep links

The current selection can be put in the URL, which is handy for sharing a case or
re-running one:

    http://127.0.0.1:8765/?instance=data05-08-39.dat&E=2&t=60&run=1

Supported keys: `instance`, `E`, `T`, `t`, `m`, `s`, `g`, `u`, `run`, plus `qubo` (a
QUBO file to load, e.g. `qubo=qubo%2Fdata03-03-13-E2.qubo`) and `view=quantum` to
open on the Quantum tab. A saved design is addressed as `instance=custom%2F<name>.dat`.

A designed instance that is only run, never saved, is written to a temporary file for
the duration of the run and deleted afterwards — nothing lands in `data/` unless you
press *Save*.

## Layout

| File | Role |
|------|------|
| `server.py` | stdlib HTTP server: static files + `/api/instances`, `/api/instance`, `/api/solve`, `/api/save-instance`, `/api/stop`, the QUBO endpoints and `/api/quantum/*` |
| `brp_instance.py` | `.dat` readers (Caserta / Bacci / Exposito-Izquierdo), the `.dat` writer used by the designer, and the move-plan replay |
| `runner.py` | builds the `rbrp_ip` command line, runs it, parses stdout/stderr |
| `qubo.py` | reads an exported `.qubo` file, scores selections, replays one into a plan |
| `quantum.py` | the Quantum tab's back end: sample scoring, embedding, D-Wave samplers and timing model, QAOA build / estimates / simulation / landscape, IBM Runtime, exports, background jobs |
| `hpc.py` | the HPC tab's back end: ssh transport, project sync, image builds, Slurm array scripts, job registry (`quantum_runs/hpc/jobs.json`), status, logs, fetch and summaries |
| `static/` | `index.html`, `styles.css`, `app.js`, `qubo.js`, `charts.js` (SVG charts), `quantum.js`, `hpc.js` |

Long quantum computations (sampling, QAOA optimisation, estimates, the landscape,
job submission) run as background jobs the browser polls through
`/api/quantum/job?id=…`, with progress and a Stop button. All Qiskit work runs on a
few long-lived worker threads: with Qiskit 2.5 / Aer 0.17, a thread that touched a
circuit and then *exited* — which every one-thread-per-request handler does —
makes the next Aer run segfault.

Each solve runs as a child in its own process group, registered under the run id the
browser generates, which is what makes a targeted stop possible. `POST /api/stop` with
`{"runId": "…"}` stops that run; with `{}` it stops every run in flight — useful if a
browser tab was closed while the solver was still going.

`runner.py` reads the relocations out of the solver's own output and
`brp_instance.replay()` re-derives every intermediate bay state the same way
`Solution::print()` walks a plan, so the animation matches what the solver prints.

## Reading a QUBO

A `.qubo` file records the coefficient matrix plus, per column, the blocking block it
belongs to, the relocations it stands for and what they cost — the same
`# var / # cost / # reloc` metadata `verify_solution.py` replays from the command
line. Two things it does *not* record: which instance it was exported from, and the
height limit that was in force.

Exports made from the GUI carry both in their `.qubo.json` sidecar, and samples are
replayed against that test case and height limit whatever the sidebar shows. For
other files the GUI falls back to the sidebar: it compares the QUBO's one-hot groups
against the blocking blocks of the selected test case and shows a badge saying
whether they match, and it replays plans under the height limit from the parameter
panel, saying so. If a search reports that no selection replays, the usual causes are
an export made with different `-E`/`-T` settings than the panel holds, or optimal
selections that use provisional sequences (the quantum tab completes those).

`qaoa_qubo.py`, `sample_qubo.py` and `rescore.py` remain the command-line
equivalents of the Quantum tab.
