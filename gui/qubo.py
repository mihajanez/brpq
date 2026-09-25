"""Read a QUBO file exported by QUBOModel::export_qubo() and turn it into
something the GUI can draw and explain.

The file is the upper-triangular coefficient dump described in load_qubo.py,
plus the "# var / # cost / # reloc" comment lines that tie each column to a
sequence variable of the IP model: which blocking block it belongs to, how many
relocations selecting it costs, and the concrete moves it stands for. That is
enough to score any selection of columns and to replay a feasible one into an
actual move plan -- the same thing verify_solution.py does from the command line.
"""
import itertools
import os
import re

from .brp_instance import replay

NAME_RE = re.compile(r"^([a-zA-Z]+)\((.*)\)$")
TOKEN_RE = re.compile(r"[a-zA-Z]+\([^)]*\)|\d+")


class QuboVariable:
    __slots__ = ("index", "name", "kind", "priority", "cost", "relocations", "linear")

    def __init__(self, index, name):
        self.index = index
        self.name = name
        self.kind = "slack" if name.startswith("s(") else "sequence"
        self.priority = None      # blocking block this column belongs to
        self.cost = None          # relocations charged for selecting it
        self.relocations = []     # [(period, src, dst)], dst -1 = leaves the bay
        self.linear = 0.0

    @property
    def complete(self):
        """A sequence whose moves are all decided. Sequence::length() charges
        remainingRelocations + len(relocations) - 1, so cost == len - 1 means
        nothing is left to decide; a provisional one cannot be replayed (see
        is_complete_sequence in verify_solution.py)."""
        if self.kind != "sequence" or self.cost is None:
            return None
        return self.cost == len(self.relocations) - 1

    def as_dict(self):
        return {
            "index": self.index, "name": self.name, "kind": self.kind,
            "priority": self.priority, "cost": self.cost, "linear": self.linear,
            "complete": self.complete,
            "relocations": [{"period": p, "src": s, "dst": d}
                            for p, s, d in self.relocations],
        }


def load(path):
    """Parse a .qubo file into (variables, terms) where terms is {(i, j): coef}."""
    variables = {}
    terms = {}
    declared = None

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                parts = line.split()
                if len(parts) == 4 and parts[1] == "var":
                    idx = int(parts[2])
                    variables[idx] = QuboVariable(idx, parts[3])
                elif len(parts) == 4 and parts[1] == "cost":
                    var = variables.get(int(parts[2]))
                    if var is not None:
                        var.cost = int(parts[3])
                        var.priority = priority_of(var.name)
                elif len(parts) == 6 and parts[1] == "reloc":
                    var = variables.get(int(parts[2]))
                    if var is not None:
                        var.relocations.append((int(parts[3]), int(parts[4]),
                                                int(parts[5])))
                elif len(parts) == 3 and parts[1] == "variables:":
                    declared = int(parts[2])
                continue
            i, j, coefficient = line.split()
            i, j, coefficient = int(i), int(j), float(coefficient)
            terms[(min(i, j), max(i, j))] = coefficient

    for index in set(i for pair in terms for i in pair):
        variables.setdefault(index, QuboVariable(index, str(index)))
    for (i, j), coefficient in terms.items():
        if i == j:
            variables[i].linear = coefficient
    if declared is not None and declared != len(variables):
        # the header count and the columns disagree; the columns win
        pass
    return variables, terms


def read_offset(path):
    """The "# offset" line: the constant part of the model's objective, which a
    bare coefficient list cannot carry. energy(file) + offset = objective."""
    with open(path) as f:
        for line in f:
            if not line.startswith("#"):
                break
            parts = line.split()
            if len(parts) == 3 and parts[1] == "offset":
                try:
                    return float(parts[2])
                except ValueError:
                    return 0.0
    return 0.0


def priority_of(name):
    """'x(4,3)' -> 4: the blocking block whose sequences the column belongs to."""
    match = NAME_RE.match(name)
    if not match:
        return None
    try:
        return int(match.group(2).split(",")[0])
    except ValueError:
        return None


def energy(terms, selected):
    """QUBO energy of a 0/1 assignment given as a set of selected columns."""
    total = 0.0
    for (i, j), coefficient in terms.items():
        if i in selected and j in selected:
            total += coefficient
    return total


def groups(variables):
    """Sequence columns grouped by blocking block: exactly one must be picked."""
    by_priority = {}
    for var in variables.values():
        if var.kind == "sequence" and var.priority is not None:
            by_priority.setdefault(var.priority, []).append(var)
    return {p: sorted(v, key=lambda x: x.index) for p, v in sorted(by_priority.items())}


def cheapest_selection(variables):
    """One column per blocking block, always the fewest relocations."""
    return {min(members, key=lambda v: (v.cost if v.cost is not None else 99, v.index)).index
            for members in groups(variables).values()}


def relax_slacks(terms, variables, selected, rounds=6):
    """With the sequence columns fixed, hunt for the slack assignment that
    minimises the energy by single flips. The penalty terms are what the slack
    columns exist for, so leaving them all at 0 usually overstates the energy."""
    slots = [v.index for v in variables.values() if v.kind == "slack"]
    if not slots:
        return set(selected), energy(terms, selected)
    current = set(selected) - set(slots)
    best = energy(terms, current)
    neighbours = {}
    for (i, j), coefficient in terms.items():
        if i != j:
            neighbours.setdefault(i, []).append((j, coefficient))
            neighbours.setdefault(j, []).append((i, coefficient))

    for _ in range(rounds):
        improved = False
        for slot in slots:
            delta = variables[slot].linear if slot not in current else -variables[slot].linear
            for other, coefficient in neighbours.get(slot, ()):
                if other in current and other != slot:
                    delta += coefficient if slot not in current else -coefficient
            if delta < -1e-9:
                current.symmetric_difference_update({slot})
                best += delta
                improved = True
        if not improved:
            break
    return current, best


def resolve_selection(text, variables):
    """Accept column names ('x(4,3)'), plain indices, or a mix."""
    by_name = {v.name: v.index for v in variables.values()}
    selected, unknown = set(), []
    for token in TOKEN_RE.findall(text or ""):
        if token in by_name:
            selected.add(by_name[token])
        elif token.isdigit() and int(token) in variables:
            selected.add(int(token))
        else:
            unknown.append(token)
    return selected, unknown


def selection_from_bitstring(bits, variables, msb_first=False):
    """Qiskit hands back little-endian bitstrings (column 0 = rightmost char),
    the convention qaoa_qubo.py and verify_solution.py both use."""
    bits = re.sub(r"[^01]", "", bits or "")
    n = len(variables)
    if len(bits) < n:
        raise ValueError(f"bitstring has {len(bits)} bits, the QUBO has {n} columns")
    if msb_first:
        return {i for i in range(n) if bits[i] == "1"}
    return {i for i in range(n) if bits[-1 - i] == "1"}


def check_groups(variables, selected):
    """One column per blocking block, no more, no less."""
    violations = []
    for priority, members in groups(variables).items():
        picked = [v for v in members if v.index in selected]
        if not picked:
            violations.append(f"block {priority}: nothing selected")
        elif len(picked) > 1:
            violations.append(f"block {priority}: {len(picked)} columns selected ("
                              + ", ".join(v.name for v in picked) + ")")
    return violations


def move_plan(instance, variables, selected, max_height=None):
    """Replay the selected sequences into a move plan.

    A sequence variable records its moves as (period, src, dst): `period` is the
    retrieval the move belongs to (the rank of the block being dug out) and the
    order *within* a period follows the bay, not the file -- whichever block is
    on top of the target stack moves next. That is how Solution in
    verify_solution.py walks a selection, and it is what this mirrors."""
    schedule = {}
    provisional = []
    for index in sorted(selected):
        var = variables[index]
        if var.kind != "sequence":
            continue
        if var.complete is False:
            provisional.append(var.name)
        schedule[var.priority] = sorted(var.relocations, key=lambda r: r[0])
    if provisional:
        return None, ("selection includes provisional sequence(s) "
                      + ", ".join(sorted(provisional))
                      + " whose moves are not fully decided, so no plan can be replayed")

    labels = sorted(b for stack in instance.bay for b in stack)
    rank = {label: i for i, label in enumerate(labels)}
    bay = [list(stack) for stack in instance.bay]
    remaining = list(labels)
    cursor = {priority: 0 for priority in schedule}
    relocations = []

    while remaining:
        target = remaining[0]
        stack = next(i for i, s in enumerate(bay) if target in s)
        if bay[stack][-1] == target:
            bay[stack].pop()
            remaining.pop(0)
            continue
        block = bay[stack][-1]
        priority = rank[block]
        moves = schedule.get(priority)
        index = cursor.get(priority, 0)
        if not moves or index >= len(moves):
            return None, (f"block {block} sits on target {target} but its sequence "
                          "has no move left for it")
        period, src, dst = moves[index]
        if period != rank[target] or src != stack:
            return None, (f"the sequence for block {block} expects to move in "
                          f"retrieval {period + 1} from stack {src + 1}, but it is "
                          f"needed now in retrieval {rank[target] + 1} from stack "
                          f"{stack + 1}")
        if dst < 0:
            return None, f"block {block} has no destination recorded for this move"
        if max_height and len(bay[dst]) + 1 > max_height:
            return None, (f"moving block {block} onto stack {dst + 1} would make it "
                          f"{len(bay[dst]) + 1} tiers high, over the limit of {max_height}")
        cursor[priority] = index + 1
        bay[stack].pop()
        bay[dst].append(block)
        relocations.append((block, stack + 1, dst + 1))

    steps, error = replay(instance, relocations)
    if error:
        return None, error
    return {"relocations": relocations, "steps": steps}, None


def search_selections(instance, variables, terms, limit=200000, max_height=None):
    """Walk every one-per-blocking-block selection and keep the cheapest that
    actually replays into a valid plan. The selections are exactly the
    assignments the one-hot penalty allows, so this is the QUBO's own feasible
    set -- small enough to enumerate on the instances this model is used on."""
    grouped = groups(variables)
    sizes = [len(v) for v in grouped.values()]
    total = 1
    for size in sizes:
        total *= size
    if total > limit:
        return {"capped": True, "total": total, "limit": limit}

    best = None
    replayable = 0
    for combination in itertools.product(*grouped.values()):
        selection = {v.index for v in combination}
        plan, error = move_plan(instance, variables, selection, max_height)
        if error or plan is None:
            continue
        replayable += 1
        moves = len(plan["relocations"])
        if best is None or moves < best["cost"]:
            best = {"selection": sorted(selection), "cost": moves,
                    "names": sorted(variables[i].name for i in selection)}
    result = {"capped": False, "total": total, "replayable": replayable,
              "best": best}
    if best:
        full, relaxed = relax_slacks(terms, variables, set(best["selection"]))
        result["bestEnergy"] = relaxed
        result["bestEnergyRaw"] = energy(terms, set(best["selection"]))
    return result


def instance_matches(instance, variables):
    """The QUBO records blocking blocks by their rank in the instance, so a
    mismatch means the file was exported from a different test case."""
    labels = sorted(b for stack in instance.bay for b in stack)
    rank = {label: i for i, label in enumerate(labels)}
    blocking = set()
    for stack in instance.bay:
        lowest = None
        for block in stack:
            if lowest is not None and block > lowest:
                blocking.add(rank[block])
            lowest = block if lowest is None else min(lowest, block)
    return blocking == set(groups(variables).keys()), sorted(blocking)


def summarize(path, variables, terms):
    """Everything the GUI needs to draw and explain the model."""
    quadratic = {k: v for k, v in terms.items() if k[0] != k[1]}
    coefficients = list(terms.values())
    n = len(variables)
    grouped = groups(variables)
    sizes = [len(v) for v in grouped.values()]
    feasible_assignments = 1
    for size in sizes:
        feasible_assignments *= size

    cheapest = cheapest_selection(variables)
    return {
        "path": os.path.basename(path),
        "fullPath": path,
        "offset": read_offset(path),
        "variables": [variables[i].as_dict() for i in sorted(variables)],
        "terms": [[i, j, c] for (i, j), c in sorted(terms.items())],
        "groups": [{"priority": p,
                    "variables": [v.index for v in members],
                    "minCost": min((v.cost for v in members if v.cost is not None),
                                   default=None)}
                   for p, members in grouped.items()],
        "stats": {
            "columns": n,
            "sequenceColumns": sum(1 for v in variables.values() if v.kind == "sequence"),
            "slackColumns": sum(1 for v in variables.values() if v.kind == "slack"),
            "quadraticTerms": len(quadratic),
            "density": (2.0 * len(quadratic) / (n * (n - 1))) if n > 1 else 0.0,
            "minCoefficient": min(coefficients) if coefficients else 0.0,
            "maxCoefficient": max(coefficients) if coefficients else 0.0,
            "blockingBlocks": len(grouped),
            "feasibleAssignments": feasible_assignments,
            "cheapestCost": sum(variables[i].cost or 0 for i in cheapest),
            "offset": read_offset(path),
        },
        "cheapestSelection": sorted(cheapest),
    }
