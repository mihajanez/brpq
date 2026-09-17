#!/usr/bin/env python3
"""Solve an exported QUBO with QAOA on a gate-based quantum backend.

Pipeline:
    problem.qubo --[load_qubo]--> dimod BQM --[to_ising]--> Ising Hamiltonian
    --[QAOAAnsatz, p layers]--> parameterized circuit
    --[classical optimizer + Estimator]--> tuned angles
    --[Sampler]--> bitstrings --[decode]--> selected sequence variables

This is a research/teaching tool for comparing backends, not a production
solver: for the instance sizes this project exports (a few tens of
variables), brute force and Gurobi both beat QAOA on quality and speed.  The
point here is to see the algorithm run on each kind of hardware.

Backends (choose exactly one):
    (default)     ideal statevector simulation, no shot noise
    --shots N     statevector *sampling* -- adds shot noise, still noiseless
                  gates (use this to see the effect of finite shots alone)
    --fake NAME   noisy Aer simulation using a real device's noise model,
                  e.g. --fake FakeMelbourneV2 (needs qiskit-aer). Pick a
                  fake backend with roughly as many qubits as the problem
                  (see qiskit_ibm_runtime.fake_provider) -- simulating a
                  127-qubit noise model (e.g. FakeSherbrooke) for a 15-qubit
                  circuit is far slower for no extra realism.
    --ibm [NAME]  real IBM Quantum hardware via Qiskit Runtime; bare --ibm
                  picks the least-busy operational device. Needs a saved
                  account (QiskitRuntimeService.save_account(...)) or
                  QISKIT_IBM_TOKEN in the environment.

Before spending real QPU time, run in this order:
    1. .venv/bin/python qaoa_qubo.py problem.qubo -p 1 --fake FakeMelbourneV2
       -- classical simulation with the target device's noise model, so you
       see roughly how the algorithm will behave. Copy the "parameters: ..."
       line it prints at the end.
    2. .venv/bin/python qaoa_qubo.py problem.qubo -p 1 --ibm --estimate
       -- validates the circuit against a real device's basis gates and
       coupling map (no job submitted), and prints a lower-bound QPU time
       estimate for the full run (see report_estimate()'s output for what it
       does and doesn't include).
    3a. .venv/bin/python qaoa_qubo.py problem.qubo -p 1 --ibm
        -- the real run with the full optimization loop, once 1-2 look
        reasonable -- but note --estimate's job-count warning: --maxiter
        iterations x --restarts is that many separate Runtime jobs.
    3b. .venv/bin/python qaoa_qubo.py problem.qubo -p 1 --ibm --no-optimize \
            --init-params <the "parameters: ..." line from step 1>
        -- the cheaper alternative: skip optimizing on hardware altogether
        and spend real QPU time on a single sampling job with angles already
        tuned in step 1.

Usage:
    .venv/bin/python qaoa_qubo.py problem.qubo -p 1
    .venv/bin/python qaoa_qubo.py problem.qubo -p 2 --restarts 5 --shots 4096
    .venv/bin/python qaoa_qubo.py problem.qubo -p 1 --fake FakeMelbourneV2
    .venv/bin/python qaoa_qubo.py problem.qubo -p 1 --ibm --estimate
    .venv/bin/python qaoa_qubo.py problem.qubo -p 1 --ibm
    .venv/bin/python qaoa_qubo.py problem.qubo -p 1 --ibm ibm_brisbane
    .venv/bin/python qaoa_qubo.py problem.qubo -p 1 --ibm --no-optimize \
        --init-params 1.570796,0.785398

Interpreting results (see also sample_qubo.py):
  - the exported QUBO drops the objective's constant term, and the
    assignment/conflict/capacity constraints are folded in as penalty terms
    -- a low-energy bitstring can still be classically infeasible, just as
    with the annealer.
  - variable names like x(4,2) identify a *sequence*, not the relocations it
    encodes; recovering an actual move plan needs the C++ solver's sequence
    list.
  - for small instances (n <= --brute-force-limit, default 20) the true
    optimum is computed by brute force so you can quote an approximation
    ratio -- QAOA is not expected to beat it, only approach it.
"""
import argparse
import sys
from itertools import product
from types import SimpleNamespace

import numpy as np
from qiskit.circuit.library import QAOAAnsatz
from qiskit.primitives import StatevectorEstimator, StatevectorSampler
from qiskit.quantum_info import SparsePauliOp
from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager
from qiskit_ibm_runtime.accounts import AccountNotFoundError
from scipy.optimize import minimize

from load_qubo import block_of, load_qubo


def qubo_to_ising_operator(bqm):
    """dimod BQM -> (SparsePauliOp cost Hamiltonian, ising offset).

    Qubit i <-> QUBO variable i (from_qubo() keeps the file's 0..n-1 indices),
    so a measured bit at position i decodes directly with load_qubo's names.
    """
    variables = sorted(bqm.variables)
    n = variables[-1] + 1
    if variables != list(range(n)):
        raise ValueError("expected QUBO variables to be a contiguous 0..n-1 "
                          "range, as produced by QUBOModel::export_qubo")

    h, J, offset = bqm.to_ising()
    terms = [("Z", [i], float(bias)) for i, bias in h.items() if bias]
    terms += [("ZZ", list(pair), float(bias)) for pair, bias in J.items() if bias]
    cost_op = SparsePauliOp.from_sparse_list(terms, num_qubits=n)
    return cost_op, offset, n


def bitstring_to_sample(bitstring, n):
    """Qiskit bitstrings are little-endian (qubit 0 = rightmost char)."""
    return {i: int(bitstring[-1 - i]) for i in range(n)}


def brute_force_optimum(bqm, n, limit):
    if n > limit:
        return None
    best_sample, best_energy = None, float("inf")
    for bits in product((0, 1), repeat=n):
        sample = dict(enumerate(bits))
        e = bqm.energy(sample)
        if e < best_energy:
            best_sample, best_energy = sample, e
    return best_sample, best_energy


def build_backend(args):
    """Returns (estimator, sampler, pm, backend) for the chosen backend.

    pm.run(ansatz) -> isa_ansatz transpiles (and, for --ibm/--fake, validates
    against the device's basis gates and coupling map). backend is the
    BackendV2 used for transpilation and, via backend.target, timing data for
    --estimate; it is None for the ideal statevector path, which has no
    associated device to validate against or time.
    """
    if args.ibm is not None:
        from qiskit_ibm_runtime import EstimatorV2, QiskitRuntimeService, SamplerV2

        service = QiskitRuntimeService()
        backend = (service.least_busy(operational=True, simulator=False)
                   if args.ibm == "" else service.backend(args.ibm))
        print(f"backend: {backend.name} ({backend.num_qubits} qubits, "
              f"queue may apply)")
        pm = generate_preset_pass_manager(optimization_level=3, backend=backend)
        estimator, sampler = EstimatorV2(mode=backend), SamplerV2(mode=backend)
        return estimator, sampler, pm, backend

    if args.fake is not None:
        import qiskit_ibm_runtime.fake_provider as fake_provider
        from qiskit_aer import AerSimulator
        from qiskit_aer.primitives import EstimatorV2 as AerEstimator
        from qiskit_aer.primitives import SamplerV2 as AerSampler

        fake_backend = getattr(fake_provider, args.fake)()
        backend = AerSimulator.from_backend(fake_backend)
        print(f"backend: Aer simulator with {args.fake} noise model "
              f"({backend.num_qubits} qubits)")
        pm = generate_preset_pass_manager(optimization_level=3, backend=backend)
        estimator, sampler = AerEstimator.from_backend(backend), AerSampler.from_backend(backend)
        return estimator, sampler, pm, backend

    print("backend: ideal statevector simulator"
          + (f" ({args.shots} shots)" if args.shots else ", exact expectation"))
    # QAOAAnsatz's cost/mixer layers are PauliEvolutionGate instructions; the
    # statevector primitives simulate a circuit gate-by-gate but need those
    # decomposed into standard basis gates first, or they fall back to
    # building the full 2^n x 2^n unitary (fine for a handful of qubits,
    # catastrophic for n=15). A basis-only preset pass manager (no backend/
    # coupling map, so no routing) does exactly that decomposition.
    pm = generate_preset_pass_manager(
        optimization_level=1, basis_gates=["rz", "sx", "x", "cx"])
    return StatevectorEstimator(), StatevectorSampler(), pm, None


def report_estimate(args, isa_ansatz, isa_meas, backend):
    """Print ISA-compatibility stats and a lower-bound QPU time estimate for
    running the full QAOA loop against --ibm/--fake, then let the caller
    exit without running the optimizer or submitting anything.

    Reaching this function already proves the circuit transpiles onto the
    target's basis gates and coupling map -- that "verification" happened in
    pm.run() (called before this, in main()) and would have raised there if
    the circuit didn't fit.
    """
    if backend is None:
        print("--estimate needs real device timing data: pass --ibm or "
              "--fake NAME", file=sys.stderr)
        return 2

    name = getattr(backend, "name", str(backend))
    ops = isa_ansatz.count_ops()
    two_qubit = sum(c for g, c in ops.items() if g in ("cx", "cz", "ecr"))
    print(f"\nvalidated against {name}'s ISA (transpiled without error): "
          f"{isa_ansatz.num_qubits} qubits, depth {isa_ansatz.depth()}, "
          f"{sum(ops.values())} gates {dict(ops)} ({two_qubit} two-qubit)")

    # estimate_duration walks the longest dependency path using the target's
    # per-instruction durations -- the same data source real Runtime jobs are
    # billed against, computed here entirely locally.
    eval_time = isa_ansatz.estimate_duration(backend.target, unit="s")
    sample_time = isa_meas.estimate_duration(backend.target, unit="s")
    shots = args.shots or 4096
    evaluations = args.restarts * args.maxiter
    active_time = eval_time * shots * evaluations + sample_time * shots

    print(f"gate time per cost-function circuit: {eval_time * 1e6:.1f} us")
    print(f"gate+readout time for the final sampling circuit: "
          f"{sample_time * 1e6:.1f} us")
    print(f"shots per circuit: {shots}")
    print(f"cost-function circuit evaluations: {evaluations} "
          f"({args.restarts} restart(s) x {args.maxiter} optimizer "
          "iterations) + 1 final sampling call")
    print(f"\nestimated ACTIVE QPU time: {active_time:.3g} s "
          f"({active_time / 60:.2f} min)")
    print(
        "\nThis is a lower bound on gate time only. It excludes IBM's "
        "per-shot reset/rep-delay (commonly ~100-300 us, often bigger than "
        "the gate time itself for a circuit this shallow), and it excludes "
        "queueing entirely. In this script each of the "
        f"{evaluations} classical-optimizer iterations is its own Runtime "
        "job; public-queue waits of seconds to tens of minutes per job are "
        "common, so wall-clock time can be orders of magnitude above the "
        "active-time figure above. For a real --ibm run, keep --maxiter and "
        "--restarts small, or tune angles on --fake first and use --ibm "
        "only for the final sampling call."
    )
    return 0


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("qubo", help="QUBO file from ./rbrp_ip --export-qubo")
    parser.add_argument("-p", "--reps", type=int, default=1,
                        help="QAOA depth (number of cost/mixer layers, default: 1)")
    parser.add_argument("--restarts", type=int, default=1,
                        help="random parameter initializations to try, "
                             "keeping the best (default: 1)")
    parser.add_argument("--optimizer", default="COBYLA",
                        help="scipy.optimize.minimize method (default: COBYLA)")
    parser.add_argument("--maxiter", type=int, default=200,
                        help="classical optimizer iteration budget (default: 200)")
    parser.add_argument("--shots", type=int, default=0,
                        help="shots for the final sampling step, and for the "
                             "cost estimator too if > 0 (default: 0 = exact "
                             "statevector, ignored for --fake/--ibm which "
                             "always use finite shots)")
    parser.add_argument("--fake", metavar="NAME",
                        help="noisy Aer sim using a qiskit_ibm_runtime "
                             "fake-backend noise model, e.g. FakeMelbourneV2")
    parser.add_argument("--ibm", metavar="NAME", nargs="?", const="",
                        help="run on real IBM hardware; omit NAME to pick "
                             "the least-busy device")
    parser.add_argument("--seed", type=int, default=0, help="RNG seed")
    parser.add_argument("--brute-force-limit", type=int, default=20,
                        help="compute the true optimum by brute force when "
                             "n is at most this (default: 20)")
    parser.add_argument("--estimate", action="store_true",
                        help="validate the circuit against --ibm/--fake's "
                             "ISA, print its stats and a lower-bound QPU "
                             "time estimate for the full run, then exit -- "
                             "no job is submitted and no optimizer runs")
    parser.add_argument("--init-params", metavar="B1,G1,B2,G2,...",
                        help="comma-separated QAOA angles to start from "
                             "(as printed at the end of a run), instead of "
                             "the first random restart")
    parser.add_argument("--no-optimize", action="store_true",
                        help="skip the classical optimization loop entirely "
                             "and sample directly with --init-params "
                             "(required). Use this to spend real --ibm time "
                             "only on the final sampling call, after tuning "
                             "angles on --fake or the default simulator")
    args = parser.parse_args()

    bqm, names = load_qubo(args.qubo)
    cost_op, offset, n = qubo_to_ising_operator(bqm)
    print(f"{n} qubits, {len(cost_op)} Pauli terms, QAOA depth p={args.reps}")

    ansatz = QAOAAnsatz(cost_operator=cost_op, reps=args.reps)

    init_params = None
    if args.init_params:
        try:
            init_params = np.array(
                [float(v) for v in args.init_params.replace(",", " ").split()])
        except ValueError:
            print(f"--init-params must be a list of numbers, got: "
                  f"{args.init_params!r}", file=sys.stderr)
            return 2
        if len(init_params) != ansatz.num_parameters:
            print(f"--init-params has {len(init_params)} values but this "
                  f"ansatz (p={args.reps}) needs {ansatz.num_parameters}",
                  file=sys.stderr)
            return 2

    if args.no_optimize and init_params is None:
        print("--no-optimize needs --init-params", file=sys.stderr)
        return 2

    try:
        estimator, sampler, pm, backend = build_backend(args)
    except AccountNotFoundError as e:
        print(f"\ncannot reach IBM Quantum: {e}\n"
              "save an account with QiskitRuntimeService.save_account(...) "
              "or set QISKIT_IBM_TOKEN, then retry.", file=sys.stderr)
        return 2

    isa_ansatz = pm.run(ansatz)
    layout = getattr(isa_ansatz, "layout", None)
    isa_cost_op = cost_op.apply_layout(layout) if layout is not None else cost_op

    isa_meas = isa_ansatz.copy()
    isa_meas.measure_all()

    if args.estimate:
        return report_estimate(args, isa_ansatz, isa_meas, backend)

    if args.no_optimize:
        best = SimpleNamespace(x=init_params)
        print("skipping optimization, using --init-params as given "
              "(no cost-function circuit evaluations, no jobs beyond the "
              "final sampling call below)")
    else:
        # StatevectorEstimator/AerEstimator take a target standard error, not
        # a shot count directly; 1/sqrt(shots) is the usual estimator-noise
        # scaling. Runtime EstimatorV2 (--ibm) always runs real shots
        # regardless.
        precision = 1.0 / np.sqrt(args.shots) if args.shots else None

        def expectation(params):
            pub = (isa_ansatz, [isa_cost_op], [params])
            result = estimator.run([pub], precision=precision).result()
            return float(result[0].data.evs[0])

        rng = np.random.default_rng(args.seed)
        best = None
        for r in range(args.restarts):
            if r == 0 and init_params is not None:
                x0 = init_params
            else:
                x0 = rng.uniform(0.0, np.pi, size=ansatz.num_parameters)
            res = minimize(expectation, x0, method=args.optimizer,
                           options={"maxiter": args.maxiter})
            print(f"  restart {r}: <H> = {res.fun + offset:.4f} "
                  f"({res.nfev} evals)")
            if best is None or res.fun < best.fun:
                best = res

        print(f"optimized <H_ising> + offset = {best.fun + offset:.4f}")

    params_str = ",".join(f"{v:.6f}" for v in best.x)
    print(f"parameters: {params_str}")
    if not args.no_optimize:
        print("  (rerun sampling only, e.g. on --ibm, with: "
              f"--init-params {params_str} --no-optimize)")

    shots = args.shots or 4096
    sample_result = sampler.run([(isa_meas, best.x)], shots=shots).result()
    counts = sample_result[0].data.meas.get_counts()

    scored = sorted(
        ((bitstring_to_sample(bs, n), cnt) for bs, cnt in counts.items()),
        key=lambda sc: bqm.energy(sc[0]))
    best_sample, best_count = scored[0]
    best_energy = bqm.energy(best_sample)
    print(f"\nbest of {len(counts)} distinct bitstrings ({shots} shots): "
          f"energy={best_energy:.4f}, seen {best_count}/{shots} times")

    opt = brute_force_optimum(bqm, n, args.brute_force_limit)
    if opt is not None:
        _, opt_energy = opt
        gap = best_energy - opt_energy  # >= 0: energy is being minimized
        print(f"true optimum (brute force): {opt_energy:.4f}  "
              f"(gap: {gap:.4f}"
              + (f", {100 * gap / abs(opt_energy):.1f}% of |optimum|"
                 if opt_energy else "")
              + ")")
        if gap == 0:
            print("QAOA found the optimum")

    selected = sorted((names[i] for i, v in best_sample.items() if v == 1),
                      key=lambda s: (block_of(s), s))
    print(f"\nselected sequence variables ({len(selected)}):")
    for name in selected:
        print(f"  {name}")

    from collections import Counter
    counts_by_block = Counter(block_of(s) for s in selected)
    bad = {b: c for b, c in counts_by_block.items() if c != 1}
    if bad:
        print(f"assignment constraint VIOLATED (block -> #selected): {bad}")
        return 1
    print("assignment constraint satisfied (exactly one sequence per block)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
