#!/usr/bin/env python3
"""Load a QUBO file produced by QUBOModel::export_qubo (see qubomodel.cpp)
into a dimod.BinaryQuadraticModel, ready to hand to a D-Wave sampler.

Usage:
    python3 load_qubo.py problem.qubo              # summary only
    python3 load_qubo.py problem.qubo --show       # print the dense QUBO matrix
    python3 load_qubo.py problem.qubo --plot       # save a heatmap to qubo.png
    python3 load_qubo.py problem.qubo --plot Q.png # ... to a chosen path

The file format is a plain-text upper-triangular coefficient dump:
  - lines starting with '#' are comments (including a "# var <i> <name>"
    line per variable, giving the human-readable sequence-variable name
    for each column index)
  - all other lines are "<i> <j> <coefficient>" triples, i <= j, where
    the diagonal (i == j) is the linear bias and off-diagonal is the
    quadratic bias between variables i and j.
"""
import argparse

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


def block_of(name):
    """'x(4,2)' -> '4': the blocking-block priority a sequence variable belongs
    to. Every sequence variable for one blocking block must sum to exactly 1
    (the assignment constraint), so this is the grouping key for the
    feasibility check in the sampler/QAOA scripts."""
    inside = name[name.find("(") + 1:name.find(")")]
    return inside.split(",")[0]


def dense_matrix(bqm, n):
    """Upper-triangular QUBO matrix as a NumPy array (rows/cols = column index).

    Diagonal holds the linear bias, [i, j] with i < j holds the quadratic
    bias, matching the on-disk convention.
    """
    import numpy as np

    Q = np.zeros((n, n))
    for i, bias in bqm.linear.items():
        Q[i, i] = bias
    for (a, b), bias in bqm.quadratic.items():
        Q[min(a, b), max(a, b)] = bias
    return Q


def print_matrix(Q, names):
    import numpy as np

    n = Q.shape[0]
    labels = [names.get(i, str(i)) for i in range(n)]
    width = max((len(s) for s in labels), default=1)
    width = max(width, 8)

    with np.printoptions(linewidth=200, precision=3, suppress=True):
        print("columns:")
        for i, name in enumerate(labels):
            print(f"  {i:>3}  {name}")
        print()
        header = " " * (width + 1) + "".join(f"{lab:>{width + 1}}" for lab in labels)
        print(header)
        for i in range(n):
            row = "".join(f"{Q[i, j]:>{width + 1}.3g}" for j in range(n))
            print(f"{labels[i]:>{width}} {row}")


def plot_matrix(Q, names, out_path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    n = Q.shape[0]
    labels = [names.get(i, str(i)) for i in range(n)]
    span = float(np.abs(Q).max()) or 1.0

    fig, ax = plt.subplots(figsize=(max(6, 0.5 * n), max(5, 0.5 * n)))
    im = ax.imshow(Q, cmap="RdBu", vmin=-span, vmax=span)
    fig.colorbar(im, ax=ax, label="bias")
    ax.set_xticks(range(n))
    ax.set_yticks(range(n))
    ax.set_xticklabels(labels, rotation=90, fontsize=8)
    ax.set_yticklabels(labels, fontsize=8)
    ax.set_title("QUBO matrix (diagonal = linear bias)")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    print(f"heatmap written to {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("qubo", help="QUBO file from ./rbrp_ip --export-qubo")
    parser.add_argument("--show", action="store_true",
                        help="print the dense QUBO matrix to stdout")
    parser.add_argument("--plot", nargs="?", const="qubo.png", metavar="PNG",
                        help="save a heatmap of the QUBO matrix (default: qubo.png)")
    args = parser.parse_args()

    bqm, names = load_qubo(args.qubo)
    print(f"{len(bqm.variables)} variables, "
          f"{len(bqm.quadratic)} quadratic terms")

    if args.show or args.plot is not None:
        n = 1 + max([*bqm.variables, *names], default=-1)
        Q = dense_matrix(bqm, n)
        if args.show:
            print_matrix(Q, names)
        if args.plot is not None:
            plot_matrix(Q, names, args.plot)
    else:
        print("variable names:", names)
