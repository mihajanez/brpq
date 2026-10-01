#!/usr/bin/env python3
"""Re-score saved QAOA samples against a QUBO, offline (no QPU access needed).

Inputs (one of):
  - an IBM Quantum job downloaded from the IBM Quantum Platform:
        --ibm-job job-<id>-info.json job-<id>-result.json
    The info file holds the transpiled circuit, whose layout says which
    physical qubit carried which QUBO variable; the result file holds the
    counts over the device's classical bits.
  - a run saved by qaoa_qubo.py --save (counts already over the QUBO
    variables):
        --run run.json

For each sample the script decodes the QUBO variables, then reports the mean
energy, the one-hot feasible fraction, how often the optimum was hit, the most
frequent and the best bitstrings, and -- for n <= --brute-force-limit -- the
same figures for uniform random sampling, the baseline any useful QAOA run has
to beat.

Sign convention: since the fix in qaoa_qubo.qubo_to_ising_operator(), a
measured bit is the QUBO variable (x = bit). Circuits built by earlier versions
of qaoa_qubo.py used the opposite sign on the linear terms, so their samples
decode as complements; pass --old-sign for those (e.g. job
damhugf8gn2s739lf5e0 from 18 Sep 2026).

Usage:
    .venv/bin/python rescore.py problem.qubo --ibm-job job-X-info.json job-X-result.json
    .venv/bin/python rescore.py problem.qubo --ibm-job ... --old-sign
    .venv/bin/python rescore.py problem.qubo --run run.json
    .venv/bin/python rescore.py problem.qubo --run run.json \\
        --instance data03-03-13.dat          # replay the best feasible sample
    .venv/bin/python rescore.py problem.qubo --ibm-job ... --save rescored.json
"""
import argparse
import json
import subprocess
import sys
from collections import Counter
from itertools import product
from pathlib import Path

from load_qubo import block_of, load_qubo


def one_hot_groups(names):
    groups = {}
    for i, name in names.items():
        if name.startswith("x("):
            groups.setdefault(block_of(name), []).append(i)
    return groups


def is_feasible(x, groups):
    return all(sum(x[i] for i in idx) == 1 for idx in groups.values())


def logical_bits_from_ibm_job(info_path, result_path, n):
    """Counts over the QUBO variables from a downloaded IBM Runtime Sampler
    job. Returns (Counter{tuple(x_0..x_{n-1}): count}, description)."""
    from qiskit_ibm_runtime.json import RuntimeDecoder

    with open(info_path) as f:
        info = json.load(f, cls=RuntimeDecoder)
    with open(result_path) as f:
        result = json.load(f, cls=RuntimeDecoder)

    circuit = info["params"]["pubs"][0][0]
    pub = result[0]
    field = getattr(pub.data, next(iter(pub.data.keys())))
    raw_counts = field.get_counts()
    num_bits = field.num_bits

    if num_bits == n:
        # Measured on the logical circuit (qaoa_qubo.py after the fix):
        # classical bit i is QUBO variable i.
        clbit_of = list(range(n))
        how = f"{n} measured bits = QUBO variables"
    else:
        layout = circuit.layout
        if layout is None:
            raise ValueError("job circuit has no layout; cannot map "
                             f"{num_bits} measured bits to {n} variables")
        # physical qubit each logical qubit ends on, after SWAP routing
        physical = layout.final_index_layout()[:n]
        measured = {}
        for inst in circuit.data:
            if inst.operation.name == "measure":
                q = circuit.find_bit(inst.qubits[0]).index
                c = circuit.find_bit(inst.clbits[0]).index
                measured[q] = c
        clbit_of = [measured[q] for q in physical]
        how = (f"{num_bits} measured bits, QUBO variables read from "
               f"physical qubits {physical}")

    counts = Counter()
    for bitstring, cnt in raw_counts.items():
        counts[tuple(int(bitstring[-1 - c]) for c in clbit_of)] += cnt
    meta = {"backend": info.get("backend"), "job_id": info.get("id"),
            "created": info.get("created"),
            "parameters": [float(v) for v in info["params"]["pubs"][0][1]]
            if len(info["params"]["pubs"][0]) > 1 else None}
    return counts, how, meta


def logical_bits_from_run(path, n):
    with open(path) as f:
        run = json.load(f)
    counts = Counter()
    for bitstring, cnt in run["counts"].items():
        if len(bitstring) != n:
            raise ValueError(f"run has {len(bitstring)}-bit samples, QUBO has "
                             f"{n} variables")
        counts[tuple(int(bitstring[-1 - i]) for i in range(n))] += cnt
    meta = {k: run.get(k) for k in ("backend", "job_id", "parameters")}
    return counts, f"{n}-bit samples from {path}", meta


def to_bitstring(x):
    """Qiskit order: variable 0 is the rightmost character."""
    return "".join(str(b) for b in reversed(x))


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("qubo", help="the QUBO file the samples were drawn for")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--ibm-job", nargs=2, metavar=("INFO", "RESULT"),
                     help="job-<id>-info.json and job-<id>-result.json")
    src.add_argument("--run", metavar="JSON", help="a qaoa_qubo.py --save file")
    parser.add_argument("--old-sign", action="store_true",
                        help="samples come from a circuit built before the "
                             "Ising sign fix: decode bits as complements")
    parser.add_argument("--brute-force-limit", type=int, default=22,
                        help="exact optimum and uniform baseline up to this "
                             "many variables (default: 22)")
    parser.add_argument("--top", type=int, default=5,
                        help="how many most-frequent bitstrings to list")
    parser.add_argument("--instance", metavar="DAT",
                        help="replay the best feasible sample with "
                             "verify_solution.py on this instance")
    parser.add_argument("--save", metavar="JSON", help="write the scores here")
    args = parser.parse_args()

    bqm, names = load_qubo(args.qubo)
    n = len(bqm.variables)
    groups = one_hot_groups(names)

    try:
        if args.ibm_job:
            counts, how, meta = logical_bits_from_ibm_job(*args.ibm_job, n)
        else:
            counts, how, meta = logical_bits_from_run(args.run, n)
    except (OSError, KeyError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.old_sign:
        counts = Counter({tuple(1 - b for b in x): c for x, c in counts.items()})

    shots = sum(counts.values())
    energy = {x: bqm.energy(dict(enumerate(x))) for x in counts}
    print(f"{n} QUBO variables, {len(groups)} one-hot groups; {shots} shots, "
          f"{len(counts)} distinct samples")
    print(f"decoding: {how}" + (" (complemented, --old-sign)" if args.old_sign else ""))
    if meta.get("backend"):
        print(f"backend: {meta['backend']}  job: {meta.get('job_id')}  "
              f"parameters: {meta.get('parameters')}")

    mean_energy = sum(energy[x] * c for x, c in counts.items()) / shots
    feasible = sum(c for x, c in counts.items() if is_feasible(x, groups))
    best_x = min(counts, key=lambda x: energy[x])
    feasible_samples = [x for x in counts if is_feasible(x, groups)]
    best_feasible = (min(feasible_samples, key=lambda x: energy[x])
                     if feasible_samples else None)

    report = {
        "qubo": args.qubo, "decoding": how, "old_sign": args.old_sign,
        **{k: v for k, v in meta.items() if v is not None},
        "shots": shots, "distinct": len(counts),
        "mean_energy": mean_energy, "feasible_fraction": feasible / shots,
        "best_energy": energy[best_x], "best_bitstring": to_bitstring(best_x),
        "best_feasible_bitstring": (to_bitstring(best_feasible)
                                    if best_feasible else None),
    }

    print(f"\nsamples: mean energy {mean_energy:.3f}, feasible "
          f"{feasible}/{shots} ({100 * feasible / shots:.2f}%), best energy "
          f"{energy[best_x]:.3f}")

    if n <= args.brute_force_limit:
        total, n_feasible, n_opt, opt = 0.0, 0, 0, float("inf")
        for bits in product((0, 1), repeat=n):
            e = bqm.energy(dict(enumerate(bits)))
            total += e
            n_feasible += is_feasible(bits, groups)
            if e < opt:
                opt, n_opt = e, 1
            elif e == opt:
                n_opt += 1
        hits = sum(c for x, c in counts.items() if energy[x] == opt)
        uniform = {"mean_energy": total / 2 ** n,
                   "feasible_fraction": n_feasible / 2 ** n,
                   "optimum_fraction": n_opt / 2 ** n}
        report.update(optimum_energy=opt, optimum_hits=hits,
                      optimum_fraction=hits / shots, uniform=uniform)
        print(f"optimum {opt:.3f}: hit {hits}/{shots} "
              f"({100 * hits / shots:.3f}%)")
        print(f"uniform random: mean energy {uniform['mean_energy']:.3f}, "
              f"feasible {100 * uniform['feasible_fraction']:.2f}%, optimum "
              f"{100 * uniform['optimum_fraction']:.3f}%")
        if mean_energy >= uniform["mean_energy"]:
            print("  -> no better than random sampling on mean energy")

    print(f"\nmost frequent samples (bitstring, count, energy, feasible):")
    for x, c in counts.most_common(args.top):
        print(f"  {to_bitstring(x)}  {c:5d}  {energy[x]:8.3f}  "
              f"{'yes' if is_feasible(x, groups) else 'no'}")

    if best_feasible is not None:
        chosen = sorted((names[i] for i, b in enumerate(best_feasible) if b),
                        key=lambda s: (block_of(s), s))
        print(f"\nbest feasible sample: energy {energy[best_feasible]:.3f}, "
              f"{', '.join(chosen)}")
        if args.instance:
            script = Path(__file__).with_name("verify_solution.py")
            print(f"\nreplaying with {script.name}:")
            subprocess.run([sys.executable, str(script), args.instance,
                            args.qubo, "--bitstring",
                            to_bitstring(best_feasible)])
    else:
        print("\nno feasible sample")

    if args.save:
        with open(args.save, "w") as f:
            json.dump(report, f, indent=1)
        print(f"\nscores saved to {args.save}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
