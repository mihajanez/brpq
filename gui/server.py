#!/usr/bin/env python3
"""Local web GUI for the restricted BRP solver.

    .venv/bin/python -m gui.server          # then open http://127.0.0.1:8000

Standard library only: it serves gui/static and exposes a small JSON API that
lists the instances in data/, runs ./rbrp_ip with the chosen parameters and
returns the move plan step by step.
"""
import argparse
import json
import mimetypes
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import qubo as qubo_model
from .brp_instance import effective_tiers, read_instance, validate_bay, write_dat
from .runner import ROOT, BINARY, Options, solve, stop_runs

try:                                    # needs the project's .venv; the classical
    from . import quantum               # GUI runs without it
    QUANTUM_ERROR = None
except Exception as _exc:               # pragma: no cover - depends on the env
    quantum = None
    QUANTUM_ERROR = f"{type(_exc).__name__}: {_exc}"

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
DATA_DIR = os.path.join(ROOT, "data")
CUSTOM_DIR = os.path.join(DATA_DIR, "custom")   # instances designed in the GUI
CUSTOM_PREFIX = "custom/"
QUBO_DIR = os.path.join(ROOT, "qubo")           # exports made from the GUI
QUBO_DIRS = {"": ROOT, "data": DATA_DIR, "qubo": QUBO_DIR}
QUBO_STAT_RE = re.compile(r"qubo_lb_objective=([-\d.]+)\s+ip_objective=([-\d.]+)")
SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_instance_cache = {}
_cache_lock = threading.Lock()


def instance_path(name):
    """Resolve an instance name to a path inside data/, data/custom/ or the
    project root. Only the basename is ever used, so a name cannot escape them."""
    safe = os.path.basename(name)
    bases = (CUSTOM_DIR,) if name.startswith(CUSTOM_PREFIX) else (DATA_DIR, ROOT)
    for base in bases:
        path = os.path.join(base, safe)
        if os.path.isfile(path):
            return path
    raise FileNotFoundError(f"instance not found: {name}")


def load_instance(name):
    path = instance_path(name)
    key = (path, os.path.getmtime(path))
    with _cache_lock:
        if key in _instance_cache:
            return _instance_cache[key]
    instance = read_instance(path, name=os.path.basename(path))
    with _cache_lock:
        _instance_cache[key] = instance
    return instance


def list_instances():
    names = sorted(f for f in os.listdir(DATA_DIR) if f.endswith((".dat", ".txt")))
    names += sorted(f for f in os.listdir(ROOT) if f.endswith(".dat"))
    if os.path.isdir(CUSTOM_DIR):
        names += [CUSTOM_PREFIX + f for f in sorted(os.listdir(CUSTOM_DIR))
                  if f.endswith(".dat")]
    entries = []
    for name in names:
        try:
            instance = load_instance(name)
        except Exception as exc:                        # unreadable file: still list it
            entries.append({"name": name, "error": str(exc)})
            continue
        entries.append({
            "name": name,
            "stacks": instance.number_of_stacks,
            "blocks": instance.number_of_blocks,
            "fileTiers": instance.file_number_of_tiers,
            "maxStackHeight": max((len(s) for s in instance.bay), default=0),
            "custom": name.startswith(CUSTOM_PREFIX),
        })
    return entries


def qubo_files():
    """.qubo files in qubo/ (the GUI's exports), the project root and data/,
    newest first, each with the provenance the GUI recorded for it."""
    found = []
    for prefix, base in (("qubo/", QUBO_DIR), ("", ROOT), ("data/", DATA_DIR)):
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            if name.endswith(".qubo"):
                path = os.path.join(base, name)
                meta = qubo_meta(path)
                found.append({"name": prefix + name,
                              "size": os.path.getsize(path),
                              "modified": os.path.getmtime(path),
                              "instance": meta.get("instance") if meta else None})
    found.sort(key=lambda f: -f["modified"])
    return found


def qubo_path(name):
    """'qubo/x.qubo', 'data/x.qubo' or 'x.qubo' (project root) -> a path. Only
    the basename is used inside the chosen directory, so a name cannot escape."""
    name = str(name or "problem.qubo")
    prefix, _, base = name.rpartition("/")
    safe = os.path.basename(base)
    bases = [QUBO_DIRS[prefix]] if prefix in QUBO_DIRS else []
    bases += [b for b in (ROOT, DATA_DIR, QUBO_DIR) if b not in bases]
    for base_dir in bases:
        path = os.path.join(base_dir, safe)
        if os.path.isfile(path):
            return path
    raise FileNotFoundError(f"QUBO file not found: {name}")


def qubo_meta(path):
    """The sidecar the GUI writes next to an export (<file>.qubo.json): which
    test case and height limit the QUBO came from, and the IP's objective."""
    try:
        with open(path + ".json") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def export_name(payload, instance):
    stem = SAFE_NAME_RE.sub("-", os.path.splitext(
        os.path.basename(payload.get("name") or instance.name))[0]).strip("-.") or "custom"
    options = Options(payload)
    if options.empty_tiers is not None and options.empty_tiers >= 0:
        stem += f"-E{options.empty_tiers}"
    if options.maximum_height:
        stem += f"-T{options.maximum_height}"
    return stem + ".qubo"


def write_qubo_meta(path, payload, instance, result):
    """Record where an export came from, so the quantum tab can replay its
    samples against the right bay and compare them with the IP optimum."""
    options = Options(payload)
    meta = {
        "instance": None if payload.get("bay") is not None else instance.name,
        "name": instance.name,
        "emptyTiers": options.empty_tiers,
        "maximumHeight": options.maximum_height,
        "heightLimit": result.get("heightLimit"),
        "ipObjective": result.get("objective"),
        "provenOptimal": (result.get("stats") or {}).get("optimal_value") is not None,
        "relocations": len(result.get("relocations") or []),
        "solveSeconds": (result.get("stats") or {}).get("total_time", result.get("wallTime")),
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if payload.get("bay") is not None:          # a design that may never be saved
        meta["bay"] = instance.bay
        meta["tiers"] = instance.file_number_of_tiers
    match = QUBO_STAT_RE.search(result.get("stderr") or "")
    if match:
        meta["quboLowerBound"] = float(match.group(1))
    with open(path + ".json", "w") as f:
        json.dump(meta, f, indent=1)
    return meta


def replay_context(payload, path=None):
    """(instance, height limit, where they came from) to replay QUBO samples
    against: the export's sidecar when there is one, else the sidebar."""
    meta = qubo_meta(path) if path else None
    if meta:
        try:
            if meta.get("bay") is not None:
                handle, scratch = tempfile.mkstemp(prefix="rbrp-qubo-", suffix=".dat")
                os.close(handle)
                try:
                    write_dat(scratch, meta["bay"], meta.get("tiers"))
                    instance = read_instance(scratch, name=meta.get("name") or "custom")
                finally:
                    os.unlink(scratch)
            else:
                instance = load_instance(meta["instance"])
            limit = meta.get("heightLimit") or effective_tiers(
                instance, meta.get("emptyTiers"), meta.get("maximumHeight"))
            return instance, limit, "export"
        except Exception:
            pass
    instance, limit = qubo_height_limit(payload)
    return instance, limit, "sidebar" if instance else None


def qubo_height_limit(payload):
    """The QUBO file does not record the height limit it was exported under, so
    the one from the parameter panel is used -- and said so in the GUI."""
    try:
        instance = load_instance(payload.get("instance", ""))
    except Exception:
        return None, None
    options = Options(payload)
    return instance, effective_tiers(instance, options.empty_tiers, options.maximum_height)


def clean_bay(payload):
    """Read a designed bay off a request: a list of stacks, bottom -> top."""
    bay = payload.get("bay")
    if not isinstance(bay, list):
        raise ValueError("no bay given")
    cleaned = []
    for stack in bay:
        if not isinstance(stack, list):
            raise ValueError("each stack must be a list of priorities")
        cleaned.append([int(b) for b in stack])
    tiers = payload.get("tiers")
    problems = validate_bay(cleaned, int(tiers) if tiers else None)
    if problems:
        raise ValueError("; ".join(problems))
    return cleaned, (int(tiers) if tiers else None)


def safe_dat_name(name):
    stem = SAFE_NAME_RE.sub("-", os.path.basename(str(name or "")).strip()).strip("-.")
    if stem.endswith(".dat"):
        stem = stem[:-4]
    return (stem or "custom") + ".dat"


class Handler(BaseHTTPRequestHandler):
    server_version = "rbrp-gui"

    def log_message(self, fmt, *args):                  # keep the console readable
        if self.path.startswith("/api/solve") or (
                self.path.startswith("/api/quantum/") and "/job" not in self.path):
            print(f"[gui] {fmt % args}")

    # ------------------------------------------------------------------ helpers
    def send_json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path):
        if not os.path.isfile(path):
            self.send_error(404, "not found")
            return
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # ---------------------------------------------------------------------- GET
    def do_GET(self):
        url = urlparse(self.path)
        route = url.path
        query = parse_qs(url.query)

        if route == "/api/instances":
            try:
                self.send_json({"instances": list_instances(),
                                "solverAvailable": os.path.exists(BINARY),
                                "dataDir": DATA_DIR})
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=500)
            return

        if route == "/api/qubo-files":
            self.send_json({"files": qubo_files()})
            return

        if route == "/api/qubo":
            try:
                name = (query.get("path") or [""])[0]
                path = qubo_path(name)
                variables, terms = qubo_model.load(path)
                summary = qubo_model.summarize(path, variables, terms)
                summary["path"] = name or summary["path"]
                summary["meta"] = qubo_meta(path)
                self.send_json(summary)
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
            return

        if route.startswith("/api/quantum/"):
            self.quantum_get(route[len("/api/quantum/"):], query)
            return

        if route == "/api/instance":
            name = (query.get("name") or [""])[0]
            try:
                self.send_json(load_instance(name).as_dict())
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
            return

        if route in ("/", "/index.html"):
            self.send_file(os.path.join(STATIC_DIR, "index.html"))
            return

        rel = posixpath.normpath(route).lstrip("/")
        if rel.startswith("static/"):
            rel = rel[len("static/"):]
        target = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if not target.startswith(STATIC_DIR):
            self.send_error(403, "forbidden")
            return
        self.send_file(target)

    # --------------------------------------------------------------------- POST
    def do_POST(self):
        route = urlparse(self.path).path
        if route not in ("/api/solve", "/api/save-instance", "/api/stop",
                         "/api/qubo-eval") and not route.startswith("/api/quantum/"):
            self.send_error(404, "not found")
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as exc:
            self.send_json({"error": f"bad request: {exc}"}, status=400)
            return

        if route == "/api/stop":
            count = stop_runs(payload.get("runId"))
            print(f"[gui] stop requested: {count} run(s) killed")
            self.send_json({"stopped": count})
            return

        if route == "/api/qubo-eval":
            self.qubo_eval(payload)
            return

        if route.startswith("/api/quantum/"):
            self.quantum_post(route[len("/api/quantum/"):], payload)
            return

        if route == "/api/save-instance":
            self.save_instance(payload)
            return

        scratch = None
        try:
            if payload.get("bay") is not None:
                bay, tiers = clean_bay(payload)
                handle, scratch = tempfile.mkstemp(prefix="rbrp-custom-", suffix=".dat")
                os.close(handle)
                write_dat(scratch, bay, tiers)
                instance = read_instance(scratch, name=payload.get("name") or "custom instance")
            else:
                instance = load_instance(payload.get("instance", ""))
        except Exception as exc:
            if scratch and os.path.exists(scratch):
                os.unlink(scratch)
            self.send_json({"error": str(exc)}, status=400)
            return

        options = Options(payload)
        if options.export_qubo:
            # GUI exports go to qubo/<test case>.qubo -- never over problem.qubo,
            # which is tracked -- with a sidecar saying where they came from
            os.makedirs(QUBO_DIR, exist_ok=True)
            options.export_qubo = os.path.join(QUBO_DIR, export_name(payload, instance))

        print(f"[gui] solving {instance.name} ...")
        started = time.time() - 1
        try:
            result = solve(instance, options, run_id=payload.get("runId"))
        except Exception as exc:
            self.send_json({"error": f"solver failed: {exc}"}, status=500)
            return
        finally:
            if scratch and os.path.exists(scratch):
                os.unlink(scratch)
        print(f"[gui] {instance.name}: objective={result.get('objective')} "
              f"({result.get('wallTime', 0):.2f}s)")
        if options.export_qubo and os.path.isfile(options.export_qubo) \
                and os.path.getmtime(options.export_qubo) >= started:
            try:
                meta = write_qubo_meta(options.export_qubo, payload, instance, result)
                result["quboExport"] = {"name": "qubo/" + os.path.basename(options.export_qubo),
                                        "meta": meta}
            except OSError as exc:
                result["quboExport"] = {"error": str(exc)}
        self.send_json(result)

    def qubo_eval(self, payload):
        """Score a selection of QUBO columns, and replay it into a move plan."""
        try:
            path = qubo_path(payload.get("path"))
            variables, terms = qubo_model.load(path)
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=400)
            return

        instance, height_limit, source = replay_context(payload, path)
        matches, blocking = (qubo_model.instance_matches(instance, variables)
                             if instance else (None, None))

        result = {"instanceMatches": matches, "heightLimit": height_limit,
                  "instance": instance.name if instance else None,
                  "contextSource": source}

        if payload.get("search"):
            if instance is None:
                self.send_json({"error": "select a test case first: a plan can only "
                                         "be replayed against an instance"}, status=400)
                return
            found = qubo_model.search_selections(instance, variables, terms,
                                                 max_height=height_limit)
            result["search"] = found
            selection = set(found.get("best", {}).get("selection", [])) if found.get("best") else set()
        elif payload.get("cheapest"):
            selection = qubo_model.cheapest_selection(variables)
        elif payload.get("bitstring"):
            try:
                selection = qubo_model.selection_from_bitstring(
                    payload["bitstring"], variables, bool(payload.get("msbFirst")))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
        else:
            selection, unknown = qubo_model.resolve_selection(
                payload.get("selection", ""), variables)
            if unknown:
                result["unknown"] = unknown

        result["selection"] = sorted(selection)
        result["names"] = sorted(variables[i].name for i in selection if i in variables)
        result["violations"] = qubo_model.check_groups(variables, selection)
        result["energy"] = qubo_model.energy(terms, selection)
        relaxed_selection, relaxed = qubo_model.relax_slacks(terms, variables, selection)
        result["energyRelaxed"] = relaxed
        result["offset"] = qubo_model.read_offset(path)
        result["objective"] = relaxed + result["offset"]
        result["slacksOn"] = len(relaxed_selection - selection)
        result["cost"] = sum(variables[i].cost or 0 for i in selection
                             if i in variables and variables[i].kind == "sequence")

        if instance is not None and not result["violations"]:
            plan, error = qubo_model.move_plan(instance, variables, selection,
                                               height_limit,
                                               complete=bool(payload.get("complete")))
            result["planError"] = error
            if plan:
                result["plan"] = qubo_model.plan_payload(instance, plan, height_limit,
                                                         energy=relaxed,
                                                         label=payload.get("label"))
        elif instance is None:
            result["planError"] = "no test case selected to replay the plan against"

        self.send_json(result)

    # ------------------------------------------------------------- quantum
    def quantum_ready(self):
        if quantum is None:
            self.send_json({"error": "the quantum tools are not importable here ("
                                     f"{QUANTUM_ERROR}); start the GUI with the project's "
                                     ".venv/bin/python"}, status=503)
            return False
        return True

    def send_quantum(self, payload, status=200):
        body = quantum.to_json(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, body, ctype, filename=None):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def quantum_problem(self, payload):
        path = qubo_path(payload.get("path"))
        problem = quantum.problem_for(path)
        instance, limit, source = replay_context(payload, path)
        return problem, (instance, limit), source

    def quantum_get(self, action, query):
        if action == "status":
            if quantum is None:
                self.send_json({"available": False, "error": QUANTUM_ERROR,
                                "python": sys.executable})
            else:
                self.send_quantum({"available": True, **quantum.status(),
                                   "devices": quantum.devices(),
                                   "topologies": quantum.TOPOLOGIES,
                                   "annealMethods": quantum.ANNEAL_METHODS})
            return
        if not self.quantum_ready():
            return
        one = lambda key, default="": (query.get(key) or [default])[0]
        try:
            if action == "job":
                job = quantum.get_job(one("id"))
                if job is None:
                    self.send_json({"error": "unknown or expired job"}, status=404)
                    return
                self.send_quantum(job.view(int(one("since", "0") or 0)))
            elif action == "diagram":
                problem = quantum.problem_for(qubo_path(one("path")))
                svg = quantum.call(quantum.draw, problem, int(one("reps", "1")),
                                   one("kind", "logical"), one("device") or None,
                                   one("fold") or None)
                self.send_bytes(svg, "image/svg+xml")
            elif action == "export":
                problem = quantum.problem_for(qubo_path(one("path")))
                params = [float(v) for v in one("params").split(",") if v.strip()] or None
                filename, ctype, body = quantum.call(quantum.export, problem, one("what"),
                                                     int(one("reps", "1")), params,
                                                     one("embedding") or None)
                self.send_bytes(body, ctype, filename)
            elif action == "ibm-jobs":
                self.send_quantum({"jobs": quantum.ibm_job_log()})
            else:
                self.send_json({"error": f"unknown action {action}"}, status=404)
        except Exception as exc:
            self.send_json({"error": f"{type(exc).__name__}: {exc}"}, status=400)

    JOB_ACTIONS = {"anneal", "qaoa-run", "qaoa-estimate", "qaoa-landscape", "ibm-submit"}

    def quantum_post(self, action, payload):
        if not self.quantum_ready():
            return
        try:
            if action == "cancel":
                self.send_json({"cancelled": quantum.cancel_job(payload.get("id"))})
                return
            if action == "qpu-timing":
                self.send_quantum(quantum.qpu_timing(payload.get("topology") or "pegasus",
                                                     int(payload.get("qubits") or 1),
                                                     int(payload.get("reads") or 1000),
                                                     payload.get("annealTime")))
                return
            if action == "ibm-backends":
                self.send_quantum(quantum.call(quantum.ibm_backends, payload.get("creds")))
                return
            problem, ctx, source = self.quantum_problem(payload)
            if action == "embed":
                self.send_quantum(quantum.call(quantum.embed, problem,
                                               payload.get("topology") or "pegasus",
                                               payload.get("seed") or 1,
                                               payload.get("tries") or 10,
                                               payload.get("timeout") or 30))
            elif action == "qaoa-build":
                self.send_quantum(quantum.call(quantum.qaoa_build, problem,
                                               payload.get("reps") or 1))
            elif action == "ibm-check":
                self.send_quantum(quantum.call(quantum.ibm_check, problem,
                                               payload.get("creds"), payload))
            elif action == "ibm-job":
                self.send_quantum(quantum.call(quantum.ibm_job, problem, ctx,
                                               payload.get("creds"),
                                               str(payload.get("jobId") or "").strip(),
                                               bool(payload.get("oldSign"))))
            elif action in self.JOB_ACTIONS:
                if action == "anneal":
                    job = quantum.start_job(action, lambda job: quantum.anneal(problem, ctx, payload))
                elif action == "qaoa-run":
                    job = quantum.start_job(action, quantum.qaoa_run, problem, ctx, payload)
                elif action == "qaoa-estimate":
                    job = quantum.start_job(action, quantum.qaoa_estimate, problem, payload)
                elif action == "qaoa-landscape":
                    job = quantum.start_job(action, quantum.qaoa_landscape, problem, payload)
                else:
                    job = quantum.start_job(action, lambda job: quantum.ibm_submit(
                        problem, payload.get("creds"), payload))
                print(f"[gui] quantum {action} on {problem.name} (job {job.id})")
                self.send_quantum({"job": job.id, "contextSource": source,
                                   "instance": ctx[0].name if ctx[0] else None,
                                   "heightLimit": ctx[1]})
            else:
                self.send_json({"error": f"unknown action {action}"}, status=404)
        except Exception as exc:
            self.send_json({"error": f"{type(exc).__name__}: {exc}"}, status=400)

    def save_instance(self, payload):
        """Store a designed bay as data/custom/<name>.dat so it can be reloaded."""
        try:
            bay, tiers = clean_bay(payload)
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=400)
            return
        name = safe_dat_name(payload.get("name"))
        os.makedirs(CUSTOM_DIR, exist_ok=True)
        path = os.path.join(CUSTOM_DIR, name)
        existed = os.path.exists(path)
        try:
            write_dat(path, bay, tiers)
        except OSError as exc:
            self.send_json({"error": f"could not save: {exc}"}, status=500)
            return
        print(f"[gui] saved {path}")
        self.send_json({"name": CUSTOM_PREFIX + name, "path": path,
                        "overwritten": existed})


def is_wsl():
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        with open("/proc/version") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def open_browser(url):
    """Open the GUI in a browser. Under WSL there is usually no xdg-open and
    webbrowser falls back to gio, which cannot handle http:// -- hand the URL to
    Windows instead."""
    candidates = []
    if is_wsl():
        for command in (["wslview", url],
                        ["explorer.exe", url],
                        ["powershell.exe", "-NoProfile", "-Command", "Start-Process", url],
                        ["cmd.exe", "/c", "start", "", url]):
            if shutil.which(command[0]):
                candidates.append(command)

    for command in candidates:
        try:
            # explorer.exe reports failure even when it worked, so trust no exit code
            subprocess.run(command, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=15, check=False)
            return True
        except (OSError, subprocess.SubprocessError):
            continue

    try:
        if webbrowser.open(url):
            return True
    except Exception:
        pass
    print(f"could not open a browser automatically -- point one at {url}")
    return False


def serve_on(host, port, attempts=20):
    """Bind the first free port at or after `port`."""
    last = None
    for candidate in range(port, port + attempts):
        try:
            return ThreadingHTTPServer((host, candidate), Handler), candidate
        except OSError as exc:
            last = exc
    raise SystemExit(f"no free port in {port}..{port + attempts - 1}: {last}")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    # 8000/8080 are popular; under WSL a Windows program holding the port silently
    # wins the localhost forward, so default to something quieter.
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="open a browser window")
    args = parser.parse_args()

    server, port = serve_on(args.host, args.port)
    if port != args.port:
        print(f"port {args.port} was busy, using {port} instead", flush=True)
    url = f"http://{args.host}:{port}/"
    print(f"RBRP solver GUI on {url}  (Ctrl-C to stop)", flush=True)
    if not os.path.exists(BINARY):
        print(f"warning: {BINARY} is missing -- run 'make' before solving", flush=True)
    if args.open:
        threading.Timer(0.5, open_browser, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
