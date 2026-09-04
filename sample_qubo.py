#!/usr/bin/env python3
"""Run a QUBO file (from ./rbrp_ip --export-qubo) on a D-Wave sampler.

Prerequisites:
  - a D-Wave Leap account and API token, configured via either
        export DWAVE_API_TOKEN=...
    or
        .venv/bin/dwave config create
  - the Ocean client:
        uv pip install --python .venv/bin/python dwave-system

Usage:
    .venv/bin/python sample_qubo.py problem.qubo                # pure QPU
    .venv/bin/python sample_qubo.py problem.qubo --hybrid       # Leap hybrid
    .venv/bin/python sample_qubo.py problem.qubo --reads 2000 --anneal-time 40
    .venv/bin/python sample_qubo.py problem.qubo --chain-strength 32

Notes on interpreting the result (see qubomodel.cpp):
  - capture_qubo() drops the objective's constant term, so the reported
    energy is shifted by that constant; the argmin (which variables are 1)
    is unaffected.
  - the file carries variable *names* like x(4,2) (blocking block of
    priority 4, its sequence #2), not the relocations that sequence
    encodes -- turning a sample into an actual plan needs the solver's
    sequence list.
"""
import argparse
import sys
from collections import Counter

from dwave.cloud.exceptions import (
    ConfigFileError,
    SolverAuthenticationError,
    SolverNotFoundError,
)

from load_qubo import block_of, load_qubo


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("qubo", help="QUBO file from ./rbrp_ip --export-qubo")
    parser.add_argument("--hybrid", action="store_true",
                        help="use the Leap hybrid BQM solver instead of the QPU")
    parser.add_argument("--reads", type=int, default=1000,
                        help="num_reads for the QPU (default: 1000)")
    parser.add_argument("--anneal-time", type=float, default=20.0,
                        help="annealing_time in microseconds (default: 20)")
    parser.add_argument("--chain-strength", type=float, default=None,
                        help="fixed chain strength; default lets Ocean pick "
                             "(uniform torque compensation)")
    parser.add_argument("--label", default="RBRP QUBO",
                        help="problem label shown in the Leap dashboard")
    args = parser.parse_args()

    bqm, names = load_qubo(args.qubo)
    print(f"{len(bqm.variables)} variables, "
          f"{len(bqm.quadratic)} quadratic terms")

    try:
        if args.hybrid:
            from dwave.system import LeapHybridSampler

            sampler = LeapHybridSampler()
            print(f"solver: {sampler.solver.id}")
            sampleset = sampler.sample(bqm, label=args.label)
        else:
            from dwave.system import DWaveSampler, EmbeddingComposite

            qpu = DWaveSampler()
            print(f"solver: {qpu.properties.get('chip_id', qpu.solver.id)}")
            sample_kwargs = dict(
                num_reads=args.reads,
                annealing_time=args.anneal_time,
                label=args.label,
            )
            if args.chain_strength is not None:
                sample_kwargs["chain_strength"] = args.chain_strength
            sampleset = EmbeddingComposite(qpu).sample(bqm, **sample_kwargs)
    except (ValueError, ConfigFileError, SolverAuthenticationError,
            SolverNotFoundError) as e:
        print(f"\ncannot reach a D-Wave solver: {e}\n"
              "set a token with `export DWAVE_API_TOKEN=...` or "
              "`.venv/bin/dwave config create`, then retry.", file=sys.stderr)
        return 2

    best = sampleset.first
    print(f"energy: {best.energy}  (shifted by the dropped QUBO constant)")
    cbf = best.chain_break_fraction if hasattr(best, "chain_break_fraction") else None
    if cbf is not None:
        print(f"chain break fraction: {cbf}")
        if cbf > 0.05:
            print("  -> high; retry with a larger --chain-strength")
    occurrences = getattr(best, "num_occurrences", None)
    if occurrences is not None:
        print(f"num_occurrences: {occurrences} / {args.reads if not args.hybrid else 1}")

    selected = sorted((names[i] for i, v in best.sample.items() if v == 1),
                      key=lambda s: (block_of(s), s))
    print(f"selected sequence variables ({len(selected)}):")
    for name in selected:
        print(f"  {name}")

    counts = Counter(block_of(s) for s in selected)
    bad = {b: c for b, c in counts.items() if c != 1}
    if bad:
        print(f"assignment constraint VIOLATED (block -> #selected): {bad}")
        return 1
    print("assignment constraint satisfied (exactly one sequence per block)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
