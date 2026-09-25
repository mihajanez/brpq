"""Run the rbrp_ip binary and turn its output into structured results."""
import os
import re
import signal
import subprocess
import threading
import time

from .brp_instance import effective_tiers, replay

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BINARY = os.path.join(ROOT, "rbrp_ip")

RELOCATION_RE = re.compile(r"^Relocation\s+(\d+):\s*\[\s*(-?\d+)\s*\]\s*(\d+)->(\d+)")
STAT_RE = re.compile(r"^([a-z_]+)=(.*)$")
ITERATION_RE = re.compile(r"^iteration=(\d+),\s*time=([\d.]+)")

# Solver runs currently in flight, so the GUI can stop them: each entry is
# {"proc": Popen, "stopped": bool}, keyed by the run id the browser sent.
_runs = {}
_runs_lock = threading.Lock()


def _register(run_id, proc):
    with _runs_lock:
        _runs[run_id] = {"proc": proc, "stopped": False}


def _unregister(run_id):
    with _runs_lock:
        return _runs.pop(run_id, None)


def _kill(proc, grace=3.0):
    """Stop a solver and everything it started: the child gets its own process
    group, so one signal reaches the whole tree. SIGTERM first, SIGKILL if it
    does not go."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if proc.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.send_signal(sig)
            except (ProcessLookupError, OSError):
                return
        try:
            proc.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            continue


def stop_runs(run_id=None):
    """Stop one run, or every run when no id is given. Returns how many were hit."""
    with _runs_lock:
        entries = ([_runs[run_id]] if run_id in _runs
                   else list(_runs.values()) if run_id is None else [])
        for entry in entries:
            entry["stopped"] = True
    for entry in entries:
        _kill(entry["proc"])
    return len(entries)


# stderr keys the solver reports, and how to read them
INT_STATS = {"greedy_upper_bound", "iterations", "initial_number_of_variables",
             "initial_number_of_constraints", "final_number_of_variables",
             "final_number_of_constraints", "optimal_value", "lower_bound"}
FLOAT_STATS = {"total_time", "lb_time", "ub_time", "total_ip_time"}


class Options:
    """Command-line parameters of rbrp_ip, as the GUI exposes them."""

    FIELDS = ("emptyTiers", "maximumHeight", "timeLimit", "threads", "threshold",
              "disableGreedy", "disableUpperBound", "verbose", "exportQubo")

    def __init__(self, payload=None):
        payload = payload or {}
        self.empty_tiers = _opt_int(payload.get("emptyTiers"))
        self.maximum_height = _opt_int(payload.get("maximumHeight"))
        self.time_limit = _opt_float(payload.get("timeLimit"))
        self.threads = _opt_int(payload.get("threads"))
        self.threshold = _opt_int(payload.get("threshold"))
        self.disable_greedy = bool(payload.get("disableGreedy"))
        self.disable_upper_bound = bool(payload.get("disableUpperBound"))
        self.verbose = _opt_int(payload.get("verbose"))
        self.export_qubo = payload.get("exportQubo") or None

    def argv(self, instance_path):
        argv = [BINARY]
        if self.verbose:
            argv += ["-v", str(self.verbose)]
        if self.empty_tiers is not None and self.empty_tiers >= 0:
            argv += ["-E", str(self.empty_tiers)]
        if self.maximum_height:
            argv += ["-T", str(self.maximum_height)]
        if self.time_limit and self.time_limit > 0:
            argv += ["-t", str(self.time_limit)]
        if self.threads:
            argv += ["-m", str(self.threads)]
        if self.threshold is not None:
            argv += ["-s", str(self.threshold)]
        if self.disable_greedy:
            argv += ["-g"]
        if self.disable_upper_bound:
            argv += ["-u"]
        if self.export_qubo:
            argv += ["-Q", self.export_qubo]
        argv.append(instance_path)
        return argv


def _opt_int(value):
    # careful: 0 is a meaningful value here (-E 0 is the ZQLZ convention)
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _opt_float(value):
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_stats(stderr):
    """Read the key=value summary solve() writes to stderr."""
    stats = {"iterationLog": []}
    for line in stderr.splitlines():
        line = line.strip()
        iteration = ITERATION_RE.match(line)
        if iteration:
            stats["iterationLog"].append({"iteration": int(iteration.group(1)),
                                          "time": float(iteration.group(2))})
            continue
        if line in ("solved", "not solved"):
            stats["solved"] = (line == "solved")
            continue
        match = STAT_RE.match(line)
        if not match:
            continue
        key, raw = match.group(1), match.group(2).strip()
        if key in INT_STATS:
            try:
                stats[key] = int(raw)
            except ValueError:
                stats[key] = raw
        elif key in FLOAT_STATS:
            try:
                stats[key] = float(raw)
            except ValueError:
                stats[key] = raw
        elif key == "upper_bound":
            stats[key] = None if raw == "infeasible" else _opt_int(raw)
            stats["upperBoundInfeasible"] = (raw == "infeasible")
        else:
            stats[key] = raw
    return stats


def parse_relocations(stdout):
    """Pull the move plan out of Solution::print()'s diagram."""
    moves = []
    for line in stdout.splitlines():
        match = RELOCATION_RE.match(line.strip())
        if match:
            moves.append((int(match.group(2)), int(match.group(3)),
                          int(match.group(4))))
    return moves


def ip_solution_variables(stdout):
    for line in stdout.splitlines():
        if line.startswith("IP solution:"):
            return line.split(":", 1)[1].split()
    return []


def solve(instance, options, hard_timeout=None, run_id=None):
    """Run the solver on an already-parsed instance and build the GUI payload.
    The child runs in its own process group so stop_runs() can end it."""
    if not os.path.exists(BINARY):
        return {"ok": False,
                "error": f"solver binary not found at {BINARY} -- run 'make' first"}

    argv = options.argv(instance.path)
    if hard_timeout is None:
        hard_timeout = (options.time_limit + 120.0) if options.time_limit else 900.0

    started = time.time()
    timed_out = False
    proc = subprocess.Popen(argv, cwd=ROOT, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, start_new_session=True)
    _register(run_id, proc)
    try:
        stdout, stderr = proc.communicate(timeout=hard_timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill(proc)
        stdout, stderr = proc.communicate()
    finally:
        entry = _unregister(run_id)
    returncode = proc.returncode
    stopped = bool(entry and entry["stopped"])
    if stopped:
        timed_out = False

    wall_time = time.time() - started
    stats = parse_stats(stderr)
    relocations = parse_relocations(stdout)
    steps, replay_error = replay(instance, relocations)
    retrievals = sum(1 for s in steps if s["kind"] == "retrieve")

    objective = stats.get("optimal_value")
    if objective is None and relocations:
        objective = len(relocations)

    return {
        "ok": not timed_out and not stopped and returncode in (0, 1) and bool(relocations),
        "timedOut": timed_out,
        "stopped": stopped,
        "returncode": returncode,
        "command": " ".join(argv),
        "wallTime": wall_time,
        "hardTimeout": hard_timeout,
        "instance": instance.as_dict(),
        "heightLimit": effective_tiers(instance, options.empty_tiers,
                                       options.maximum_height),
        "stats": stats,
        "objective": objective,
        "relocations": [{"number": i + 1, "block": b, "src": s, "dst": d}
                        for i, (b, s, d) in enumerate(relocations)],
        "steps": steps,
        "retrievals": retrievals,
        "replayError": replay_error,
        "ipVariables": ip_solution_variables(stdout),
        "stdout": stdout,
        "stderr": stderr,
    }
