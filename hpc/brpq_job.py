#!/usr/bin/env python3
"""Run brpq tasks without the GUI: one Slurm array task at a time, or all in a row.

A job directory holds a spec.json written by the GUI's HPC tab (or by hand):

    {"name": "sweep-03", "tasks": [
        {"id": "000", "kind": "classical", "instance": "data03-05-03.dat",
         "options": {"emptyTiers": 2, "timeLimit": 600}, "exportQubo": true},
        {"id": "001", "kind": "pipeline", "instance": "data04-04-08.dat",
         "options": {"emptyTiers": 2},
         "anneal": [{"method": "sa", "reads": 1000}],
         "qaoa": [{"reps": 1, "restarts": 2, "maxiter": 100}]},
        {"id": "002", "kind": "anneal", "qubo": "inputs/data03-05-03-E2.qubo",
         "anneal": {"method": "tabu", "reads": 100}},
        {"id": "003", "kind": "qaoa", "qubo": "inputs/data03-05-03-E2.qubo",
         "qaoa": {"reps": 2, "restarts": 4, "maxiter": 200, "cvar": 0.25}}]}

Kinds:
  classical  solve the Tanaka-Voss IP with ./rbrp_ip (Gurobi), optionally export the QUBO
  pipeline   classical solve + QUBO export, then every annealing and QAOA configuration
             listed in the task on that QUBO
  anneal     one D-Wave sampler (simulated annealing, tabu, ...) on a given QUBO
  qaoa       QAOA simulation (angle optimisation + sampling) on a given QUBO
  embed      minor embedding of a given QUBO into a D-Wave topology

Usage:
    python3 hpc/brpq_job.py count JOBDIR/spec.json
    python3 hpc/brpq_job.py run JOBDIR/spec.json --task "$SLURM_ARRAY_TASK_ID"
    python3 hpc/brpq_job.py run JOBDIR/spec.json --all
    python3 hpc/brpq_job.py summarize JOBDIR

Each task writes JOBDIR/results/<id>.json (status, timings, Slurm context and the
same result payload the GUI shows); QUBO exports go to JOBDIR/results/qubo/.
"summarize" collects every task into JOBDIR/results/summary.json and summary.csv.

Environment (set by the generated sbatch script):
    BRPQ_EXACT_LIMIT     largest QUBO simulated exactly (default 24 in the GUI)
    BRPQ_QAOA_BACKEND    aer (Qiskit Aer statevector, CPU), numpy, or cupy (GPU)
    OMP_NUM_THREADS      threads for Aer and numpy
"""
import argparse
import csv
import json
import os
import platform
import socket
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from gui import server                                   # noqa: E402
from gui.runner import Options, solve                    # noqa: E402

KINDS = ("classical", "pipeline", "anneal", "qaoa", "embed")
SLURM_KEYS = ("SLURM_JOB_ID", "SLURM_ARRAY_JOB_ID", "SLURM_ARRAY_TASK_ID",
              "SLURM_JOB_PARTITION", "SLURM_JOB_NODELIST", "SLURMD_NODENAME",
              "SLURM_CPUS_PER_TASK", "SLURM_MEM_PER_NODE", "SLURM_GPUS_ON_NODE",
              "CUDA_VISIBLE_DEVICES", "SLURM_JOB_ACCOUNT")


# ------------------------------------------------------------------ helpers
def log(message):
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def load_spec(path):
    with open(path) as f:
        spec = json.load(f)
    tasks = spec.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError(f"{path}: 'tasks' must be a non-empty list")
    for k, task in enumerate(tasks):
        task.setdefault("id", f"{k:03d}")
        if task.get("kind") not in KINDS:
            raise ValueError(f"task {task['id']}: kind must be one of {', '.join(KINDS)}")
    return spec


def job_dir_of(spec_path):
    return os.path.dirname(os.path.abspath(spec_path))


def resolve(job_dir, path):
    """Paths in a spec are relative to the job directory, else to the repo."""
    if not path:
        return None
    if os.path.isabs(path):
        return path
    for base in (job_dir, ROOT):
        candidate = os.path.normpath(os.path.join(base, path))
        if os.path.exists(candidate):
            return candidate
    return os.path.normpath(os.path.join(job_dir, path))


def slurm_context():
    ctx = {k: os.environ[k] for k in SLURM_KEYS if k in os.environ}
    ctx["host"] = socket.gethostname()
    ctx["python"] = platform.python_version()
    ctx["qaoaBackend"] = os.environ.get("BRPQ_QAOA_BACKEND", "aer")
    ctx["exactLimit"] = os.environ.get("BRPQ_EXACT_LIMIT")
    return ctx


def cpus():
    try:
        return int(os.environ.get("SLURM_CPUS_PER_TASK") or 0) or len(os.sched_getaffinity(0))
    except (AttributeError, ValueError):
        return os.cpu_count() or 1


def quantum_module():
    from gui import quantum
    return quantum


class PrintingJob:
    """Stands in for the GUI's background-job object: progress goes to the log."""

    def __init__(self, label):
        self.label = label
        self.cancelled = False
        self.points = 0

    def note(self, text):
        log(f"{self.label}: {text}")

    def progress(self, point):
        self.points += 1
        if "eval" in point and (point["eval"] <= 3 or point["eval"] % 25 == 0):
            log(f"{self.label}: eval {point['eval']} value={point.get('value'):.6g} "
                f"P(opt)={point.get('pOpt', 0):.4g}")


def trim_embedding(result):
    """The chip drawing data is only for the GUI's canvas."""
    return {k: v for k, v in result.items()
            if k not in ("xy", "chains", "chainEdges", "couplers")}


# --------------------------------------------------------------- task kinds
def run_classical(task, job_dir, out_dir):
    payload = dict(task.get("options") or {})
    instance = server.load_instance(task["instance"])
    options = Options(payload)
    if options.threads is None and task.get("useAllCpus", True):
        options.threads = cpus()
    qubo_path = None
    if task.get("exportQubo", task["kind"] == "pipeline"):
        os.makedirs(os.path.join(out_dir, "qubo"), exist_ok=True)
        qubo_path = os.path.join(out_dir, "qubo", server.export_name(payload, instance))
        options.export_qubo = qubo_path
    hard = task.get("hardTimeout") or (
        options.time_limit + 600 if options.time_limit else 7 * 24 * 3600)
    log(f"classical: {instance.name} -> {' '.join(options.argv(instance.path)[1:])}")
    result = solve(instance, options, hard_timeout=hard)
    if not result.get("ok") and result.get("error"):
        raise RuntimeError(result["error"])
    log(f"classical: objective={result.get('objective')} "
        f"time={result.get('wallTime', 0):.1f}s ok={result.get('ok')}")
    if qubo_path and os.path.isfile(qubo_path):
        payload["instance"] = task["instance"]
        meta = server.write_qubo_meta(qubo_path, payload, instance, result)
        result["quboExport"] = {"name": "qubo/" + os.path.basename(qubo_path),
                                "path": os.path.relpath(qubo_path, job_dir), "meta": meta}
    return result


def quantum_problem(task, job_dir, qubo_file):
    quantum = quantum_module()
    path = resolve(job_dir, qubo_file)
    if not path or not os.path.isfile(path):
        raise FileNotFoundError(f"QUBO file not found: {qubo_file}")
    problem = quantum.problem_for(path)
    context_payload = {"instance": task.get("instance") or "",
                       **(task.get("options") or {})}
    instance, limit, source = server.replay_context(context_payload, path)
    return problem, (instance, limit), source


def run_anneal(task, job_dir, qubo_file, opts):
    quantum = quantum_module()
    problem, ctx, source = quantum_problem(task, job_dir, qubo_file)
    opts = dict(opts or {})
    method = opts.get("method") or "sa"
    embedding = None
    if method == "sa-embedded" and not opts.get("embeddingId"):
        log(f"anneal: embedding {problem.name} into {opts.get('topology') or 'pegasus'}")
        embedding = quantum.embed(problem, opts.get("topology") or "pegasus",
                                  opts.get("seed") or 1, opts.get("tries") or 10,
                                  opts.get("embedTimeout") or 300)
        opts["embeddingId"] = embedding["id"]
    log(f"anneal: {method} on {problem.name} ({problem.n} variables)")
    result = quantum.anneal(problem, ctx, opts)
    result["contextSource"] = source
    result["qubo"] = qubo_file
    if embedding:
        result["embedding"] = trim_embedding(embedding)
    s = result["scores"]
    log(f"anneal: best objective={s.get('bestObjective')} "
        f"feasible={s.get('feasibleFraction'):.3f} wall={result.get('wallTime', 0):.2f}s")
    return result


def run_qaoa(task, job_dir, qubo_file, opts):
    quantum = quantum_module()
    problem, ctx, source = quantum_problem(task, job_dir, qubo_file)
    opts = dict(opts or {})
    log(f"qaoa: p={opts.get('reps') or 1} on {problem.name} ({problem.n} qubits), "
        f"backend {os.environ.get('BRPQ_QAOA_BACKEND', 'aer')}")
    result = quantum.qaoa_run(PrintingJob("qaoa"), problem, ctx, opts)
    result["contextSource"] = source
    result["qubo"] = qubo_file
    log(f"qaoa: <E>={result['expectation']:.4f} r={result['approximationRatio']:.4f} "
        f"P(opt)={result['pOptimum']:.4g} in {result['optimiseSeconds']:.1f}s")
    if opts.get("estimate"):
        est = dict(opts)
        est.setdefault("devices", ["FakeFez", "FakeTorino"])
        result["estimate"] = quantum.qaoa_estimate(PrintingJob("estimate"), problem, est)
    return result


def run_embed(task, job_dir, qubo_file, opts):
    quantum = quantum_module()
    problem, _, _ = quantum_problem(task, job_dir, qubo_file)
    opts = dict(opts or {})
    result = quantum.embed(problem, opts.get("topology") or "pegasus",
                           opts.get("seed") or 1, opts.get("tries") or 10,
                           opts.get("timeout") or 300)
    return trim_embedding(result)


def as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def run_task(task, job_dir, out_dir):
    kind = task["kind"]
    if kind == "classical":
        return {"classical": run_classical(task, job_dir, out_dir)}
    if kind == "pipeline":
        classical = run_classical(task, job_dir, out_dir)
        out = {"classical": classical, "anneal": [], "qaoa": []}
        export = classical.get("quboExport") or {}
        qubo_file = export.get("path")
        if not qubo_file:
            out["error"] = "no QUBO was exported, so the quantum steps were skipped"
            return out
        for opts in as_list(task.get("anneal")):
            out["anneal"].append(_guarded(run_anneal, task, job_dir, qubo_file, opts))
        for opts in as_list(task.get("qaoa")):
            out["qaoa"].append(_guarded(run_qaoa, task, job_dir, qubo_file, opts))
        return out
    qubo_file = task.get("qubo")
    if kind == "anneal":
        return {"anneal": [run_anneal(task, job_dir, qubo_file, task.get("anneal"))]}
    if kind == "qaoa":
        return {"qaoa": [run_qaoa(task, job_dir, qubo_file, task.get("qaoa"))]}
    return {"embed": run_embed(task, job_dir, qubo_file, task.get("embed"))}


def _guarded(fn, *args):
    """One failing configuration of a pipeline must not lose the others."""
    try:
        return fn(*args)
    except Exception as exc:
        log(f"{fn.__name__}: FAILED {type(exc).__name__}: {exc}")
        return {"error": f"{type(exc).__name__}: {exc}", "options": args[-1]}


def write_json(path, value):
    try:
        from gui import quantum
        text = quantum.to_json(value)
    except Exception:
        text = json.dumps(value, default=str)
    tmp = path + ".part"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def execute(spec_path, index, out_dir):
    spec = load_spec(spec_path)
    tasks = spec["tasks"]
    if not 0 <= index < len(tasks):
        raise IndexError(f"task {index} out of range (spec has {len(tasks)})")
    task = tasks[index]
    job_dir = job_dir_of(spec_path)
    os.makedirs(out_dir, exist_ok=True)
    record = {"task": task, "index": index, "spec": spec.get("name"),
              "slurm": slurm_context(), "status": "running",
              "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    target = os.path.join(out_dir, f"{task['id']}.json")
    write_json(target, record)
    log(f"task {task['id']} ({task['kind']}) on {record['slurm']['host']}, {cpus()} CPUs")
    t0 = time.time()
    try:
        record["result"] = run_task(task, job_dir, out_dir)
        record["status"] = "done"
    except Exception as exc:
        record["status"] = "error"
        record["error"] = f"{type(exc).__name__}: {exc}"
        record["traceback"] = traceback.format_exc()
        log(f"task {task['id']} FAILED: {record['error']}")
    record["seconds"] = time.time() - t0
    record["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    write_json(target, record)
    log(f"task {task['id']} {record['status']} in {record['seconds']:.1f}s -> {target}")
    return record["status"] == "done"


# ---------------------------------------------------------------- summarize
def _scores_row(base, kind, label, result):
    s = (result or {}).get("scores") or {}
    row = dict(base, kind=kind, method=label,
               best_objective=s.get("bestObjective"),
               feasible_fraction=s.get("feasibleFraction"),
               optimum_fraction=s.get("optimumFraction"),
               mean_energy=s.get("meanEnergy"),
               shots=s.get("shots"),
               seconds=(result or {}).get("wallTime") or (result or {}).get("optimiseSeconds"),
               error=(result or {}).get("error"))
    if kind == "qaoa" and result and "approximationRatio" in result:
        row["approximation_ratio"] = result["approximationRatio"]
        row["p_optimum_exact"] = result.get("pOptimum")
    return row


def summarize(job_dir):
    results = os.path.join(job_dir, "results")
    rows = []
    for name in sorted(os.listdir(results)) if os.path.isdir(results) else []:
        if not name.endswith(".json") or name.startswith("summary"):
            continue
        with open(os.path.join(results, name)) as f:
            record = json.load(f)
        task = record.get("task") or {}
        base = {"task": task.get("id"), "instance": task.get("instance"),
                "qubo": task.get("qubo"), "status": record.get("status"),
                "node": (record.get("slurm") or {}).get("host"),
                "task_seconds": record.get("seconds")}
        result = record.get("result") or {}
        classical = result.get("classical")
        ip = None
        if task.get("qubo"):                     # IP optimum from the export record
            try:
                with open(resolve(job_dir, task["qubo"]) + ".json") as f:
                    ip = json.load(f).get("ipObjective")
            except (OSError, ValueError, TypeError):
                pass
        if classical:
            ip = classical.get("objective")
            stats = classical.get("stats") or {}
            rows.append(dict(base, kind="classical", method="IP (Gurobi)",
                             best_objective=ip,
                             proven_optimal=stats.get("optimal_value") is not None,
                             seconds=stats.get("total_time", classical.get("wallTime")),
                             qubo_export=(classical.get("quboExport") or {}).get("path")))
        qubo = task.get("qubo") or ((classical or {}).get("quboExport") or {}).get("path")
        for k, r in enumerate(result.get("anneal") or []):
            label = (r or {}).get("methodLabel") or ((r or {}).get("options") or {}).get("method")
            rows.append(_scores_row(dict(base, ip_objective=ip, sub=k, qubo=qubo),
                                    "anneal", label, r))
        for k, r in enumerate(result.get("qaoa") or []):
            reps = (r or {}).get("reps") or ((r or {}).get("options") or {}).get("reps") or "?"
            rows.append(_scores_row(dict(base, ip_objective=ip, sub=k, qubo=qubo),
                                    "qaoa", f"QAOA p={reps}", r))
        if result.get("embed"):
            e = result["embed"]
            rows.append(dict(base, kind="embed", method=e.get("label"),
                             physical_qubits=e.get("physical"), max_chain=e.get("maxChain")))
        if record.get("status") != "done" and not result:
            rows.append(dict(base, kind=task.get("kind"), error=record.get("error")))
    with open(os.path.join(results, "summary.json"), "w") as f:
        json.dump(rows, f, indent=1, default=str)
    columns = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    with open(os.path.join(results, "summary.csv"), "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return rows


# --------------------------------------------------------------------- main
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p_count = sub.add_parser("count", help="print the number of tasks in a spec")
    p_count.add_argument("spec")
    p_run = sub.add_parser("run", help="run one task (or all) of a spec")
    p_run.add_argument("spec")
    which = p_run.add_mutually_exclusive_group(required=True)
    which.add_argument("--task", type=int, help="task index (SLURM_ARRAY_TASK_ID)")
    which.add_argument("--all", action="store_true", help="run every task in a row")
    p_run.add_argument("--out", help="results directory (default: JOBDIR/results)")
    p_sum = sub.add_parser("summarize", help="collect results into summary.json/.csv")
    p_sum.add_argument("jobdir")
    args = parser.parse_args(argv)

    if args.command == "count":
        print(len(load_spec(args.spec)["tasks"]))
        return 0
    if args.command == "summarize":
        rows = summarize(os.path.abspath(args.jobdir))
        print(f"{len(rows)} rows -> {os.path.join(args.jobdir, 'results', 'summary.csv')}")
        return 0
    out_dir = args.out or os.path.join(job_dir_of(args.spec), "results")
    if args.all:
        n = len(load_spec(args.spec)["tasks"])
        ok = [execute(args.spec, k, out_dir) for k in range(n)]
        summarize(job_dir_of(args.spec))
        return 0 if all(ok) else 1
    return 0 if execute(args.spec, args.task, out_dir) else 1


if __name__ == "__main__":
    sys.exit(main())
