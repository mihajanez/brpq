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

Usage:
    .venv/bin/python qaoa_qubo.py problem.qubo -p 1
    .venv/bin/python qaoa_qubo.py problem.qubo -p 2 --restarts 5 --shots 4096
    .venv/bin/python qaoa_qubo.py problem.qubo -p 1 --fake FakeMelbourneV2
    .venv/bin/python qaoa_qubo.py problem.qubo -p 1 --ibm
    .venv/bin/python qaoa_qubo.py problem.qubo -p 1 --ibm ibm_brisbane

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
    """Returns (estimator, sampler, transpile) for the chosen backend.

    transpile(ansatz) -> (isa_ansatz, layout_fn) where layout_fn(op) maps a
    Hamiltonian defined on logical qubits onto the transpiled circuit's
    physical layout (identity when no transpilation is needed).
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
        return estimator, sampler, pm

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
        return estimator, sampler, pm

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
    return StatevectorEstimator(), StatevectorSampler(), pm


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
    args = parser.parse_args()

    bqm, names = load_qubo(args.qubo)
    cost_op, offset, n = qubo_to_ising_operator(bqm)
    print(f"{n} qubits, {len(cost_op)} Pauli terms, QAOA depth p={args.reps}")

    ansatz = QAOAAnsatz(cost_operator=cost_op, reps=args.reps)
    try:
        estimator, sampler, pm = build_backend(args)
    except AccountNotFoundError as e:
        print(f"\ncannot reach IBM Quantum: {e}\n"
              "save an account with QiskitRuntimeService.save_account(...) "
              "or set QISKIT_IBM_TOKEN, then retry.", file=sys.stderr)
        return 2

    isa_ansatz = pm.run(ansatz)
    layout = getattr(isa_ansatz, "layout", None)
    isa_cost_op = cost_op.apply_layout(layout) if layout is not None else cost_op

    # StatevectorEstimator/AerEstimator take a target standard error, not a
    # shot count directly; 1/sqrt(shots) is the usual estimator-noise scaling.
    # Runtime EstimatorV2 (--ibm) always runs real shots regardless.
    precision = 1.0 / np.sqrt(args.shots) if args.shots else None

    def expectation(params):
        pub = (isa_ansatz, [isa_cost_op], [params])
        result = estimator.run([pub], precision=precision).result()
        return float(result[0].data.evs[0])

    rng = np.random.default_rng(args.seed)
    best = None
    for r in range(args.restarts):
        x0 = rng.uniform(0.0, np.pi, size=ansatz.num_parameters)
        res = minimize(expectation, x0, method=args.optimizer,
                       options={"maxiter": args.maxiter})
        print(f"  restart {r}: <H> = {res.fun + offset:.4f} "
              f"({res.nfev} evals)")
        if best is None or res.fun < best.fun:
            best = res

    print(f"optimized <H_ising> + offset = {best.fun + offset:.4f}")

    isa_meas = isa_ansatz.copy()
    isa_meas.measure_all()
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
