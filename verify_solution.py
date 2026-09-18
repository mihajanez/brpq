#!/usr/bin/env python3
"""Check a user- or quantum-computer-selected set of IP/QUBO variables for
feasibility and, if feasible, print the relocation diagrams and cost.

QUBOModel::export_qubo() (see qubomodel.cpp) exports the sequence variables
x(p,sq) that make up the RBRP IP/QUBO model, plus -- since this script was
added -- "# cost <i> <n>" and "# reloc <i> <period> <src> <dst>" comment
lines that record each variable's relocation path. That is enough to
replay any feasible combination of variables (one per blocking block) into
an actual move plan, without needing Gurobi or re-running the solver: this
script is a small, dependency-free Python port of Bay/Solution (bay.cpp,
solution.cpp) plus the "Caserta"-style .dat instance reader (instance.cpp).

A selection can come from:
  - explicit variable names/indices (--select),
  - a bitstring, e.g. sampled on IBM Quantum hardware via qaoa_qubo.py
    --ibm (--bitstring; same little-endian convention as
    qaoa_qubo.bitstring_to_sample: qubit/column 0 is the rightmost char),
  - or, if neither is given, an interactive prompt that lists every
    variable grouped by block and reads a selection from stdin.

Usage:
    .venv/bin/python verify_solution.py data03-03-13.dat problem.qubo
    .venv/bin/python verify_solution.py data03-03-13.dat problem.qubo \\
        --select x(4,3),x(7,0),x(2,1),x(5,0),x(8,9)
    .venv/bin/python verify_solution.py data03-03-13.dat problem.qubo \\
        --bitstring 0001000000000100100010000
    .venv/bin/python verify_solution.py data03-03-13.dat problem.qubo \\
        --bitstring 0001... --msb-first   # if your bitstring isn't Qiskit-ordered
"""
import argparse
import copy
import re
import sys
from collections import Counter


def block_of(name):
    """'x(4,2)' -> 4: the blocking-block priority a sequence variable
    belongs to. Every variable for one blocking block must sum to exactly
    1 (the assignment constraint), same convention as load_qubo.block_of."""
    inside = name[name.find("(") + 1:name.find(")")]
    return int(inside.split(",")[0])


def bitstring_to_sample(bitstring, n):
    """Qiskit bitstrings are little-endian (qubit 0 = rightmost char), the
    same convention qaoa_qubo.py's bitstring_to_sample() uses."""
    return {i: int(bitstring[-1 - i]) for i in range(n)}


# ---------------------------------------------------------------------------
# QUBO-file metadata (variable names, groups, costs, relocation paths)
# ---------------------------------------------------------------------------

class VariableInfo:
    __slots__ = ("index", "name", "priority", "cost", "relocations")

    def __init__(self, index, name):
        self.index = index
        self.name = name
        self.priority = None
        self.cost = None
        self.relocations = []


def load_variables(path):
    """Parse the "# var/# cost/# reloc" comment lines written by
    QUBOModel::export_qubo(). Returns a dict {index: VariableInfo}."""
    variables = {}

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) == 4 and parts[1] == "var":
                idx = int(parts[2])
                variables[idx] = VariableInfo(idx, parts[3])
            elif len(parts) == 4 and parts[1] == "cost":
                idx = int(parts[2])
                variables[idx].cost = int(parts[3])
                variables[idx].priority = block_of(variables[idx].name)
            elif len(parts) == 6 and parts[1] == "reloc":
                idx, period, src, dst = (int(x) for x in parts[2:])
                variables[idx].relocations.append((period, src, dst))

    if not variables:
        raise ValueError(f"{path}: no '# var' lines found; is this a QUBO file "
                         "exported by ./rbrp_ip --export-qubo?")

    # Variables with no "# cost"/"# reloc" lines aren't sequence variables:
    # they're the s(period,stack,k) capacity-penalty slack bits added by
    # build_capacity_penalty() (see qubomodel.cpp), which exist only to
    # encode a capacity constraint in QUBO form and don't correspond to a
    # blocking block's relocation choice. They're kept in the returned dict
    # (needed so --bitstring's length lines up with the file's column
    # count) but callers that group/select by block should skip them via
    # is_sequence_variable().
    return variables


def is_sequence_variable(v):
    return v.priority is not None


def is_complete_sequence(v):
    """A sequence variable's cost is remainingRelocations + len(relocations)
    - 1 (see Sequence::length() in sequence.hpp). remainingRelocations is 0
    only for a finished (NonBlocking-type) sequence, whose relocations list
    is the full, concrete move path; a "Blocking"-type sequence has
    remainingRelocations > 0 and its last relocation's destination is still
    the solver's -2 placeholder (see Bay.relocate above) -- more relocations
    remain to be *decided* by further search, not just replayed."""
    return v.cost == len(v.relocations) - 1


# ---------------------------------------------------------------------------
# Instance reader (Caserta-style .dat, the format this project's own
# data/*.dat files use -- see the input_data.size() == 2 branch of
# Instance::read in instance.cpp; the other legacy formats it also accepts
# are not supported here)
# ---------------------------------------------------------------------------

class Instance:
    def __init__(self):
        self.number_of_stacks = 0
        self.number_of_blocks = 0
        self.number_of_tiers = 0
        self.block = []  # priority -> original label
        self.bay = []    # bay[stack] = [priority, ...] bottom to top


def read_instance(path):
    instance = Instance()

    with open(path) as f:
        tokens_by_line = [line.split() for line in f]

    header = None
    header_line = 0
    for i, toks in enumerate(tokens_by_line):
        if len(toks) == 2 and all(t.lstrip("-").isdigit() for t in toks):
            header = [int(t) for t in toks]
            header_line = i
            break
    if header is None:
        raise ValueError(f"{path}: could not find a 'stacks blocks' header line")

    instance.number_of_stacks, instance.number_of_blocks = header

    rest = []
    for toks in tokens_by_line[header_line + 1:]:
        rest.extend(int(t) for t in toks)

    instance.bay = [[] for _ in range(instance.number_of_stacks)]
    pos = 0
    for s in range(instance.number_of_stacks):
        if pos >= len(rest):
            raise ValueError(f"{path}: insufficient block data")
        c = rest[pos]
        instance.bay[s] = rest[pos + 1: pos + 1 + c]
        pos += c + 1

    labels = sorted(b for stack in instance.bay for b in stack)
    rank = {label: i for i, label in enumerate(labels)}
    instance.block = labels
    instance.bay = [[rank[b] for b in stack] for stack in instance.bay]
    instance.number_of_tiers = instance.number_of_blocks

    return instance


# ---------------------------------------------------------------------------
# Bay / Solution (port of bay.cpp / solution.cpp)
# ---------------------------------------------------------------------------

class Slot:
    __slots__ = ("priority", "minimum_priority", "stacked")

    def __init__(self, priority, minimum_priority, stacked):
        self.priority = priority
        self.minimum_priority = minimum_priority
        self.stacked = stacked


class StackState:
    __slots__ = ("minimum_priority", "stacked", "height")

    def __init__(self, minimum_priority, stacked, height):
        self.minimum_priority = minimum_priority
        self.stacked = stacked
        self.height = height


class Bay:
    def __init__(self, instance):
        self.number_of_stacks = instance.number_of_stacks
        self.number_of_blocks = instance.number_of_blocks
        self.bay = []
        self.stack = []
        self.target_stack = -1
        self.target_tier = -1
        self.target_priority = 0

        for s in range(self.number_of_stacks):
            minimum_priority = self.number_of_blocks
            stacked = 0
            slots = [Slot(-1, minimum_priority, stacked)]

            for block in instance.bay[s]:
                if block < minimum_priority:
                    minimum_priority = block
                    stacked = 0
                else:
                    stacked += 1
                slots.append(Slot(block, minimum_priority, stacked))

            self.bay.append(slots)
            top = slots[-1]
            self.stack.append(StackState(top.minimum_priority, top.stacked,
                                         len(slots) - 1))

            if self.target_priority == self.stack[s].minimum_priority:
                self.target_stack = s
                self.target_tier = (self.stack[s].height
                                    - self.stack[s].stacked - 1)

    def set_target(self, target):
        self.target_priority = target
        self.target_stack = -1
        self.target_tier = -1
        for s in range(self.number_of_stacks):
            if target == self.stack[s].minimum_priority:
                self.target_stack = s
                self.target_tier = self.stack[s].height - self.stack[s].stacked - 1
                break

    def relocatable_block(self):
        if self.target_stack == -1 or self.stack[self.target_stack].stacked == 0:
            return -1
        return self.bay[self.target_stack][-1].priority

    def retrieve(self):
        if self.target_stack == -1 or self.stack[self.target_stack].stacked > 0:
            return -1
        self.bay[self.target_stack].pop()
        top = self.bay[self.target_stack][-1]
        st = self.stack[self.target_stack]
        st.stacked = top.stacked
        st.minimum_priority = top.minimum_priority
        st.height -= 1
        self.target_stack = self.target_tier = -1
        self.number_of_blocks -= 1
        return self.target_priority

    def relocate(self, dst):
        if dst < 0:
            # -2 is the C++ solver's "destination not yet decided" placeholder
            # for an incomplete (Blocking-type) sequence -- see
            # QUBOModel::expand_sequence()'s change_last_destination(-2) in
            # qubomodel.cpp. It should never reach here: callers must reject
            # such a selection before replaying it (see is_complete_sequence
            # in this file), since a negative index would otherwise silently
            # wrap around to an unrelated stack instead of failing loudly.
            raise ValueError(f"relocate() got an unresolved destination ({dst}); "
                             "this relocation belongs to an incomplete sequence")
        if self.target_stack == -1 or self.stack[self.target_stack].stacked == 0:
            return -1
        b = self.bay[self.target_stack].pop()
        bp = self.bay[dst][-1]
        st = self.stack[self.target_stack]
        st.stacked -= 1
        st.height -= 1

        if b.priority < bp.minimum_priority:
            b_min, b_stacked = b.priority, 0
            self.stack[dst].minimum_priority = b.priority
            self.stack[dst].stacked = 0
        else:
            b_min, b_stacked = bp.minimum_priority, bp.stacked + 1
            self.stack[dst].stacked += 1

        self.stack[dst].height += 1
        self.bay[dst].append(Slot(b.priority, b_min, b_stacked))
        return b.priority

    def format(self, instance):
        h = max((st.height for st in self.stack), default=1)
        h = max(h, 1)
        lines = []
        for t in range(h, 0, -1):
            row = f"{t:>3}:"
            for s in range(self.number_of_stacks):
                if self.stack[s].height >= t:
                    row += f"[{instance.block[self.bay[s][t].priority]:>3}]"
                else:
                    row += "     "
            lines.append(row)
        lines.append("   " + "".join(f"{s + 1:>5}" for s in range(self.number_of_stacks)))
        return "\n".join(lines)


class Relocation:
    __slots__ = ("priority", "src", "dst")

    def __init__(self, priority, src, dst):
        self.priority = priority
        self.src = src
        self.dst = dst


class Solution:
    """sol: {priority: [(period, src, dst), ...]} for every blocking block
    (blocks with nothing to relocate may be omitted)."""

    def __init__(self, bay, sol):
        self.bay = bay
        self.sequence = []
        self.feasible = True

        current = copy.deepcopy(bay)
        cursor = {b: 0 for b in sol}

        for target in range(bay.number_of_blocks):
            current.set_target(target)

            while True:
                b = current.relocatable_block()
                if b < 0:
                    break

                relocs = sol.get(b, [])
                idx = cursor.get(b, 0)
                if (idx >= len(relocs) or relocs[idx][0] != target
                        or relocs[idx][1] != current.target_stack):
                    self.sequence = []
                    self.feasible = False
                    break

                _, src, dst = relocs[idx]
                current.relocate(dst)
                self.sequence.append(Relocation(b, current.target_stack, dst))
                cursor[b] = idx + 1

            if not self.feasible:
                break

            current.retrieve()

        if bay.number_of_blocks == 0:
            self.sequence = []

    def number_of_relocations(self):
        return len(self.sequence)

    def print(self, instance, out=sys.stdout):
        out.write("--------\n")

        current = copy.deepcopy(self.bay)
        out.write("Initial configuration\n")
        out.write(current.format(instance) + "\n\n")

        i = 0
        target = 0
        current.set_target(target)

        while current.number_of_blocks > 0:
            c = 0
            while current.relocatable_block() < 0 and current.number_of_blocks > 0:
                current.retrieve()
                target += 1
                current.set_target(target)
                c += 1

            if c > 0:
                out.write("++++++++\n")
                out.write(f"Retrieve {c} block{'s' if c > 1 else ''}\n")
                if current.number_of_blocks == 0:
                    out.write("--------\n")
                    break
                out.write(current.format(instance) + "\n\n")

            while True:
                b = current.relocatable_block()
                if b < 0:
                    break
                r = self.sequence[i]
                if b != r.priority or current.target_stack != r.src:
                    raise RuntimeError(
                        "internal error: solution replay diverged from the "
                        "recorded relocation sequence")
                out.write("--------\n")
                out.write(f"Relocation {i + 1}: [{instance.block[r.priority]:>3}] "
                          f"{r.src + 1}->{r.dst + 1}\n")
                current.relocate(r.dst)
                i += 1
                out.write(current.format(instance) + "\n\n")


# ---------------------------------------------------------------------------
# Selection / feasibility / main
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[A-Za-z]\([0-9,]+\)|\d+")


def resolve_selection(text, variables):
    """text: variable names and/or indices, separated by commas, spaces, or
    both (names like 'x(4,3)' contain a comma themselves, so splitting on
    plain ',' would break them -- token boundaries are found by regex
    instead)."""
    by_name = {v.name: v.index for v in variables.values()}
    selected = set()
    for tok in _TOKEN_RE.findall(text):
        if tok in by_name:
            idx = by_name[tok]
        elif tok.isdigit() and int(tok) in variables:
            idx = int(tok)
        else:
            raise ValueError(f"unknown variable: {tok!r}")
        if not is_sequence_variable(variables[idx]):
            raise ValueError(
                f"{variables[idx].name} is not a sequence variable (it's a "
                "capacity-penalty slack bit, see build_capacity_penalty() in "
                "qubomodel.cpp) and has no relocations to select")
        selected.add(idx)
    return selected


def check_assignment_constraint(selected, variables):
    """Exactly one selected variable per blocking-block group. Returns a
    list of human-readable violations (empty if feasible)."""
    groups = sorted({v.priority for v in variables.values() if is_sequence_variable(v)})
    counts = Counter(variables[i].priority for i in selected)

    violations = []
    for g in groups:
        c = counts.get(g, 0)
        if c == 0:
            violations.append(f"block {g}: no sequence selected")
        elif c > 1:
            names = sorted(variables[i].name for i in selected if variables[i].priority == g)
            violations.append(f"block {g}: {c} sequences selected ({', '.join(names)})")
    return violations


def prompt_for_selection(variables):
    sequence_vars = [v for v in variables.values() if is_sequence_variable(v)]
    aux_count = len(variables) - len(sequence_vars)

    groups = {}
    for v in sorted(sequence_vars, key=lambda v: (v.priority, v.index)):
        groups.setdefault(v.priority, []).append(v)

    print(f"{len(sequence_vars)} IP sequence variables, {len(groups)} blocking blocks"
          + (f" ({aux_count} capacity-slack bits omitted)" if aux_count else "") + ":")
    for g, vs in groups.items():
        row = "  ".join(f"{v.name}(cost={v.cost})" for v in vs)
        print(f"  block {g}: {row}")

    print("\nEnter one variable per block (names or indices, space/comma separated):")
    try:
        return input("> ")
    except EOFError:
        raise ValueError("no selection given (stdin closed); "
                         "use --select or --bitstring instead") from None


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("instance", help="the .dat instance file solved to produce the QUBO")
    parser.add_argument("qubo", help="QUBO file from ./rbrp_ip --export-qubo")
    sel = parser.add_mutually_exclusive_group()
    sel.add_argument("--select", help="comma-separated variable names or indices, "
                                      "e.g. x(4,3),x(7,0)")
    sel.add_argument("--bitstring", help="a 0/1 string over all variables, e.g. sampled "
                                         "on IBM Quantum hardware (see qaoa_qubo.py --ibm)")
    parser.add_argument("--msb-first", action="store_true",
                        help="--bitstring's leftmost char is variable 0 (default: "
                             "rightmost, Qiskit's little-endian convention)")
    args = parser.parse_args()

    try:
        variables = load_variables(args.qubo)
        n = len(variables)

        if args.bitstring:
            bits = args.bitstring.strip()
            if len(bits) != n:
                parser.error(f"--bitstring has {len(bits)} characters, expected {n}")
            if args.msb_first:
                sample = {i: int(bits[i]) for i in range(n)}
            else:
                sample = bitstring_to_sample(bits, n)
            selected = {i for i, bit in sample.items() if bit == 1}
            aux_selected = {i for i in selected if not is_sequence_variable(variables[i])}
            if aux_selected:
                print(f"ignoring {len(aux_selected)} set capacity-slack bit(s) "
                     f"({', '.join(sorted(variables[i].name for i in aux_selected))}); "
                     "they don't affect which sequence is chosen for a block")
                selected -= aux_selected
        elif args.select:
            selected = resolve_selection(args.select, variables)
        else:
            selected = resolve_selection(prompt_for_selection(variables), variables)
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    violations = check_assignment_constraint(selected, variables)
    if violations:
        print("INFEASIBLE: assignment constraint violated")
        for v in violations:
            print(f"  {v}")
        return 1

    print(f"{len(selected)} IP variables selected:")
    expected_cost = 0
    sol = {}
    for i in sorted(selected):
        v = variables[i]
        print(f"  {v.name} = 1, cost = {v.cost}")
        expected_cost += v.cost
        sol[v.priority] = [(p, s, d) for p, s, d in v.relocations]
    print(f"Cost of the selected solution: {expected_cost}")

    incomplete = [variables[i] for i in sorted(selected) if not is_complete_sequence(variables[i])]
    if incomplete:
        print("\nINFEASIBLE: the following selected sequence(s) are incomplete -- "
              "the solver hadn't yet decided their remaining relocations when "
              "the QUBO was exported, so there's no move plan to replay:")
        for v in incomplete:
            still_needed = v.cost - (len(v.relocations) - 1)
            print(f"  {v.name}: {len(v.relocations) - 1} relocation(s) listed, "
                 f"{still_needed} more still undecided (cost {v.cost} total)")
        return 1

    try:
        instance = read_instance(args.instance)
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    bay = Bay(instance)
    solution = Solution(bay, sol)

    if (not solution.feasible
            or solution.number_of_relocations() != expected_cost):
        print("\nINFEASIBLE: the selected sequences don't combine into a valid "
              "relocation plan (a selected sequence may be an incomplete/"
              "provisional one from a mid-search branch, not a final one)")
        return 1

    print()
    solution.print(instance)
    print(f"\nCost: {solution.number_of_relocations()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
