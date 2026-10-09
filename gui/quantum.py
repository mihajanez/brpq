"""Quantum side of the GUI: the exported QUBO on a D-Wave annealer and as a
QAOA circuit on IBM hardware -- built, simulated classically, estimated, and
optionally run on the real platforms.

Unlike the rest of gui/, this module needs the project's .venv (dimod,
dwave-samplers, minorminer, dwave-system, qiskit, qiskit-aer,
qiskit-ibm-runtime, matplotlib); server.py imports it lazily, so the
classical GUI keeps working on a bare python3.

Conventions shared with qaoa_qubo.py, rescore.py and verify_solution.py:
  - QUBO variable i is qubit i; a measured bit is the variable (x = bit);
  - bitstrings are in Qiskit order (variable 0 = rightmost character);
  - energy(file) + "# offset" = the IP model's objective, so a feasible,
    replayable selection has energy + offset = its number of relocations.
"""
import base64
import io
import json
import math
import os
import sys
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from . import qubo as qubo_model
from .runner import ROOT

if ROOT not in sys.path:                      # qaoa_qubo.py / load_qubo.py live there
    sys.path.insert(0, ROOT)

# brute force / statevector up to this many variables; HPC jobs raise it
EXACT_LIMIT = int(os.environ.get("BRPQ_EXACT_LIMIT") or 24)
# how QAOA probabilities are computed: Qiskit Aer (default), or the same state by
# direct statevector algebra with numpy, or with CuPy on an NVIDIA GPU
QAOA_BACKEND = (os.environ.get("BRPQ_QAOA_BACKEND") or "aer").lower()
DRAW_LIMIT = 2500         # gates; bigger circuits are offered as downloads only
RUNS_DIR = os.path.join(ROOT, "quantum_runs")
IBM_JOBS_FILE = os.path.join(RUNS_DIR, "ibm-jobs.json")

_lock = threading.Lock()
_draw_lock = threading.Lock()   # matplotlib is not thread-safe

# Everything here runs on pool threads that live as long as the server. With
# Qiskit 2.5 / Aer 0.17, a thread that has read a circuit's data (depth(),
# count_ops(), drawing...) and then *exits* makes the next Aer run segfault --
# exactly what one-thread-per-request HTTP handlers do. Pool threads never exit.
_job_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="quantum-job")
_call_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="quantum-call")


def call(fn, *args, **kwargs):
    """Run fn on a long-lived worker thread and wait for its result."""
    return _call_pool.submit(fn, *args, **kwargs).result()


class Cancelled(Exception):
    pass


# ======================================================================= status
def _version(module):
    try:
        mod = __import__(module, fromlist=["__version__"])
        return getattr(mod, "__version__", "installed")
    except Exception:
        return None


def status():
    """Which quantum toolkits are importable, and whether credentials exist.
    Never returns a token -- only whether one is configured."""
    libs = {
        "numpy": _version("numpy"),
        "dimod": _version("dimod"),
        "dwave.samplers": _version("dwave.samplers"),
        "minorminer": _version("minorminer"),
        "dwave.system": _version("dwave.system"),
        "dwave.graphs": _version("dwave.graphs"),
        "qiskit": _version("qiskit"),
        "qiskit_aer": _version("qiskit_aer"),
        "qiskit_ibm_runtime": _version("qiskit_ibm_runtime"),
        "matplotlib": _version("matplotlib"),
        "pylatexenc": _version("pylatexenc"),
    }
    ibm = {"savedAccount": False, "channel": None}
    try:
        path = os.path.join(os.path.expanduser("~"), ".qiskit", "qiskit-ibm.json")
        with open(path) as f:
            accounts = json.load(f)
        if accounts:
            ibm["savedAccount"] = True
            first = next(iter(accounts.values()))
            ibm["channel"] = first.get("channel")
            ibm["instanceSet"] = bool(first.get("instance"))
    except (OSError, ValueError):
        pass
    if os.environ.get("QISKIT_IBM_TOKEN"):
        ibm["envToken"] = True
    dwave = {"configured": bool(os.environ.get("DWAVE_API_TOKEN"))}
    if not dwave["configured"]:
        try:
            from dwave.cloud.config import load_config
            dwave["configured"] = bool(load_config().get("token"))
        except Exception:
            pass
    return {
        "libraries": libs,
        "annealing": all(libs[k] for k in ("dimod", "dwave.samplers", "minorminer",
                                           "dwave.graphs")),
        "qaoa": all(libs[k] for k in ("qiskit", "qiskit_aer")),
        "ibmRuntime": bool(libs["qiskit_ibm_runtime"]),
        "drawing": bool(libs["matplotlib"] and libs["pylatexenc"]),
        "ibm": ibm,
        "dwave": dwave,
        "exactLimit": EXACT_LIMIT,
        "qaoaBackend": QAOA_BACKEND,
        "python": sys.executable,
    }


# ====================================================================== problem
class Problem:
    """One exported QUBO, with the vectorised helpers every solver here needs."""

    def __init__(self, path):
        self.path = path
        self.name = os.path.basename(path)
        self.variables, self.terms = qubo_model.load(path)
        self.offset = qubo_model.read_offset(path)
        self.n = len(self.variables)
        if sorted(self.variables) != list(range(self.n)):
            raise ValueError("expected QUBO columns 0..n-1, as QUBOModel::export_qubo "
                             "writes them")
        self.Q = np.zeros((self.n, self.n))
        for (i, j), c in self.terms.items():
            self.Q[i, j] += c
        self.groups = [[v.index for v in members]
                       for members in qubo_model.groups(self.variables).values()]
        self._bqm = None
        self._vectors = None
        self._optimum = None
        self._ansatz = {}
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- basics
    def bqm(self):
        if self._bqm is None:
            import dimod
            bqm = dimod.BinaryQuadraticModel.from_qubo(dict(self.terms))
            for i in range(self.n):
                bqm.add_variable(i)
            self._bqm = bqm
        return self._bqm

    def energies(self, X):
        X = np.asarray(X, dtype=float)
        return np.einsum("ij,jk,ik->i", X, self.Q, X)

    def feasible_rows(self, X):
        X = np.asarray(X)
        ok = np.ones(len(X), dtype=bool)
        for group in self.groups:
            ok &= X[:, group].sum(axis=1) == 1
        return ok

    def coefficient_scale(self):
        values = [abs(c) for c in self.terms.values() if c]
        return max(values) if values else 1.0

    # --------------------------------------------------- exhaustive (n <= 24)
    def vectors(self):
        """Energy and one-hot feasibility of all 2^n assignments, indexed the
        way a statevector is: bit i of the index is variable i."""
        if self.n > EXACT_LIMIT:
            raise ValueError(f"{self.n} variables: exhaustive vectors stop at "
                             f"{EXACT_LIMIT}")
        with self._lock:
            if self._vectors is None:
                self._vectors = self._build_vectors()
        return self._vectors

    def _build_vectors(self):
        n = self.n
        # energy by doubling: E[x + 2^k] = E[x] + Q_kk + sum_{j<k} Q_jk x_j, so
        # variable k only ever touches the first 2^k entries
        energy = np.zeros(1)
        for k in range(n):
            idx = np.arange(1 << k, dtype=np.int64)
            add = np.full(1 << k, self.Q[k, k])
            for j in np.nonzero(self.Q[:k, k])[0]:
                add += self.Q[j, k] * ((idx >> int(j)) & 1)
            energy = np.concatenate([energy, energy + add])
        idx = np.arange(1 << n, dtype=np.int64)
        feasible = np.ones(1 << n, dtype=bool)
        for group in self.groups:
            count = np.zeros(1 << n, dtype=np.int8)
            for i in group:
                count += ((idx >> i) & 1).astype(np.int8)
            feasible &= count == 1
        del idx
        quarters = energy * 4
        if np.allclose(quarters, np.rint(quarters)):   # the usual case: a 0.25 grid
            keys = np.rint(quarters).astype(np.int64)
            low = keys.min()
            present = np.bincount(keys - low) > 0
            slot = np.cumsum(present) - 1
            levels = (np.nonzero(present)[0] + low) / 4.0
            inverse = slot[keys - low]
        else:
            levels, inverse = np.unique(np.round(energy, 9), return_inverse=True)
        return {"energy": energy, "feasible": feasible,
                "levels": levels, "inverse": inverse.astype(np.int32)}

    def optimum(self):
        """Ground-state energy of the QUBO, and what uniform random guessing
        would score -- the baseline every sampler here is judged against."""
        with self._lock:
            if self._optimum is not None:
                return self._optimum
        if self.n <= EXACT_LIMIT:
            v = self.vectors()
            energy, feasible = v["energy"], v["feasible"]
            best = float(energy.min())
            at_best = np.isclose(energy, best)
            result = {
                "energy": best, "objective": best + self.offset,
                "method": f"brute force over 2^{self.n} assignments",
                "degeneracy": int(at_best.sum()),
                "maxEnergy": float(energy.max()),
                "uniform": {"meanEnergy": float(energy.mean()),
                            "feasibleFraction": float(feasible.mean()),
                            "optimumFraction": float(at_best.mean())},
            }
        else:
            result = self._optimum_by_tree_decomposition()
        with self._lock:
            self._optimum = result
        return result

    def _optimum_by_tree_decomposition(self):
        rng = np.random.default_rng(0)
        X = rng.integers(0, 2, size=(20000, self.n))
        energy = self.energies(X)
        uniform = {"meanEnergy": float(energy.mean()),
                   "feasibleFraction": float(self.feasible_rows(X).mean()),
                   "optimumFraction": None, "estimated": True}
        result = {"energy": None, "objective": None, "method": None,
                  "maxEnergy": None, "uniform": uniform}
        try:
            import networkx as nx
            import dwave.graphs as dg
            graph = nx.Graph()
            graph.add_nodes_from(range(self.n))
            graph.add_edges_from((i, j) for (i, j) in self.terms if i != j)
            width, _ = dg.min_fill_heuristic(graph)
            result["treewidth"] = int(width)
            if width > 22:
                result["method"] = (f"not computed: treewidth ~{width} is too large "
                                    "for an exact tree decomposition")
                return result
            from dwave.samplers import TreeDecompositionSolver
            sampleset = TreeDecompositionSolver().sample(self.bqm())
            best = float(sampleset.first.energy)
            result.update(energy=best, objective=best + self.offset,
                          method=f"exact tree decomposition (treewidth {width})")
        except Exception as exc:                       # pragma: no cover - defensive
            result["method"] = f"not computed: {exc}"
        return result

    # ------------------------------------------------------------ describing
    def describe(self, bits, ctx=None):
        """What one assignment means: energy, objective, one-hot check, and
        -- when it is feasible -- the move plan it replays into."""
        bits = [int(b) for b in bits]
        selection = {i for i, b in enumerate(bits) if b}
        energy = float(self.energies([bits])[0])
        violations = qubo_model.check_groups(self.variables, selection)
        cost = sum(self.variables[i].cost or 0 for i in selection
                   if self.variables[i].kind == "sequence")
        out = {
            "bitstring": "".join(str(b) for b in reversed(bits)),
            "energy": energy,
            "objective": energy + self.offset,
            "feasible": not violations,
            "violations": violations,
            "names": sorted(self.variables[i].name for i in selection),
            "sequenceCost": cost,
            "relocations": None,
            "planError": None,
        }
        instance, height_limit = ctx or (None, None)
        if instance is not None and not violations:
            plan, error = qubo_model.move_plan(instance, self.variables, selection,
                                               height_limit, complete=True)
            if plan:
                out["relocations"] = len(plan["relocations"])
                out["filled"] = plan["filled"]
                out["_plan"] = plan
            else:
                out["planError"] = error
        elif instance is None:
            out["planError"] = "no test case to replay against"
        return out


_problems = {}


def problem_for(path):
    key = (path, os.path.getmtime(path))
    with _lock:
        if key not in _problems:
            if len(_problems) > 8:
                _problems.clear()
            _problems[key] = Problem(path)
        return _problems[key]


def _strip(sample):
    return {k: v for k, v in sample.items() if not k.startswith("_")}


def score_counts(problem, counts, ctx, top=30, label=None):
    """Turn {bits tuple: count} into everything the GUI shows about a run:
    energy statistics, feasibility, the optimum hit rate, an energy histogram,
    the lowest-energy and the most frequent samples, and a playable plan for
    the best sample that replays."""
    keys = list(counts)
    X = np.array(keys, dtype=np.int8).reshape(len(keys), problem.n)
    weights = np.array([counts[k] for k in keys], dtype=float)
    shots = float(weights.sum())
    energy = problem.energies(X)
    feasible = problem.feasible_rows(X)
    optimum = problem.optimum()
    opt_energy = optimum.get("energy")

    out = {
        "shots": int(shots),
        "distinct": len(keys),
        "meanEnergy": float(weights @ energy / shots),
        "bestEnergy": float(energy.min()),
        "bestObjective": float(energy.min()) + problem.offset,
        "feasibleFraction": float(weights[feasible].sum() / shots),
        "offset": problem.offset,
        "optimum": optimum,
    }
    if opt_energy is not None:
        hits = np.isclose(energy, opt_energy)
        out["optimumHits"] = int(weights[hits].sum())
        out["optimumFraction"] = float(weights[hits].sum() / shots)

    levels = {}
    for e, w, f in zip(np.round(energy, 6), weights, feasible):
        entry = levels.setdefault(float(e), [0, 0])
        entry[0] += w
        if f:
            entry[1] += w
    hist = [{"energy": e, "count": int(c), "feasible": int(fc)}
            for e, (c, fc) in sorted(levels.items())]
    if len(hist) > 60:
        rest = hist[59:]
        hist = hist[:59] + [{"energy": rest[0]["energy"], "count": sum(h["count"] for h in rest),
                             "feasible": sum(h["feasible"] for h in rest), "rest": True}]
    out["histogram"] = hist

    order = np.lexsort((-weights, energy))
    samples = []
    best = None
    for rank, k in enumerate(order[:max(top, 200)]):
        need_row = rank < top
        if not need_row and best is not None:
            break
        described = problem.describe(X[k], ctx)
        described["count"] = int(weights[k])
        described["probability"] = float(weights[k] / shots)
        if need_row:
            samples.append(described)
        if best is None and described.get("_plan"):
            best = described
    out["samples"] = [_strip(s) for s in samples]
    frequent = np.argsort(-weights, kind="stable")[:8]
    out["frequent"] = []
    for k in frequent:
        described = problem.describe(X[k], ctx)
        described["count"] = int(weights[k])
        described["probability"] = float(weights[k] / shots)
        out["frequent"].append(_strip(described))

    if best is not None:
        instance, height_limit = ctx
        out["best"] = _strip(best)
        out["plan"] = qubo_model.plan_payload(instance, best["_plan"], height_limit,
                                              energy=best["energy"], label=label)
    return out


def counts_from_sampleset(problem, sampleset):
    sampleset = sampleset.aggregate()
    columns = [list(sampleset.variables).index(i) for i in range(problem.n)]
    counts = Counter()
    for row, occ in zip(sampleset.record.sample, sampleset.record.num_occurrences):
        counts[tuple(int(row[c]) for c in columns)] += int(occ)
    return counts


def time_to_solution(p, seconds_per_read, overhead=0.0, target=0.99):
    """Time to reach the optimum with `target` confidence, given a per-read
    success probability p -- the standard benchmark for stochastic solvers."""
    if p is None or p <= 0:
        return None, None
    if p >= target:
        return overhead + seconds_per_read, 1
    reads = math.ceil(math.log(1 - target) / math.log(1 - p))
    return overhead + reads * seconds_per_read, reads


# ==================================================================== annealing
TOPOLOGIES = {
    "pegasus": {"label": "Advantage — Pegasus P16", "qpu": "advantage",
                "family": "pegasus", "shape": "P16"},
    "zephyr": {"label": "Advantage2 — Zephyr Z12", "qpu": "advantage",
               "family": "zephyr", "shape": "Z12,T4"},
    "chimera": {"label": "D-Wave 2000Q — Chimera C16", "qpu": "2000q",
                "family": "chimera", "shape": "C16"},
}
_graphs = {}
_embeddings = {}


def target_graph(topology):
    import dwave.graphs as dg
    if topology not in TOPOLOGIES:
        raise ValueError(f"unknown topology {topology!r}")
    with _lock:
        if topology not in _graphs:
            if topology == "pegasus":
                graph = dg.pegasus_graph(16)
                layout = dg.pegasus_layout(graph)
            elif topology == "zephyr":
                graph = dg.zephyr_graph(12, 4)
                layout = dg.zephyr_layout(graph)
            else:
                graph = dg.chimera_graph(16)
                layout = dg.chimera_layout(graph)
            nodes = sorted(graph.nodes)
            xy = np.array([layout[v] for v in nodes], dtype=float)
            xy -= xy.min(axis=0)
            xy /= max(float(xy.max()), 1e-9)
            _graphs[topology] = {"graph": graph, "nodes": nodes,
                                 "index": {v: k for k, v in enumerate(nodes)},
                                 "xy": xy}
        return _graphs[topology]


def qpu_timing(topology, qubits, reads, anneal_us=None):
    """QPU access time from D-Wave's published timing model -- the same
    formula as dwave.cloud's Solver.estimate_qpu_access_time, fed with the
    problem_timing_data an Advantage / 2000Q system reported (dwave-cloud's
    reference values). Returns microseconds plus the breakdown."""
    from dwave.cloud.solver import StructuredSolver
    from dwave.cloud.testing.mocks import qpu_problem_timing_data

    qpu = TOPOLOGIES.get(topology, TOPOLOGIES["pegasus"])["qpu"]
    data = qpu_problem_timing_data(qpu)
    shim = type("TimingShim", (), {"properties": {"problem_timing_data": data}})()
    anneal = float(anneal_us or data["default_annealing_time"])
    total = StructuredSolver.estimate_qpu_access_time(
        shim, num_qubits=max(1, int(qubits)), num_reads=int(reads), annealing_time=anneal)
    programming = data["typical_programming_time"] + data["default_programming_thermalization"]
    per_read = (total - programming) / max(1, int(reads))
    return {
        "totalUs": float(total), "programmingUs": float(programming),
        "perReadUs": float(per_read), "annealUs": anneal,
        "readoutAndDelayUs": float(per_read - anneal),
        "reads": int(reads), "qubits": int(qubits),
        "model": f"D-Wave timing model v{data['version']} "
                 f"({'Advantage 4.1' if qpu == 'advantage' else '2000Q 6'} reference data)",
    }


def embed(problem, topology="pegasus", seed=1, tries=10, timeout=30.0):
    """Minor-embed the QUBO's interaction graph into an ideal (full-yield)
    QPU graph with minorminer: each logical variable becomes a chain of
    physical qubits that must agree."""
    import minorminer
    from dwave.embedding.chain_strength import uniform_torque_compensation

    target = target_graph(topology)
    graph = target["graph"]
    edges = [(i, j) for (i, j) in problem.terms if i != j]
    started = time.time()
    embedding = {}
    if edges:
        embedding = minorminer.find_embedding(edges, graph, random_seed=int(seed),
                                              tries=int(tries), timeout=float(timeout))
        if not embedding:
            raise ValueError(f"minorminer found no embedding of {problem.n} variables "
                             f"into {TOPOLOGIES[topology]['label']} within {timeout:.0f}s")
    used = {q for chain in embedding.values() for q in chain}
    free = (q for q in target["nodes"] if q not in used)
    for v in range(problem.n):                  # variables without couplings
        if v not in embedding:
            embedding[v] = [next(free)]
    elapsed = time.time() - started

    embedding = {int(v): [int(q) for q in chain] for v, chain in embedding.items()}
    index = target["index"]
    chain_edges, couplers = [], []
    for v, chain in embedding.items():
        members = set(chain)
        for a in chain:
            for b in graph.neighbors(a):
                if b in members and a < b:
                    chain_edges.append([index[a], index[b], v])
    for (i, j) in edges:
        link = None
        for a in embedding[i]:
            for b in graph.neighbors(a):
                if b in set(embedding[j]):
                    link = [index[a], index[b], i, j]
                    break
            if link:
                break
        if link:
            couplers.append(link)

    lengths = [len(embedding[v]) for v in range(problem.n)]
    physical = sum(lengths)
    key = uuid.uuid4().hex[:12]
    with _lock:
        if len(_embeddings) > 32:
            _embeddings.clear()
        _embeddings[key] = {"path": problem.path, "topology": topology,
                            "embedding": embedding}
    histogram = Counter(lengths)
    chain_strength = float(uniform_torque_compensation(problem.bqm()))
    return {
        "id": key,
        "topology": topology,
        "label": TOPOLOGIES[topology]["label"],
        "targetQubits": len(target["nodes"]),
        "targetCouplers": graph.number_of_edges(),
        "logical": problem.n,
        "physical": physical,
        "maxChain": max(lengths) if lengths else 0,
        "meanChain": physical / max(1, problem.n),
        "chainHistogram": [[k, histogram[k]] for k in sorted(histogram)],
        "couplersUsed": len(chain_edges) + len(couplers),
        "logicalCouplers": len(edges),
        "chipFraction": physical / len(target["nodes"]),
        "chainStrength": chain_strength,
        "maxBias": problem.coefficient_scale(),
        "seconds": elapsed,
        "seed": int(seed),
        "xy": np.round(target["xy"], 4).tolist(),
        "chains": [[index[q] for q in embedding[v]] for v in range(problem.n)],
        "chainEdges": chain_edges,
        "couplers": couplers,
        "names": [problem.variables[i].name for i in range(problem.n)],
        "timing": qpu_timing(topology, physical, 1000),
    }


def stored_embedding(key, problem):
    with _lock:
        entry = _embeddings.get(key)
    if entry is None or entry["path"] != problem.path:
        return None
    return entry


ANNEAL_METHODS = {
    "sa": "Simulated annealing (logical QUBO)",
    "sa-embedded": "Simulated annealing on the embedded QPU problem",
    "tabu": "Tabu search",
    "steepest": "Steepest descent",
    "tree": "Tree decomposition (exact)",
    "exact": "Exhaustive enumeration (exact)",
    "random": "Random sampler (baseline)",
    "qpu": "D-Wave QPU (Leap)",
    "hybrid": "Leap hybrid BQM solver",
}


def anneal(problem, ctx, opts):
    """Sample the QUBO with one of D-Wave's samplers and score the result."""
    import dimod

    method = opts.get("method") or "sa"
    if method not in ANNEAL_METHODS:
        raise ValueError(f"unknown method {method!r}")
    reads = max(1, min(int(opts.get("reads") or 1000), 100000))
    sweeps = max(10, min(int(opts.get("sweeps") or 1000), 1000000))
    seed = int(opts.get("seed") or 1)
    anneal_us = float(opts.get("annealTime") or 20.0)
    topology = opts.get("topology") or "pegasus"
    chain_strength = opts.get("chainStrength")
    chain_strength = float(chain_strength) if chain_strength not in (None, "") else None
    bqm = problem.bqm()
    info = {"method": method, "methodLabel": ANNEAL_METHODS[method], "reads": reads}

    started = time.time()
    if method == "sa":
        from dwave.samplers import SimulatedAnnealingSampler
        sampleset = SimulatedAnnealingSampler().sample(bqm, num_reads=reads,
                                                       num_sweeps=sweeps, seed=seed)
        info["sweeps"] = sweeps
    elif method == "sa-embedded":
        from dwave.embedding import embed_bqm, unembed_sampleset
        from dwave.embedding.chain_breaks import majority_vote
        from dwave.embedding.chain_strength import uniform_torque_compensation
        from dwave.samplers import SimulatedAnnealingSampler
        entry = stored_embedding(opts.get("embeddingId"), problem)
        if entry is None:
            raise ValueError("find an embedding first: this method anneals the "
                             "physical (embedded) problem")
        embedding = entry["embedding"]
        graph = target_graph(entry["topology"])["graph"]
        strength = chain_strength or float(uniform_torque_compensation(bqm))
        target_bqm = embed_bqm(bqm, embedding, graph, chain_strength=strength)
        raw = SimulatedAnnealingSampler().sample(target_bqm, num_reads=reads,
                                                 num_sweeps=sweeps, seed=seed)
        sampleset = unembed_sampleset(raw, embedding, bqm,
                                      chain_break_method=majority_vote,
                                      chain_break_fraction=True)
        cbf = sampleset.record.chain_break_fraction
        info.update(sweeps=sweeps, chainStrength=strength,
                    physicalQubits=len(target_bqm.variables),
                    chainBreakFraction=float(np.mean(cbf)),
                    readsWithBrokenChains=float(np.mean(cbf > 0)),
                    topology=entry["topology"])
        topology = entry["topology"]
    elif method == "tabu":
        from dwave.samplers import TabuSampler
        timeout_ms = max(5, min(int(opts.get("tabuTimeout") or 20), 60000))
        reads = min(reads, 100)
        sampleset = TabuSampler().sample(bqm, num_reads=reads, timeout=timeout_ms,
                                         seed=seed)
        info.update(reads=reads, timeoutMs=timeout_ms)
    elif method == "steepest":
        from dwave.samplers import SteepestDescentSolver
        sampleset = SteepestDescentSolver().sample(bqm, num_reads=reads, seed=seed)
    elif method == "tree":
        from dwave.samplers import TreeDecompositionSolver
        sampleset = TreeDecompositionSolver().sample(bqm, num_reads=min(reads, 50))
        info["reads"] = min(reads, 50)
    elif method == "exact":
        if problem.n > EXACT_LIMIT:
            raise ValueError(f"{problem.n} variables: exhaustive enumeration stops at "
                             f"{EXACT_LIMIT} (2^{EXACT_LIMIT} assignments)")
        energy = problem.vectors()["energy"]
        k = min(len(energy), 64)
        lowest = np.argpartition(energy, k - 1)[:k]
        rows = [[(int(s) >> i) & 1 for i in range(problem.n)] for s in lowest]
        sampleset = dimod.SampleSet.from_samples_bqm((rows, list(range(problem.n))), bqm)
        info["note"] = (f"the {k} lowest of all 2^{problem.n} assignments, one read "
                        "each — the exact ground truth, not a sampling run")
    elif method == "random":
        rng = np.random.default_rng(seed)
        rows = rng.integers(0, 2, size=(reads, problem.n))
        sampleset = dimod.SampleSet.from_samples_bqm((rows, list(range(problem.n))), bqm)
    elif method == "qpu":
        sampleset, extra = _sample_qpu(problem, opts, reads, anneal_us, topology,
                                       chain_strength)
        info.update(extra)
    else:  # hybrid
        from dwave.system import LeapHybridSampler
        sampler = LeapHybridSampler(token=opts.get("token") or None)
        limit = opts.get("timeLimit")
        kwargs = {"time_limit": float(limit)} if limit else {}
        sampleset = sampler.sample(bqm, label="RBRP QUBO (GUI)", **kwargs)
        info.update(solver=sampler.solver.id,
                    timing=dict(sampleset.info.get("timing", {})))
    wall = time.time() - started

    counts = counts_from_sampleset(problem, sampleset)
    info["wallTime"] = wall
    timing = sampleset.info.get("timing") if hasattr(sampleset, "info") else None
    if timing and "timing" not in info:
        info["samplerTiming"] = {k: (float(v) if isinstance(v, (int, float, np.number)) else v)
                                 for k, v in timing.items()}

    label = ANNEAL_METHODS[method]
    scores = score_counts(problem, counts, ctx, label=label)
    info["scores"] = scores

    # how long the same success rate would take, here and on a QPU
    p = scores.get("optimumFraction")
    per_read = wall / max(1, info["reads"])
    tts, tts_reads = time_to_solution(p, per_read)
    info["tts"] = {"p": p, "secondsPerRead": per_read, "seconds": tts, "reads": tts_reads,
                   "basis": "optimum" if p is not None else None}
    qubits = info.get("physicalQubits") or (info.get("embeddingStats") or {}).get("physical")
    if qubits is None and opts.get("embeddingId"):
        entry = stored_embedding(opts.get("embeddingId"), problem)
        if entry:
            qubits = sum(len(c) for c in entry["embedding"].values())
    if qubits is None:
        qubits = problem.n
    qpu = qpu_timing(topology, qubits, info["reads"], anneal_us)
    info["qpuEstimate"] = qpu
    if p:
        qpu_tts, reads_needed = time_to_solution(p, qpu["perReadUs"] * 1e-6,
                                                 overhead=qpu["programmingUs"] * 1e-6)
        info["tts"]["qpuSeconds"] = qpu_tts
        info["tts"]["qpuReads"] = reads_needed
    return info


def _sample_qpu(problem, opts, reads, anneal_us, topology, chain_strength):
    from dwave.system import DWaveSampler, EmbeddingComposite

    kwargs = {}
    if opts.get("token"):
        kwargs["token"] = opts["token"]
    family = TOPOLOGIES.get(topology, {}).get("family")
    if opts.get("solver"):
        kwargs["solver"] = opts["solver"]
    elif family:
        kwargs["solver"] = {"topology__type": family}
    qpu = DWaveSampler(**kwargs)
    params = {"num_reads": reads, "annealing_time": anneal_us,
              "label": "RBRP QUBO (GUI)", "return_embedding": True}
    if chain_strength:
        params["chain_strength"] = chain_strength
    sampleset = EmbeddingComposite(qpu).sample(problem.bqm(), **params)
    context = sampleset.info.get("embedding_context", {})
    embedding = context.get("embedding") or {}
    lengths = [len(c) for c in embedding.values()]
    extra = {
        "solver": qpu.solver.id,
        "timing": {k: float(v) for k, v in sampleset.info.get("timing", {}).items()
                   if isinstance(v, (int, float))},
        "chainStrength": context.get("chain_strength"),
        "embeddingStats": {"physical": sum(lengths),
                           "maxChain": max(lengths) if lengths else 0},
    }
    if "chain_break_fraction" in sampleset.record.dtype.names:
        extra["chainBreakFraction"] = float(np.mean(sampleset.record.chain_break_fraction))
    return sampleset, extra


# ========================================================================= QAOA
DEVICES = [   # (fake-provider snapshot, device it mirrors, processor, qubits)
    ("FakeFez", "ibm_fez", "Heron r2", 156),
    ("FakeMarrakesh", "ibm_marrakesh", "Heron r2", 156),
    ("FakeKingston", "ibm_kingston", "Heron r2", 156),
    ("FakeAachen", "ibm_aachen", "Heron r3", 156),
    ("FakeTorino", "ibm_torino", "Heron r1", 133),
    ("FakeBrisbane", "ibm_brisbane", "Eagle r3", 127),
    ("FakeSherbrooke", "ibm_sherbrooke", "Eagle r3", 127),
    ("FakeKolkataV2", "ibmq_kolkata", "Falcon r5.11", 27),
    ("FakeGuadalupeV2", "ibmq_guadalupe", "Falcon r4", 16),
]
_fake = {}
_noisy = {}


def fake_backend(name):
    import qiskit_ibm_runtime.fake_provider as fake_provider
    if name not in {d[0] for d in DEVICES}:
        raise ValueError(f"unknown device snapshot {name!r}")
    with _lock:
        if name not in _fake:
            _fake[name] = getattr(fake_provider, name)()
        return _fake[name]


def noisy_simulator(name):
    from qiskit_aer import AerSimulator
    backend = fake_backend(name)
    with _lock:
        if name not in _noisy:
            _noisy[name] = AerSimulator.from_backend(backend)
        return _noisy[name]


def devices():
    return [{"id": cls, "name": real, "family": family, "qubits": qubits}
            for cls, real, family, qubits in DEVICES]


def ansatz(problem, reps):
    """The QAOA circuit for this QUBO: qaoa_ansatz over the Ising form from
    qaoa_qubo.py (so the sign convention is the CLI's), with the angles
    renamed beta[k] / gamma[k] so they survive an OpenQASM round trip."""
    reps = max(1, min(int(reps), 12))
    with problem._lock:
        cached = problem._ansatz.get(reps)
    if cached:
        return cached
    from qiskit.circuit import ParameterVector
    from qiskit.circuit.library import qaoa_ansatz
    from qaoa_qubo import qubo_to_ising_operator

    cost_op, ising_offset, n = qubo_to_ising_operator(problem.bqm())
    circuit = qaoa_ansatz(cost_operator=cost_op, reps=reps)
    beta, gamma = ParameterVector("beta", reps), ParameterVector("gamma", reps)
    mapping = {}
    for p in circuit.parameters:
        kind, index = p.name[0], int(p.name[2:-1])
        mapping[p] = beta[index] if kind == "β" else gamma[index]
    circuit = circuit.assign_parameters(mapping)
    circuit.name = f"QAOA p={reps}"
    entry = {"circuit": circuit, "cost": cost_op, "isingOffset": float(ising_offset),
             "reps": reps}
    with problem._lock:
        problem._ansatz[reps] = entry
    return entry


def measured(circuit, params=None):
    c = circuit.assign_parameters(params) if params is not None else circuit.copy()
    c.measure_all()
    return c


def _two_qubit(ops):
    return sum(c for g, c in ops.items() if g in ("cx", "cz", "ecr", "rzz", "swap", "iswap"))


def qaoa_build(problem, reps):
    """Circuit facts and the cost Hamiltonian, for the Circuit panel."""
    from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager

    entry = ansatz(problem, reps)
    circuit, cost_op = entry["circuit"], entry["cost"]
    ops = dict(circuit.count_ops())
    pm = generate_preset_pass_manager(optimization_level=1,
                                      basis_gates=["rz", "sx", "x", "cx"])
    basis = pm.run(circuit)
    basis_ops = dict(basis.count_ops())
    terms = []
    for label, indices, coeff in cost_op.to_sparse_list():
        terms.append({"pauli": label, "qubits": [int(i) for i in indices],
                      "coeff": float(np.real(coeff))})
    terms.sort(key=lambda t: (len(t["qubits"]), t["qubits"]))
    return {
        "reps": entry["reps"],
        "qubits": circuit.num_qubits,
        "parameters": [p.name for p in circuit.parameters],
        "depth": circuit.depth(),
        "ops": ops,
        "twoQubit": _two_qubit(ops),
        "basis": {"depth": basis.depth(), "ops": basis_ops,
                  "twoQubit": _two_qubit(basis_ops)},
        "hamiltonian": terms,
        "isingOffset": entry["isingOffset"],
        "quboOffset": problem.offset,
        "linearTerms": sum(1 for t in terms if len(t["qubits"]) == 1),
        "quadraticTerms": sum(1 for t in terms if len(t["qubits"]) == 2),
        "drawable": sum(ops.values()) <= DRAW_LIMIT,
        "names": [problem.variables[i].name for i in range(problem.n)],
        "statevectorBytes": 16 * (1 << problem.n) if problem.n <= 40 else None,
        "exact": problem.n <= EXACT_LIMIT,
    }


def _transpiled_for(problem, reps, device, seed=7, params=None):
    from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager
    backend = fake_backend(device)
    circuit = measured(ansatz(problem, reps)["circuit"], params)
    pm = generate_preset_pass_manager(optimization_level=3, backend=backend,
                                      seed_transpiler=seed)
    return backend, pm.run(circuit)


def draw(problem, reps, kind="logical", device=None, fold=None):
    """A circuit diagram from Qiskit's matplotlib drawer, as SVG bytes."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    entry = ansatz(problem, reps)
    circuit = entry["circuit"]
    if kind == "logical":
        target = measured(circuit)
    elif kind == "layer":
        target = circuit.copy()
    elif kind == "basis":
        from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager
        pm = generate_preset_pass_manager(optimization_level=1,
                                          basis_gates=["rz", "sx", "x", "cx"])
        target = pm.run(measured(circuit))
    elif kind == "transpiled":
        _, target = _transpiled_for(problem, reps, device or "FakeFez")
    else:
        raise ValueError(f"unknown diagram {kind!r}")
    size = sum(target.count_ops().values())
    if size > DRAW_LIMIT:
        raise ValueError(f"{size} gates is too many to draw legibly; download the "
                         "circuit (QASM / QPY) and open it in IBM Quantum Composer")
    with _draw_lock:
        figure = target.draw("mpl", fold=int(fold or 40), idle_wires=False,
                             style={"name": "iqp"})
        buffer = io.BytesIO()
        figure.savefig(buffer, format="svg", bbox_inches="tight")
        plt.close(figure)
    return buffer.getvalue()


def _esp(backend, isa):
    """Estimated success probability: the product of (1 - error) over every
    gate and measurement, from the device's calibration snapshot -- a quick
    proxy for how much of the hardware output is signal rather than noise."""
    target = backend.target
    log_esp = 0.0
    for inst in isa.data:
        name = inst.operation.name
        if name in ("barrier", "delay") or name not in target.operation_names:
            continue
        qargs = tuple(isa.find_bit(q).index for q in inst.qubits)
        try:
            props = target[name].get(qargs)
        except Exception:
            props = None
        error = getattr(props, "error", None) if props is not None else None
        if error:
            log_esp += math.log(max(1e-12, 1.0 - min(error, 0.999999)))
    return math.exp(log_esp)


def device_estimate(backend, isa, shots, evaluations, label=None):
    ops = dict(isa.count_ops())
    try:
        duration = float(isa.estimate_duration(backend.target, unit="s"))
    except Exception:
        duration = None
    try:
        rep_delay = float(backend.configuration().default_rep_delay)
        if rep_delay > 1:                 # some snapshots store microseconds
            rep_delay *= 1e-6
    except Exception:
        rep_delay = 250e-6
    per_shot = (duration or 0.0) + rep_delay
    physical = sorted({isa.find_bit(q).index for inst in isa.data for q in inst.qubits
                       if inst.operation.name != "barrier"})
    family = None
    try:
        family = backend.configuration().processor_type
    except Exception:
        pass
    return {
        "device": label or backend.name,
        "family": family,
        "deviceQubits": backend.num_qubits,
        "physicalQubits": len(physical),
        "depth": isa.depth(),
        "ops": {k: v for k, v in ops.items() if k != "barrier"},
        "twoQubit": _two_qubit(ops),
        "durationS": duration,
        "repDelayS": rep_delay,
        "esp": _esp(backend, isa),
        "samplingS": shots * per_shot,
        "optimizationS": evaluations * shots * per_shot,
        "jobs": evaluations + 1,
        "shots": shots,
    }


def qaoa_estimate(job, problem, opts):
    """Resource and time estimates: the circuit transpiled for several IBM
    device snapshots (offline -- no account needed), plus the cost of
    simulating it here."""
    reps = int(opts.get("reps") or 1)
    shots = int(opts.get("shots") or 4096)
    evaluations = int(opts.get("maxiter") or 100) * int(opts.get("restarts") or 1)
    chosen = opts.get("devices") or ["FakeFez", "FakeTorino", "FakeBrisbane"]
    out = {"reps": reps, "shots": shots, "evaluations": evaluations, "devices": []}

    for name in chosen:
        if job.cancelled:
            raise Cancelled()
        backend = fake_backend(name)
        real = next((d[1] for d in DEVICES if d[0] == name), name)
        if problem.n > backend.num_qubits:
            out["devices"].append({"device": real, "snapshot": name,
                                   "deviceQubits": backend.num_qubits,
                                   "fits": False})
            continue
        job.note(f"transpiling for {real} ({backend.num_qubits} qubits)…")
        started = time.time()
        _, isa = _transpiled_for(problem, reps, name)
        estimate = device_estimate(backend, isa, shots, evaluations, label=real)
        estimate.update(snapshot=name, fits=True, transpileS=time.time() - started)
        out["devices"].append(estimate)

    # classical simulation cost on this machine
    sim = {"qubits": problem.n, "statevectorBytes": 16 * (1 << min(problem.n, 62)),
           "feasible": problem.n <= EXACT_LIMIT}
    if problem.n <= EXACT_LIMIT:
        job.note("timing one statevector evaluation…")
        evaluator = make_evaluator(problem, reps, seed=1)
        x = np.full(2 * reps, 0.3)
        evaluator.probabilities(x)                     # warm-up
        started = time.time()
        for _ in range(3):
            evaluator.probabilities(x)
        per_eval = (time.time() - started) / 3
        sim.update(secondsPerEvaluation=per_eval,
                   optimizationS=per_eval * evaluations)
    out["simulation"] = sim
    return out


class _StatevectorEvaluator:
    """Exact probabilities of the QAOA state from Qiskit Aer's statevector
    simulator, the circuit transpiled once and the angles bound per call."""

    def __init__(self, problem, reps, seed=1):
        from qiskit import transpile
        from qiskit_aer import AerSimulator

        circuit = ansatz(problem, reps)["circuit"].copy()
        circuit.save_statevector()
        self.sim = AerSimulator(method="statevector", seed_simulator=seed)
        self.circuit = transpile(circuit, self.sim)
        self.params = list(self.circuit.parameters)

    def probabilities(self, x):
        binds = [{p: [float(v)] for p, v in zip(self.params, x)}]
        result = self.sim.run(self.circuit, parameter_binds=binds).result()
        state = np.asarray(result.get_statevector())
        return np.abs(state) ** 2


def numpy_qaoa_probabilities(energy, n, betas, gammas):
    """The same state as the Qiskit circuit, by direct statevector algebra:
    |+>^n, then per layer the cost phase exp(-i*gamma*E) and Rx(2*beta) on
    every qubit. Used for the p=1 landscape (thousands of points)."""
    psi = np.full(1 << n, 1 / math.sqrt(1 << n), dtype=complex)
    for beta, gamma in zip(betas, gammas):
        psi = psi * np.exp(-1j * gamma * energy)
        c, s = math.cos(beta), -1j * math.sin(beta)
        for q in range(n):
            view = psi.reshape(-1, 2, 1 << q)
            a = view[:, 0, :].copy()
            b = view[:, 1, :]
            view[:, 0, :] = c * a + s * b
            view[:, 1, :] = s * a + c * b
            psi = view.reshape(-1)
    return np.abs(psi) ** 2


class _ArrayEvaluator:
    """QAOA probabilities by direct statevector algebra -- the state of the
    Qiskit circuit (see numpy_qaoa_probabilities), on numpy or, with
    BRPQ_QAOA_BACKEND=cupy, on an NVIDIA GPU. No circuit is built, so it
    scales to whatever the memory holds: about 48 bytes x 2^n at the peak
    (n = 30 on an 80 GB GPU, 31 on 180 GB, 32 on 288 GB)."""

    CHUNK = 1 << 24

    def __init__(self, problem, reps, backend="numpy"):
        if backend == "cupy":
            import cupy as xp
        else:
            xp = np
        self.xp = xp
        self.backend = backend
        self.n = problem.n
        self.reps = reps
        self.energy = xp.asarray(problem.vectors()["energy"], dtype=xp.float64)
        names = [f"beta[{k}]" for k in range(reps)] + [f"gamma[{k}]" for k in range(reps)]
        self.params = [type("P", (), {"name": nm})() for nm in names]
        self.label = ("CuPy statevector (GPU)" if backend == "cupy"
                      else "numpy statevector")

    def probabilities(self, x):
        xp, n = self.xp, self.n
        x = [float(v) for v in x]
        betas, gammas = x[:self.reps], x[self.reps:]
        psi = xp.full(1 << n, 1 / math.sqrt(1 << n), dtype=xp.complex128)
        for beta, gamma in zip(betas, gammas):
            for start in range(0, 1 << n, self.CHUNK):        # cost phase, in chunks
                stop = min(start + self.CHUNK, 1 << n)
                psi[start:stop] *= xp.exp(-1j * gamma * self.energy[start:stop])
            c, s = math.cos(beta), -1j * math.sin(beta)
            for q in range(n):                                 # Rx(2 beta) on qubit q
                view = psi.reshape(-1, 2, 1 << q)
                a = view[:, 0, :].copy()
                b = view[:, 1, :]
                view[:, 0, :] = c * a + s * b
                view[:, 1, :] = s * a + c * b
                del a
        probs = xp.abs(psi) ** 2
        del psi
        if xp is not np:
            probs = xp.asnumpy(probs)
        return probs


def make_evaluator(problem, reps, seed=1):
    """The QAOA probability engine chosen by BRPQ_QAOA_BACKEND."""
    if QAOA_BACKEND in ("numpy", "cupy"):
        return _ArrayEvaluator(problem, reps, QAOA_BACKEND)
    evaluator = _StatevectorEvaluator(problem, reps, seed)
    evaluator.label = "Qiskit Aer statevector"
    return evaluator


def _cvar(probs, energy, alpha):
    """Conditional value at risk: the mean energy of the best alpha fraction
    of the distribution (alpha = 1 is the plain expectation)."""
    if alpha >= 1:
        return float(probs @ energy)
    order = np.argsort(energy, kind="stable")
    p = probs[order]
    cumulative = np.cumsum(p)
    cut = np.searchsorted(cumulative, alpha)
    total = float(p[:cut] @ energy[order][:cut])
    remainder = alpha - (cumulative[cut - 1] if cut else 0.0)
    if cut < len(p):
        total += remainder * energy[order][cut]
    return total / alpha


def _sample_from_probs(probs, shots, rng):
    draws = rng.multinomial(shots, probs / probs.sum())
    nz = np.nonzero(draws)[0]
    return nz, draws[nz]


def _bits(index, n):
    return tuple((int(index) >> i) & 1 for i in range(n))


def qaoa_run(job, problem, ctx, opts):
    """Optimise the QAOA angles on Aer's statevector simulator, then sample
    the tuned circuit -- ideal, or with a real device's noise model."""
    from scipy.optimize import minimize

    if problem.n > EXACT_LIMIT:
        raise ValueError(f"{problem.n} qubits: statevector simulation here stops at "
                         f"{EXACT_LIMIT} ({16 * (1 << EXACT_LIMIT) / 2**20:.0f} MiB). "
                         "Export the circuit and run it on IBM hardware instead.")
    reps = int(opts.get("reps") or 1)
    restarts = max(1, min(int(opts.get("restarts") or 1), 20))
    maxiter = max(1, min(int(opts.get("maxiter") or 100), 2000))
    optimizer = opts.get("optimizer") or "COBYLA"
    shots = max(16, min(int(opts.get("shots") or 4096), 200000))
    alpha = float(opts.get("cvar") or 1.0)
    alpha = min(1.0, max(0.01, alpha))
    seed = int(opts.get("seed") or 1)
    shot_noise = bool(opts.get("shotNoise"))
    noisy = opts.get("noiseDevice") or None
    init = opts.get("initParams")

    job.note("building the circuit and the energy table…")
    vectors = problem.vectors()
    energy, feasible = vectors["energy"], vectors["feasible"]
    optimum = problem.optimum()
    e_min, e_max = optimum["energy"], optimum["maxEnergy"]
    at_opt = np.isclose(energy, e_min)
    evaluator = make_evaluator(problem, reps, seed)
    rng = np.random.default_rng(seed)
    scale = max(1.0, problem.coefficient_scale())

    trace = []
    best_overall = {"value": math.inf, "x": None}
    started = time.time()

    def objective(x, restart):
        if job.cancelled:
            raise Cancelled()
        probs = evaluator.probabilities(x)
        if shot_noise:
            idx, cnt = _sample_from_probs(probs, shots, rng)
            sampled = np.zeros_like(probs)
            sampled[idx] = cnt / shots
            value = _cvar(sampled, energy, alpha)
        else:
            value = _cvar(probs, energy, alpha)
        mean = float(probs @ energy)
        point = {"eval": len(trace) + 1, "restart": restart, "value": value,
                 "energy": mean, "pOpt": float(probs[at_opt].sum()),
                 "pFeasible": float(probs[feasible].sum())}
        trace.append(point)
        if value < best_overall["value"]:
            best_overall.update(value=value, x=np.array(x, dtype=float))
        job.progress(point)
        return value

    results = []
    for r in range(restarts):
        if init and r == 0:
            x0 = np.array([float(v) for v in init], dtype=float)
            if len(x0) != 2 * reps:
                raise ValueError(f"initial angles: {len(x0)} given, p={reps} needs "
                                 f"{2 * reps} (all betas, then all gammas)")
        else:
            x0 = np.concatenate([rng.uniform(0, math.pi, reps),
                                 rng.uniform(0, math.pi / scale, reps)])
        job.note(f"restart {r + 1}/{restarts}: optimising {2 * reps} angles with "
                 f"{optimizer}…")
        res = minimize(objective, x0, args=(r,), method=optimizer,
                       options={"maxiter": maxiter})
        results.append({"restart": r, "value": float(res.fun), "evaluations": int(res.nfev),
                        "x": [float(v) for v in res.x], "x0": [float(v) for v in x0],
                        "message": str(getattr(res, "message", ""))})
    best_x = best_overall["x"]
    optimise_time = time.time() - started

    probs = evaluator.probabilities(best_x)
    expectation = float(probs @ energy)
    uniform = optimum["uniform"]
    span = (e_max - e_min) or 1.0

    # where the probability mass sits, level by level, against uniform guessing
    levels, inverse = vectors["levels"], vectors["inverse"]
    mass = np.bincount(inverse, weights=probs, minlength=len(levels))
    share = np.bincount(inverse, minlength=len(levels)) / len(energy)
    feasible_mass = np.bincount(inverse, weights=probs * feasible, minlength=len(levels))
    shown = min(len(levels), 24)
    level_rows = [{"energy": float(levels[k]), "objective": float(levels[k]) + problem.offset,
                   "qaoa": float(mass[k]), "uniform": float(share[k]),
                   "feasibleQaoa": float(feasible_mass[k])} for k in range(shown)]
    if len(levels) > shown:
        level_rows.append({"energy": float(levels[shown]), "rest": True,
                           "qaoa": float(mass[shown:].sum()),
                           "uniform": float(share[shown:].sum()),
                           "feasibleQaoa": float(feasible_mass[shown:].sum())})

    top = np.argsort(-probs, kind="stable")[:25]
    states = []
    for k in top:
        described = _strip(problem.describe(_bits(k, problem.n), ctx))
        described["probability"] = float(probs[k])
        described["amplificationVsUniform"] = float(probs[k] * len(probs))
        states.append(described)

    # sampling the tuned circuit, as the hardware would
    sampling = {"shots": shots}
    if noisy:
        counts, info = _noisy_sampling(job, problem, reps, best_x, noisy, shots, seed)
        sampling.update(info)
    else:
        idx, cnt = _sample_from_probs(probs, shots, rng)
        counts = Counter({_bits(i, problem.n): int(c) for i, c in zip(idx, cnt)})
        sampling["source"] = "ideal statevector, sampled"
    label = "QAOA p=%d (%s)" % (reps, f"noisy {noisy}" if noisy else "ideal simulation")
    scores = score_counts(problem, counts, ctx, label=label)

    return {
        "reps": reps, "optimizer": optimizer, "maxiter": maxiter, "restarts": restarts,
        "simulator": getattr(evaluator, "label", None),
        "cvar": alpha, "shotNoise": shot_noise, "seed": seed,
        "parameters": [float(v) for v in best_x],
        "parameterNames": [p.name for p in evaluator.params],
        "restartResults": results,
        "trace": trace,
        "evaluations": len(trace),
        "optimiseSeconds": optimise_time,
        "secondsPerEvaluation": optimise_time / max(1, len(trace)),
        "expectation": expectation,
        "expectationObjective": expectation + problem.offset,
        "approximationRatio": float((e_max - expectation) / span),
        "uniformApproximationRatio": float((e_max - uniform["meanEnergy"]) / span),
        "pOptimum": float(probs[at_opt].sum()),
        "pFeasible": float(probs[feasible].sum()),
        "optimum": optimum,
        "levels": level_rows,
        "states": states,
        "sampling": sampling,
        "scores": scores,
        "noiseDevice": noisy,
        "tts": _qaoa_tts(float(probs[at_opt].sum()), scores),
    }


def _qaoa_tts(p_exact, scores):
    shots_needed = None
    if p_exact > 0:
        _, shots_needed = time_to_solution(p_exact, 1.0)
    return {"pExact": p_exact, "shotsFor99": shots_needed,
            "pSampled": scores.get("optimumFraction")}


def _noisy_sampling(job, problem, reps, params, device, shots, seed):
    from qiskit import transpile

    simulator = noisy_simulator(device)
    real = next((d[1] for d in DEVICES if d[0] == device), device)
    job.note(f"transpiling for the {real} noise model…")
    circuit = measured(ansatz(problem, reps)["circuit"], params)
    isa = transpile(circuit, simulator, optimization_level=3, seed_transpiler=seed)
    started = time.time()
    probe = 8
    counts = Counter()

    def add(result_counts):
        for key, value in result_counts.items():
            bits = key.replace(" ", "")
            counts[tuple(int(bits[-1 - i]) for i in range(problem.n))] += int(value)

    add(simulator.run(isa, shots=probe, seed_simulator=seed).result().get_counts())
    per_shot = (time.time() - started) / probe
    job.note(f"noisy simulation of {real}: ~{per_shot * 1000:.0f} ms per shot, "
             f"about {per_shot * shots:.0f}s for {shots} shots")
    done, chunk, k = probe, max(16, int(2.0 / max(per_shot, 1e-4))), 1
    while done < shots:
        if job.cancelled:
            break
        take = min(chunk, shots - done)
        add(simulator.run(isa, shots=take, seed_simulator=seed + k).result().get_counts())
        done += take
        k += 1
        job.progress({"noisyShots": done, "of": shots})
    backend = fake_backend(device)
    estimate = device_estimate(backend, isa, shots, 0, label=real)
    return counts, {"source": f"Aer with the {real} noise model ({device})",
                    "shots": done, "secondsPerShot": per_shot,
                    "partial": done < shots, "device": estimate}


def qaoa_landscape(job, problem, opts):
    """<E>(beta, gamma) for p = 1 on a grid, by direct statevector algebra,
    cross-checked against Aer at the best grid point. beta in [0, pi) and
    gamma >= 0 cover every distinct p = 1 state: the probabilities have
    period pi in beta and are unchanged by (gamma, beta) -> (-gamma, -beta)."""
    if problem.n > EXACT_LIMIT:
        raise ValueError(f"{problem.n} qubits is beyond the {EXACT_LIMIT}-qubit "
                         "statevector limit")
    scale = max(1.0, problem.coefficient_scale())
    beta_max = float(opts.get("betaMax") or math.pi)
    gamma_max = float(opts.get("gammaMax") or (2 * math.pi / scale))
    vectors = problem.vectors()
    energy, feasible = vectors["energy"], vectors["feasible"]
    optimum = problem.optimum()
    at_opt = np.isclose(energy, optimum["energy"])

    started = time.time()
    numpy_qaoa_probabilities(energy, problem.n, [0.1], [0.1])
    per_point = max(time.time() - started, 1e-5)
    if opts.get("grid"):
        size = int(opts["grid"])
    else:                                   # about 20 s of work, 12..40 points a side
        size = int(math.sqrt(20.0 / per_point))
    size = max(12, min(size, 48))
    job.note(f"{size} x {size} grid, ~{per_point * 1000:.0f} ms per point")

    betas = np.linspace(0, beta_max, size, endpoint=False)
    gammas = np.linspace(0, gamma_max, size)
    grid = np.zeros((size, size))
    p_opt = np.zeros((size, size))
    p_feasible = np.zeros((size, size))
    for gi, gamma in enumerate(gammas):
        if job.cancelled:
            raise Cancelled()
        for bi, beta in enumerate(betas):
            probs = numpy_qaoa_probabilities(energy, problem.n, [beta], [gamma])
            grid[gi, bi] = probs @ energy
            p_opt[gi, bi] = probs[at_opt].sum()
            p_feasible[gi, bi] = probs[feasible].sum()
        job.progress({"row": gi + 1, "of": size})
    gi, bi = np.unravel_index(np.argmin(grid), grid.shape)
    check = None
    try:
        evaluator = _StatevectorEvaluator(problem, 1)
        aer = float(evaluator.probabilities([betas[bi], gammas[gi]]) @ energy)
        check = abs(aer - grid[gi, bi])
    except Exception:
        pass
    return {
        "betas": betas.tolist(), "gammas": gammas.tolist(),
        "energy": grid.tolist(), "pOptimum": p_opt.tolist(),
        "pFeasible": p_feasible.tolist(),
        "best": {"beta": float(betas[bi]), "gamma": float(gammas[gi]),
                 "energy": float(grid[gi, bi]), "pOptimum": float(p_opt[gi, bi])},
        "uniformEnergy": optimum["uniform"]["meanEnergy"],
        "optimumEnergy": optimum["energy"],
        "aerCheck": check,
        "seconds": time.time() - started,
    }


# ===================================================================== exports
def export(problem, what, reps=1, params=None, embedding_id=None):
    """(filename, content type, bytes) for the download buttons."""
    from qiskit import qasm2, qasm3, qpy
    stem = os.path.splitext(problem.name)[0]
    if what in ("qasm3", "qasm3-bound", "qasm2-bound", "qpy", "ibm-script"):
        circuit = ansatz(problem, reps)["circuit"]
        if what == "qasm3":
            return (f"{stem}-qaoa-p{reps}.qasm", "text/plain",
                    qasm3.dumps(measured(circuit)).encode())
        if params is None:
            raise ValueError("this export needs angles: run the simulation first, or "
                             "pass params")
        if len(params) != 2 * int(reps):
            raise ValueError(f"p={reps} needs {2 * int(reps)} angles, got {len(params)}")
        bound = measured(circuit, list(params))
        if what == "qasm3-bound":
            return (f"{stem}-qaoa-p{reps}-bound.qasm", "text/plain",
                    qasm3.dumps(bound).encode())
        if what == "qasm2-bound":
            from qiskit import transpile
            basis = transpile(bound, basis_gates=["rz", "sx", "x", "cx"],
                              optimization_level=1)
            return (f"{stem}-qaoa-p{reps}-composer.qasm", "text/plain",
                    qasm2.dumps(basis).encode())
        if what == "qpy":
            buffer = io.BytesIO()
            qpy.dump(measured(circuit), buffer)
            return f"{stem}-qaoa-p{reps}.qpy", "application/octet-stream", buffer.getvalue()
        script = f"run_{stem.replace('-', '_')}_qaoa.py"
        return (script, "text/x-python",
                _ibm_script(problem, reps, params, qasm3.dumps(bound), script).encode())
    if what == "hamiltonian":
        info = qaoa_build(problem, reps)
        lines = [f"# cost Hamiltonian of {problem.name}: H = sum h_i Z_i + sum J_ij Z_i Z_j",
                 f"# Ising offset {info['isingOffset']}, QUBO offset {problem.offset}",
                 "# x_i = (1 - Z_i)/2, so a measured 1 is a selected column"]
        for t in info["hamiltonian"]:
            lines.append(f"{t['coeff']:+.6g}  " + " ".join(f"Z{q}" for q in t["qubits"]))
        return f"{stem}-hamiltonian.txt", "text/plain", "\n".join(lines).encode()
    if what == "bqm-json":
        payload = problem.bqm().to_serializable()
        payload["info"] = {"source": problem.name, "offset": problem.offset,
                           "names": {i: problem.variables[i].name for i in range(problem.n)}}
        return f"{stem}-bqm.json", "application/json", json.dumps(payload, indent=1).encode()
    if what == "embedding-json":
        entry = stored_embedding(embedding_id, problem)
        if entry is None:
            raise ValueError("no embedding found for this QUBO; compute one first")
        payload = {"qubo": problem.name, "topology": entry["topology"],
                   "chains": {problem.variables[v].name: chain
                              for v, chain in sorted(entry["embedding"].items())},
                   "embedding": {str(v): chain for v, chain in entry["embedding"].items()}}
        return (f"{stem}-embedding-{entry['topology']}.json", "application/json",
                json.dumps(payload, indent=1).encode())
    if what == "dwave-script":
        script = f"run_{stem.replace('-', '_')}_dwave.py"
        return script, "text/x-python", _dwave_script(problem, script).encode()
    raise ValueError(f"unknown export {what!r}")


def _ibm_script(problem, reps, params, qasm, script_name):
    names = {i: problem.variables[i].name for i in range(problem.n)}
    return f'''#!/usr/bin/env python3
"""QAOA p={reps} for {problem.name}, exported by the RBRP GUI.

Runs the tuned circuit on IBM Quantum hardware with Qiskit Runtime's
SamplerV2 and decodes the counts back to QUBO columns.

    pip install qiskit qiskit-ibm-runtime qiskit-qasm3-import
    python {script_name} [backend]     # default: least busy

Needs a saved account: QiskitRuntimeService.save_account(channel=
"ibm_quantum_platform", token=..., instance=...).
"""
import sys
from collections import Counter

from qiskit import qasm3
from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager
from qiskit_ibm_runtime import QiskitRuntimeService, SamplerV2

ANGLES = {[float(v) for v in params]!r}   # beta_0..beta_p-1, gamma_0..gamma_p-1
NAMES = {names!r}
OFFSET = {problem.offset!r}               # energy + OFFSET = relocations
QASM = """{qasm}"""

service = QiskitRuntimeService()
backend = (service.backend(sys.argv[1]) if len(sys.argv) > 1
           else service.least_busy(operational=True, simulator=False))
circuit = qasm3.loads(QASM)                 # measured logical circuit: bit i = column i
isa = generate_preset_pass_manager(optimization_level=3, backend=backend).run(circuit)
print(f"{{backend.name}}: depth {{isa.depth()}}, ops {{dict(isa.count_ops())}}")
job = SamplerV2(mode=backend).run([isa], shots=4096)
print("job id:", job.job_id())
counts = job.result()[0].data.meas.get_counts()
for bits, n in Counter(counts).most_common(10):
    chosen = [NAMES[i] for i in range(len(NAMES)) if bits[-1 - i] == "1"]
    print(bits, n, " ".join(chosen))
'''


def _dwave_script(problem, script_name):
    payload = problem.bqm().to_serializable()
    names = {i: problem.variables[i].name for i in range(problem.n)}
    return f'''#!/usr/bin/env python3
"""{problem.name} on a D-Wave QPU, exported by the RBRP GUI.

    pip install dwave-ocean-sdk
    dwave config create          # or: export DWAVE_API_TOKEN=...
    python {script_name}
"""
import dimod
from dwave.system import DWaveSampler, EmbeddingComposite

BQM = dimod.BinaryQuadraticModel.from_serializable({json.dumps(payload)})
NAMES = {names!r}
OFFSET = {problem.offset!r}   # energy + OFFSET = relocations of a feasible selection

sampleset = EmbeddingComposite(DWaveSampler()).sample(
    BQM, num_reads=1000, annealing_time=20, label="RBRP QUBO")
best = sampleset.first
print("energy", best.energy, "-> objective", best.energy + OFFSET)
print("columns:", sorted(NAMES[v] for v, x in best.sample.items() if x))
print("QPU access time (us):", sampleset.info["timing"]["qpu_access_time"])
'''


# ===================================================================== IBM
_services = {}
_local_jobs = {}


def _service(creds):
    from qiskit_ibm_runtime import QiskitRuntimeService
    creds = creds or {}
    token = (creds.get("token") or "").strip()
    instance = (creds.get("instance") or "").strip() or None
    channel = creds.get("channel") or "ibm_quantum_platform"
    key = (token[-8:] if token else "saved", instance, channel)
    with _lock:
        if key in _services:
            return _services[key]
    if token:
        service = QiskitRuntimeService(channel=channel, token=token, instance=instance)
        if creds.get("save"):
            QiskitRuntimeService.save_account(channel=channel, token=token,
                                              instance=instance, overwrite=True,
                                              set_as_default=True)
    else:
        service = QiskitRuntimeService()
    with _lock:
        _services[key] = service
    return service


def ibm_backends(creds):
    service = _service(creds)
    out = []
    for backend in service.backends(simulator=False):
        entry = {"name": backend.name, "qubits": backend.num_qubits}
        try:
            st = backend.status()
            entry.update(pending=st.pending_jobs, operational=st.operational,
                         message=st.status_msg)
        except Exception as exc:
            entry["statusError"] = str(exc)
        try:
            entry["processor"] = backend.configuration().processor_type
        except Exception:
            pass
        out.append(entry)
    out.sort(key=lambda b: (not b.get("operational", False), b.get("pending", 1e9)))
    return {"backends": out}


def _ibm_backend(creds, name):
    if name.startswith("local:"):
        return fake_backend(name.split(":", 1)[1]), True
    service = _service(creds)
    if not name:
        return service.least_busy(operational=True, simulator=False), False
    return service.backend(name), False


def ibm_check(problem, creds, opts):
    """Transpile against a live device's current target -- no job is sent."""
    from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager
    reps = int(opts.get("reps") or 1)
    shots = int(opts.get("shots") or 4096)
    backend, local = _ibm_backend(creds, opts.get("backend") or "")
    if problem.n > backend.num_qubits:
        raise ValueError(f"{problem.n} qubits do not fit on {backend.name} "
                         f"({backend.num_qubits})")
    pm = generate_preset_pass_manager(optimization_level=3, backend=backend,
                                      seed_transpiler=int(opts.get("seed") or 7))
    isa = pm.run(measured(ansatz(problem, reps)["circuit"]))
    estimate = device_estimate(backend, isa, shots, 0, label=backend.name)
    estimate["local"] = local
    return estimate


def ibm_submit(problem, creds, opts):
    """Send the tuned circuit as one SamplerV2 job. Returns at once with the
    job id; the result is fetched later (queues can take hours)."""
    from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager
    from qiskit_ibm_runtime import SamplerV2

    reps = int(opts.get("reps") or 1)
    params = opts.get("params")
    if not params or len(params) != 2 * reps:
        raise ValueError(f"p={reps} needs {2 * reps} tuned angles: run the QAOA "
                         "simulation first")
    shots = max(1, min(int(opts.get("shots") or 4096), 100000))
    backend, local = _ibm_backend(creds, opts.get("backend") or "")
    pm = generate_preset_pass_manager(optimization_level=3, backend=backend,
                                      seed_transpiler=int(opts.get("seed") or 7))
    isa = pm.run(measured(ansatz(problem, reps)["circuit"]))
    if isa.num_clbits != problem.n:
        raise RuntimeError(f"expected {problem.n} measured bits, got {isa.num_clbits}")
    sampler = SamplerV2(mode=backend)
    job = sampler.run([(isa, [float(v) for v in params])], shots=shots)
    job_id = job.job_id()
    record = {
        "jobId": job_id, "backend": backend.name, "local": local,
        "qubo": problem.name, "quboPath": problem.path, "reps": reps,
        "params": [float(v) for v in params], "shots": shots, "n": problem.n,
        "submitted": time.strftime("%Y-%m-%d %H:%M:%S"),
        "estimate": device_estimate(backend, isa, shots, 0, label=backend.name),
    }
    if local:
        with _lock:
            _local_jobs[job_id] = job
    _remember_ibm_job(record)
    return record


def _remember_ibm_job(record):
    os.makedirs(RUNS_DIR, exist_ok=True)
    jobs = ibm_job_log()
    jobs = [j for j in jobs if j.get("jobId") != record["jobId"]]
    jobs.insert(0, {k: v for k, v in record.items() if k != "estimate"})
    with open(IBM_JOBS_FILE, "w") as f:
        json.dump(jobs[:200], f, indent=1)


def ibm_job_log():
    try:
        with open(IBM_JOBS_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def ibm_job(problem, ctx, creds, job_id, old_sign=False):
    """Status of a submitted job and, once it is done, its decoded result."""
    with _lock:
        job = _local_jobs.get(job_id)
    local = job is not None
    if job is None:
        job = _service(creds).job(job_id)
    status = job.status()
    status = getattr(status, "name", str(status))
    out = {"jobId": job_id, "status": status, "local": local}
    try:
        out["backend"] = job.backend().name
    except Exception:
        pass
    if not local:
        try:
            metrics = job.metrics()
            out["usage"] = metrics.get("usage")
            out["timestamps"] = metrics.get("timestamps")
        except Exception:
            pass
    if status not in ("DONE", "COMPLETED"):
        return out

    result = job.result()
    pub = result[0]
    field = getattr(pub.data, "meas", None)
    if field is None:
        field = getattr(pub.data, next(iter(pub.data.keys())))
    raw = field.get_counts()
    num_bits = field.num_bits
    if num_bits == problem.n:
        clbit_of = list(range(problem.n))
        how = f"{problem.n} measured bits = QUBO columns"
    else:
        circuit = job.inputs["pubs"][0][0]
        layout = circuit.layout
        if layout is None:
            raise ValueError(f"job measured {num_bits} bits for {problem.n} columns and "
                             "its circuit has no layout to map them back")
        physical = layout.final_index_layout()[:problem.n]
        measured_on = {}
        for inst in circuit.data:
            if inst.operation.name == "measure":
                measured_on[circuit.find_bit(inst.qubits[0]).index] = \
                    circuit.find_bit(inst.clbits[0]).index
        clbit_of = [measured_on[q] for q in physical]
        how = f"{num_bits} measured bits, columns read from physical qubits {physical}"
    counts = Counter()
    for bitstring, value in raw.items():
        bits = tuple(int(bitstring[-1 - c]) for c in clbit_of)
        if old_sign:
            bits = tuple(1 - b for b in bits)
        counts[bits] += int(value)
    out["decoding"] = how + (" (complemented: --old-sign)" if old_sign else "")
    out["scores"] = score_counts(problem, counts, ctx,
                                 label=f"QAOA on {out.get('backend', 'IBM')}")
    return out


# ===================================================================== jobs
class Job:
    """A long computation run on a worker thread; the browser polls it."""

    def __init__(self, kind):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.status = "running"
        self.notes = []
        self.points = []
        self.result = None
        self.error = None
        self.cancelled = False
        self.started = time.time()
        self.finished = None

    def note(self, text):
        self.notes.append({"t": time.time() - self.started, "text": text})

    def progress(self, point):
        self.points.append(point)

    def view(self, since=0):
        return {"id": self.id, "kind": self.kind, "status": self.status,
                "elapsed": (self.finished or time.time()) - self.started,
                "notes": self.notes[-6:], "points": self.points[since:],
                "pointCount": len(self.points),
                "result": self.result if self.status == "done" else None,
                "error": self.error}


_jobs = {}


def start_job(kind, fn, *args):
    job = Job(kind)
    with _lock:
        stale = [k for k, j in _jobs.items()
                 if j.finished and time.time() - j.finished > 3600]
        for k in stale:
            _jobs.pop(k, None)
        _jobs[job.id] = job

    def run():
        try:
            job.result = fn(job, *args)
            job.status = "done"
        except Cancelled:
            job.status = "cancelled"
        except Exception as exc:
            job.status = "error"
            job.error = f"{type(exc).__name__}: {exc}"
        finally:
            job.finished = time.time()

    _job_pool.submit(run)
    return job


def get_job(job_id):
    with _lock:
        return _jobs.get(job_id)


def cancel_job(job_id):
    job = get_job(job_id)
    if job:
        job.cancelled = True
    return bool(job)


def _clean(value):
    """numpy scalars/arrays to plain JSON values; NaN and infinities to null."""
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_clean(v) for v in value]
    if isinstance(value, np.ndarray):
        return _clean(value.tolist())
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        value = float(value)
        return value if math.isfinite(value) else None
    if isinstance(value, bytes):
        return base64.b64encode(value).decode()
    return value


def to_json(value):
    return json.dumps(_clean(value), default=str)
