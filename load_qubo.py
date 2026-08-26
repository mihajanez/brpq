#!/usr/bin/env python3
"""Load a QUBO file produced by QUBOModel::export_qubo (see qubomodel.cpp)
into a dimod.BinaryQuadraticModel, ready to hand to a D-Wave sampler.

Usage:
    python3 load_qubo.py problem.qubo

The file format is a plain-text upper-triangular coefficient dump:
  - lines starting with '#' are comments (including a "# var <i> <name>"
    line per variable, giving the human-readable sequence-variable name
    for each column index)
  - all other lines are "<i> <j> <coefficient>" triples, i <= j, where
    the diagonal (i == j) is the linear bias and off-diagonal is the
    quadratic bias between variables i and j.
"""
import sys

import dimod


def load_qubo(path):
    Q = {}
    names = {}

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                parts = line.split()
                if len(parts) == 4 and parts[1] == "var":
                    names[int(parts[2])] = parts[3]
                continue
            i, j, coeff = line.split()
            Q[(int(i), int(j))] = float(coeff)

    bqm = dimod.BinaryQuadraticModel.from_qubo(Q)
    return bqm, names


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <problem.qubo>", file=sys.stderr)
        sys.exit(1)

    bqm, names = load_qubo(sys.argv[1])
    print(f"{len(bqm.variables)} variables, "
          f"{len(bqm.quadratic)} quadratic terms")
    print("variable names:", names)
