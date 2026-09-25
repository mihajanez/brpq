"""Reading of RBRP instance files and replay of a solver move plan.

The .dat readers follow Instance::read() in instance.cpp: the "Caserta"
(stacks blocks), "Bacci" (stacks tiers blocks) and "Exposito-Izquierdo"
(keyword) layouts.  Blocks keep their original labels here -- the C++ code
ranks them internally, but the GUI shows what the file shows.
"""
import os
import re


class Instance:
    def __init__(self, name, path):
        self.name = name
        self.path = path
        self.number_of_stacks = 0
        self.number_of_blocks = 0
        self.file_number_of_tiers = 0   # tier count stated in / implied by the file
        self.number_of_tiers = 0        # height limit actually used by the solver
        self.bay = []                   # bay[stack] = labels, bottom -> top

    def as_dict(self):
        return {
            "name": self.name,
            "stacks": self.number_of_stacks,
            "blocks": self.number_of_blocks,
            "fileTiers": self.file_number_of_tiers,
            "tiers": self.number_of_tiers,
            "bay": self.bay,
            "maxStackHeight": max((len(s) for s in self.bay), default=0),
        }


_INT_RE = re.compile(r"-?\d+")
_KEYS = (("tiers", ("Tiers", "Height")), ("stacks", ("Width", "Stacks")),
         ("containers", ("Containers",)))


def _ints(text):
    return [int(t) for t in _INT_RE.findall(text)]


def read_instance(path, name=None):
    instance = Instance(name or os.path.basename(path), path)
    with open(path) as f:
        lines = f.read().splitlines()

    # --- Exposito-Izquierdo: "Tiers: 5", "Stacks: 6", "0: 3 16 15 6" ---
    if any(k in line for line in lines for keys in _KEYS for k in keys[1]):
        stacks = {}
        for line in lines:
            if "Stack" in line and ":" in line:
                head, _, rest = line.partition(":")
                idx = _ints(head)
                if idx:
                    stacks[idx[-1]] = _ints(rest)
                continue
            for field, keys in _KEYS:
                if any(k in line for k in keys):
                    values = _ints(line.split(":")[-1])
                    if not values:
                        continue
                    if field == "tiers":
                        instance.file_number_of_tiers = values[0]
                    elif field == "stacks":
                        instance.number_of_stacks = values[0]
                    break
        instance.bay = [stacks.get(i, []) for i in range(instance.number_of_stacks)]
    else:
        # --- Caserta ("stacks blocks") / Bacci ("stacks tiers blocks") ---
        header_at = None
        for i, line in enumerate(lines):
            values = _ints(line)
            if len(values) == 3:
                instance.number_of_stacks, instance.file_number_of_tiers, \
                    instance.number_of_blocks = values
                header_at = i
                break
            if len(values) == 2:
                instance.number_of_stacks, instance.number_of_blocks = values
                header_at = i
                break
        if header_at is None:
            raise ValueError(f"{path}: no instance header line found")

        rest = []
        for line in lines[header_at + 1:]:
            rest.extend(_ints(line))

        instance.bay = []
        pos = 0
        for _ in range(instance.number_of_stacks):
            if pos >= len(rest):
                raise ValueError(f"{path}: insufficient block data")
            height = rest[pos]
            if len(rest) - pos - 1 < height:
                raise ValueError(f"{path}: insufficient block data")
            instance.bay.append(rest[pos + 1: pos + 1 + height])
            pos += height + 1

    instance.number_of_blocks = sum(len(s) for s in instance.bay)
    if instance.file_number_of_tiers <= 0:
        instance.file_number_of_tiers = max((len(s) for s in instance.bay), default=0)
    instance.number_of_tiers = instance.number_of_blocks
    return instance


def write_dat(path, bay, tiers=None):
    """Write a bay as a "Bacci"-style .dat file ("stacks tiers blocks", then one
    line per stack: height followed by the blocks bottom -> top).  The explicit
    tier count is what -E is measured from, so a designed instance can declare
    more tiers than its tallest stack uses."""
    blocks = sum(len(stack) for stack in bay)
    tiers = max(tiers or 0, max((len(s) for s in bay), default=0))
    with open(path, "w") as f:
        f.write(f"{len(bay)} {tiers} {blocks}\n")
        for stack in bay:
            f.write(" ".join(str(v) for v in [len(stack)] + list(stack)) + "\n")
    return path


def validate_bay(bay, tiers=None):
    """Return a list of human-readable problems with a designed bay."""
    problems = []
    if not bay:
        problems.append("no stacks")
        return problems
    labels = [b for stack in bay for b in stack]
    if not labels:
        problems.append("no blocks placed")
    for i, stack in enumerate(bay, start=1):
        if tiers and len(stack) > tiers:
            problems.append(f"stack {i} holds {len(stack)} blocks but only {tiers} tiers exist")
    bad = [b for b in labels if not isinstance(b, int) or b < 1]
    if bad:
        problems.append("priorities must be positive whole numbers")
    duplicates = sorted({b for b in labels if labels.count(b) > 1})
    if duplicates:
        problems.append("duplicate priorities: "
                        + ", ".join(str(d) for d in duplicates))
    return problems


def effective_tiers(instance, empty_tiers=None, maximum_height=None):
    """Height limit the solver ends up with, mirroring Instance::set_empty_tiers(),
    Instance::set_maximum_height() and the fallback in main.cpp."""
    tiers = 0
    if empty_tiers is not None and empty_tiers >= 0:
        tiers = min(instance.file_number_of_tiers + empty_tiers,
                    instance.number_of_blocks)
    if maximum_height:
        if tiers == 0:
            tiers = min(maximum_height, instance.number_of_blocks)
        else:
            tiers = max(maximum_height, tiers)
        tiers = max([tiers] + [len(s) for s in instance.bay])
    if tiers == 0:
        tiers = instance.number_of_blocks
    return tiers


def replay(instance, relocations):
    """Turn a list of (priority_label, src, dst) 1-based relocations into the
    full move-by-move timeline, the way Solution::print() walks a plan:
    retrieve while the target block is on top, otherwise take the next
    relocation.  Returns (steps, error)."""
    bay = [list(stack) for stack in instance.bay]
    remaining = sorted(b for stack in bay for b in stack)
    pending = list(relocations)

    def snapshot(kind, block=None, src=None, dst=None, number=None, note=None):
        target = remaining[0] if remaining else None
        return {
            "kind": kind, "block": block, "src": src, "dst": dst,
            "number": number, "note": note, "target": target,
            "bay": [list(s) for s in bay],
            "remaining": len(remaining),
            "targetStack": next((i for i, s in enumerate(bay)
                                 if target is not None and target in s), None),
        }

    steps = [snapshot("initial", note="Initial configuration")]
    retrieved = 0
    while remaining:
        target = remaining[0]
        stack = next(i for i, s in enumerate(bay) if target in s)
        if bay[stack][-1] == target:
            bay[stack].pop()
            remaining.pop(0)
            retrieved += 1
            steps.append(snapshot("retrieve", block=target, src=stack + 1,
                                  number=retrieved))
            continue
        if not pending:
            return steps, (f"move plan ran out: block {target} in stack "
                           f"{stack + 1} is still buried")
        label, src, dst = pending.pop(0)
        if src - 1 != stack or not bay[src - 1] or bay[src - 1][-1] != label:
            return steps, (f"relocation [{label}] {src}->{dst} does not match "
                           f"the current bay (expected the top of stack {stack + 1})")
        bay[src - 1].pop()
        bay[dst - 1].append(label)
        steps.append(snapshot("relocate", block=label, src=src, dst=dst,
                              number=len(relocations) - len(pending)))
    return steps, None
