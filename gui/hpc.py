"""HPC side of the GUI: run classical solves and classical simulations of the
quantum algorithms as Slurm batch jobs on FRIDA (UL FRI, https://docs.rdc.si/),
or on any Slurm cluster reached over SSH.

How it works
  * Login is the user's: FRIDA needs Teleport (`tsh login` with MFA), after which
    plain `ssh login-frida` works through the ProxyCommand that `tsh config`
    writes. This module never sees a password or a key; it only runs `ssh`
    with BatchMode, so an expired session fails fast with a clear hint.
  * Remote layout under one base directory (default ~/brpq):
        repo/                      the project, synced from here (tar over ssh)
        images/brpq-{cpu,gpu}.sqfs Enroot images built by hpc/build_image.sbatch
        jobs/<name>/               spec.json, step.sh, job.sbatch, inputs/,
                                   logs/, results/
  * One job = one Slurm array; every task is one instance (classical solve or
    full pipeline) or one quantum configuration on one QUBO, so a benchmark
    sweep runs in parallel. Each task runs hpc/brpq_job.py inside the image
    (srun --container-image, Pyxis), and writes results/<task>.json.
  * Results are fetched back into quantum_runs/hpc/<name>/ and summarised.

Transport "local" runs the same job directory on this machine (sbatch if this
machine has Slurm, else the tasks one after another in the background, without
a container) -- handy on the login node itself and for testing.
"""
import glob
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import threading
import time

from .runner import ROOT

HPC_DIR = os.path.join(ROOT, "quantum_runs", "hpc")
CONFIG_FILE = os.path.join(HPC_DIR, "config.json")
JOBS_FILE = os.path.join(HPC_DIR, "jobs.json")
NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_lock = threading.Lock()

DEFAULTS = {
    "transport": "ssh",          # ssh | local
    "host": "login-frida",       # an ssh alias (FRIDA: from `tsh config`)
    "remoteBase": "~/brpq",
    "account": "",
    "gurobiLicense": "~/gurobi.lic",
    "sshOptions": "",
    "resolvedBase": None,        # absolute remote base, filled in by check()
}

# FRIDA partitions and GPU types (docs.rdc.si/FRIDA/slurm/, 2026). Memory in GB.
PARTITIONS = [
    {"name": "frida", "maxTime": "7-00:00:00", "defaultTime": "04:00:00",
     "gpus": ["L4", "A100", "A100_80GB", "H100", "B200", "B300"],
     "note": "general production partition (all x86 GPU nodes)"},
    {"name": "amd", "maxTime": "2-00:00:00", "defaultTime": "02:00:00",
     "gpus": ["MI210"],
     "note": "CPU-heavy work: 2x EPYC 9684X, 368 vCPU, 755 GiB (experimental)"},
    {"name": "dev", "maxTime": "12:00:00", "defaultTime": "02:00:00",
     "gpus": ["L4", "A100", "A100_80GB"],
     "note": "development and testing only; not for production runs"},
]
GPU_MEMORY_GB = {"L4": 24, "A100": 40, "A100_80GB": 80, "H100": 80,
                 "B200": 180, "B300": 288, "MI210": 64}
EXCLUDE_DIRS = {".git", ".venv", "venv", "quantum_runs", "__pycache__", ".claude",
                "node_modules", ".mypy_cache", ".pytest_cache"}
EXCLUDE_FILES = {"rbrp_ip", "qubo.png"}
EXCLUDE_SUFFIXES = (".o", ".pyc", "~", ".bak", ".sqfs")


class HpcError(Exception):
    pass


# ---------------------------------------------------------------- config
def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_FILE) as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(update):
    old = load_config()
    cfg = dict(old)
    for key in DEFAULTS:
        if key in update and key != "resolvedBase":
            cfg[key] = str(update[key] or "").strip() if key != "transport" else update[key]
    cfg["host"] = cfg["host"] or DEFAULTS["host"]
    cfg["remoteBase"] = cfg["remoteBase"] or DEFAULTS["remoteBase"]
    if any(cfg[k] != old.get(k) for k in ("host", "remoteBase", "transport")):
        cfg["resolvedBase"] = None
    if cfg["transport"] not in ("ssh", "local"):
        raise HpcError("transport must be ssh or local")
    os.makedirs(HPC_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=1)
    return cfg


def layout(cfg):
    base = cfg.get("resolvedBase")
    if not base:
        raise HpcError("connect first (Test connection) so the remote home is known")
    repo = ROOT if cfg.get("transport") == "local" else f"{base}/repo"
    return {"base": base, "repo": repo, "images": f"{base}/images",
            "jobs": f"{base}/jobs",
            "imageCpu": f"{base}/images/brpq-cpu.sqfs",
            "imageGpu": f"{base}/images/brpq-gpu.sqfs"}


# ------------------------------------------------------------- transport
SSH_HINT = ("ssh could not log in. For FRIDA: run `tsh login --proxy=rdc.si "
            "--user=<you>` (MFA) in a terminal on this computer, make sure `tsh config` "
            "output is in ~/.ssh/config, and check that `ssh {host} hostname` works "
            "without a prompt.")


def run(cfg, script, data=None, timeout=180):
    """Run a bash script on the cluster (or here, for transport=local);
    `data` is fed to stdin. Returns stdout as text; raises HpcError."""
    if cfg["transport"] == "local":
        argv = ["bash", "-c", script]
    else:
        argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20"]
        argv += shlex.split(cfg.get("sshOptions") or "")
        argv += [cfg["host"], "bash -c " + shlex.quote(script)]
    try:
        proc = subprocess.run(argv, input=data, capture_output=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise HpcError(f"{argv[0]} is not installed on this computer") from exc
    except subprocess.TimeoutExpired as exc:
        raise HpcError(f"no answer from {cfg['host']} within {timeout}s") from exc
    out = proc.stdout.decode(errors="replace")
    err = proc.stderr.decode(errors="replace").strip()
    if proc.returncode == 255 and cfg["transport"] == "ssh":
        raise HpcError(SSH_HINT.format(host=cfg["host"]) + (f"\n\nssh said: {err}" if err else ""))
    if proc.returncode != 0:
        raise HpcError(f"remote command failed (exit {proc.returncode}): "
                       f"{(err or out).strip()[-1500:]}")
    return out


def run_bytes(cfg, script, timeout=600):
    """Like run(), but returns raw stdout (a tar stream)."""
    if cfg["transport"] == "local":
        argv = ["bash", "-c", script]
    else:
        argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20"]
        argv += shlex.split(cfg.get("sshOptions") or "")
        argv += [cfg["host"], "bash -c " + shlex.quote(script)]
    proc = subprocess.run(argv, capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        err = proc.stderr.decode(errors="replace").strip()
        if proc.returncode == 255 and cfg["transport"] == "ssh":
            raise HpcError(SSH_HINT.format(host=cfg["host"]) + f"\n\nssh said: {err}")
        raise HpcError(f"remote command failed (exit {proc.returncode}): {err[-1500:]}")
    return proc.stdout


def q(value):
    return shlex.quote(str(value))


def expand_base(base, home):
    base = (base or "~/brpq").strip()
    if base == "~":
        return home
    if base.startswith("~/"):
        return home.rstrip("/") + base[1:]
    if not base.startswith("/"):
        return home.rstrip("/") + "/" + base
    return base.rstrip("/")


# ------------------------------------------------------------- actions
def check(update=None):
    """Log in, resolve the remote base, report what is there."""
    cfg = save_config(update) if update else load_config()
    script = r"""
echo "home=$HOME"; echo "host=$(hostname)"; echo "user=$(whoami)"
if command -v sbatch >/dev/null 2>&1; then echo slurm=yes; else echo slurm=no; fi
if command -v python3 >/dev/null 2>&1; then echo "python=$(python3 -V 2>&1)"; fi
echo "date=$(date '+%Y-%m-%d %H:%M:%S')"
"""
    out = run(cfg, script, timeout=60)
    facts = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    home = facts.get("home") or ""
    cfg["resolvedBase"] = expand_base(cfg["remoteBase"], home) if home else None
    cfg["remoteHome"] = home or None
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=1)
    paths = layout(cfg)
    probe = f"""
B={q(paths['base'])}
[ -f "$B/repo/hpc/brpq_job.py" ] && echo repo=yes || echo repo=no
[ -x "$B/repo/rbrp_ip" ] && echo binary=yes || echo binary=no
for v in cpu gpu; do f="$B/images/brpq-$v.sqfs"
  if [ -f "$f" ]; then echo "image_$v=$(du -h "$f" | cut -f1) $(date -r "$f" '+%Y-%m-%d')"
  else echo "image_$v=missing"; fi; done
lic={q(cfg.get('gurobiLicense') or '')}; lic="${{lic/#\\~/$HOME}}"
if [ -n "$lic" ] && [ -f "$lic" ]; then echo "gurobi_license=$lic"; else echo "gurobi_license=missing"; fi
if command -v frida >/dev/null 2>&1; then echo "frida=yes"; fi
if command -v sinfo >/dev/null 2>&1; then
  sinfo -h -o 'part=%P|%a|%l|%D|%G' 2>/dev/null | head -12; fi
"""
    out = run(cfg, probe, timeout=60)
    for line in out.splitlines():
        if line.startswith("part="):
            facts.setdefault("partitions", []).append(line[5:])
        elif "=" in line:
            k, v = line.split("=", 1)
            facts[k] = v
    facts["base"] = paths["base"]
    facts["transport"] = cfg["transport"]
    return {"config": public_config(cfg), "facts": facts}


def public_config(cfg=None):
    cfg = cfg or load_config()
    return {k: cfg.get(k) for k in DEFAULTS}


def repo_tarball():
    """The project as a gzipped tar, without builds, venvs and run outputs."""
    buf = io.BytesIO()
    count = 0
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for dirpath, dirnames, filenames in os.walk(ROOT):
            rel_dir = os.path.relpath(dirpath, ROOT)
            dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS
                                 and not (rel_dir == "." and d == "qubo_runs"))
            for name in sorted(filenames):
                if name in EXCLUDE_FILES or name.endswith(EXCLUDE_SUFFIXES):
                    continue
                path = os.path.join(dirpath, name)
                arc = os.path.normpath(os.path.join(rel_dir, name))
                tar.add(path, arcname=arc, recursive=False)
                count += 1
    return buf.getvalue(), count


def sync():
    cfg = load_config()
    paths = layout(cfg)
    if cfg["transport"] == "local":
        return {"files": 0, "bytes": 0, "repo": paths["repo"],
                "note": "transport local uses this project directly; nothing to copy"}
    data, count = repo_tarball()
    script = f"mkdir -p {q(paths['repo'])} && tar -xzf - -C {q(paths['repo'])} && echo ok"
    run(cfg, script, data=data, timeout=600)
    return {"files": count, "bytes": len(data), "repo": paths["repo"]}


def build_image(variant="cpu", gurobi_version=None):
    cfg = load_config()
    paths = layout(cfg)
    if variant not in ("cpu", "gpu"):
        raise HpcError("variant must be cpu or gpu")
    if cfg["transport"] == "local":
        raise HpcError("container images are built on the cluster (transport ssh)")
    account = f"--account={q(cfg['account'])} " if cfg.get("account") else ""
    env = f"GUROBI_VERSION={q(gurobi_version)} " if gurobi_version else ""
    script = (f"cd {q(paths['repo'])} && mkdir -p {q(paths['images'])} && "
              f"{env}BRPQ_IMAGES={q(paths['images'])} sbatch --parsable {account}"
              f"--output={q(paths['images'] + '/build-' + variant + '-%j.out')} "
              f"hpc/build_image.sbatch {variant}")
    out = run(cfg, script, timeout=120).strip()
    slurm_id = out.split(";")[0].strip()
    record = {"name": f"image-{variant}-{time.strftime('%Y%m%d-%H%M%S')}", "kind": "image",
              "variant": variant, "slurmId": slurm_id, "tasks": 1,
              "submitted": time.strftime("%Y-%m-%d %H:%M:%S"),
              "log": f"{paths['images']}/build-{variant}-{slurm_id}.out",
              "remoteDir": paths["images"], "transport": cfg["transport"]}
    remember(record)
    return record


# ----------------------------------------------------------- job specs
def slug(text):
    return NAME_RE.sub("-", str(text or "job")).strip("-.")[:40] or "job"


def _clean_options(options):
    keep = ("emptyTiers", "maximumHeight", "timeLimit", "threads", "threshold",
            "disableGreedy", "disableUpperBound", "verbose")
    return {k: options.get(k) for k in keep if options.get(k) not in (None, "", False)}


def build_spec(payload):
    """GUI request -> (spec dict, files to upload {relative path: local path})."""
    mode = payload.get("mode") or "classical"
    options = _clean_options(payload.get("options") or {})
    anneal = [dict(a) for a in payload.get("anneal") or []]
    qaoa = [dict(c) for c in payload.get("qaoa") or []]
    tasks, files = [], {}
    if mode in ("classical", "pipeline"):
        instances = [i for i in payload.get("instances") or [] if i]
        if not instances:
            raise HpcError("pick at least one test case")
        for name in instances:
            task = {"kind": mode, "instance": name, "options": options,
                    "exportQubo": bool(payload.get("exportQubo", True)) or mode == "pipeline"}
            if mode == "pipeline":
                if not anneal and not qaoa:
                    raise HpcError("a pipeline needs at least one annealing method or QAOA setting")
                task["anneal"], task["qaoa"] = anneal, qaoa
            tasks.append(task)
    elif mode == "quantum":
        qubo_name = payload.get("qubo")
        if not qubo_name:
            raise HpcError("load a QUBO in the Quantum tab first")
        from .server import qubo_path                     # late: avoids a cycle
        local = qubo_path(qubo_name)
        rel = "inputs/" + os.path.basename(local)
        files[rel] = local
        if os.path.isfile(local + ".json"):
            files[rel + ".json"] = local + ".json"
        context = {"instance": payload.get("instance") or "", "options": options}
        for a in anneal:
            tasks.append({"kind": "anneal", "qubo": rel, "anneal": a, **context})
        for c in qaoa:
            tasks.append({"kind": "qaoa", "qubo": rel, "qaoa": c, **context})
        if not tasks:
            raise HpcError("choose at least one annealing method or QAOA setting")
    else:
        raise HpcError(f"unknown mode {mode!r}")
    for k, task in enumerate(tasks):
        task["id"] = f"{k:03d}"
    name = f"{slug(payload.get('name') or mode)}-{time.strftime('%Y%m%d-%H%M%S')}"
    spec = {"name": name, "mode": mode, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "tasks": tasks}
    return spec, files


def _time(value, default="04:00:00"):
    value = str(value or "").strip()
    return value if re.fullmatch(r"(\d+-)?\d{1,2}(:\d{2}){1,2}", value) else default


def resources_of(payload):
    r = dict(payload.get("resources") or {})
    gpu = r.get("gpu") or ""
    return {
        "partition": r.get("partition") or "frida",
        "time": _time(r.get("time")),
        "cpus": max(1, min(int(r.get("cpus") or 8), 384)),
        "mem": max(1, min(int(r.get("mem") or 32), 2000)),
        "gpu": gpu if gpu in GPU_MEMORY_GB or gpu == "any" else "",
        "maxParallel": max(0, int(r.get("maxParallel") or 0)),
        "backend": (r.get("backend") or ("cupy" if gpu else "aer")).lower(),
        "exactLimit": max(8, min(int(r.get("exactLimit") or 30), 36)),
    }


def licence_path(cfg):
    """The Gurobi licence as an absolute path on the cluster: '~' must be
    expanded outside the container, whose HOME is not the user's."""
    lic = (cfg.get("gurobiLicense") or "").strip()
    if lic and cfg.get("remoteHome"):
        lic = expand_base(lic, cfg["remoteHome"])
    return lic


def step_script(cfg, spec, res, paths, needs_solver):
    lic = licence_path(cfg)
    python = "/opt/venv/bin/python" if cfg["transport"] == "ssh" else sys.executable
    lines = [
        "#!/bin/bash",
        f"# brpq task runner for job {spec['name']} -- executed once per array task",
        "set -euo pipefail",
        "[ -f /opt/brpq-image.env ] && . /opt/brpq-image.env",
        f"REPO={q(paths['repo'])}",
        f"JOBDIR={q(paths['jobdir'])}",
        'TASK="${SLURM_ARRAY_TASK_ID:-${BRPQ_TASK:-0}}"',
        'export OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-${OMP_NUM_THREADS:-1}}"',
        f"export BRPQ_EXACT_LIMIT={res['exactLimit']}",
        f"export BRPQ_QAOA_BACKEND={q(res['backend'])}",
        "export MPLBACKEND=Agg PYTHONUNBUFFERED=1",
        f'PY={q(python)}; [ -x "$PY" ] || PY=python3',
    ]
    if lic:
        lines.append(f'LIC={q(lic)}; [ -f "$LIC" ] && export GRB_LICENSE_FILE="$LIC"')
    lines.append('cd "$REPO"')
    if needs_solver:
        lines += [
            "# build the solver once per repo version; concurrent tasks wait on the lock",
            'flock "$REPO/.build.lock" make -s rbrp_ip '
            '${GUROBI_LIBS:+"GUROBI_LIBS=$GUROBI_LIBS"} >"$JOBDIR/logs/build-$TASK.log" 2>&1 '
            '|| { cat "$JOBDIR/logs/build-$TASK.log"; exit 1; }',
        ]
    lines.append('exec "$PY" hpc/brpq_job.py run "$JOBDIR/spec.json" --task "$TASK"')
    return "\n".join(lines) + "\n"


def sbatch_script(cfg, spec, res, paths):
    n = len(spec["tasks"])
    array = f"0-{n - 1}" + (f"%{res['maxParallel']}" if res["maxParallel"] else "")
    head = [
        "#!/bin/bash",
        f"#SBATCH --job-name=brpq-{spec['name'][:40]}",
        f"#SBATCH --partition={res['partition']}",
        f"#SBATCH --time={res['time']}",
        f"#SBATCH --cpus-per-task={res['cpus']}",
        f"#SBATCH --mem={res['mem']}G",
        f"#SBATCH --array={array}",
        f"#SBATCH --output={paths['jobdir']}/logs/%A_%a.out",
    ]
    if cfg.get("account"):
        head.insert(3, f"#SBATCH --account={cfg['account']}")
    if res["gpu"]:
        head.append("#SBATCH --gres=gpu:1" if res["gpu"] == "any"
                    else f"#SBATCH --gres=gpu:{res['gpu']}:1")
    image = paths["imageGpu"] if res["backend"] == "cupy" else paths["imageCpu"]
    lic = licence_path(cfg)
    mounts = '"$BASE:$BASE"'
    body = [
        "",
        f"# Generated by the brpq GUI (HPC tab) on {time.strftime('%Y-%m-%d %H:%M:%S')}.",
        f"# {n} array task(s): {spec['mode']} -- see spec.json. Resubmit with: sbatch job.sbatch",
        "set -euo pipefail",
        f"BASE={q(paths['base'])}",
        f"JOBDIR={q(paths['jobdir'])}",
        f"IMAGE={q(image)}",
        'mkdir -p "$JOBDIR/logs" "$JOBDIR/results"',
        'echo "task ${SLURM_ARRAY_TASK_ID:-0} on $(hostname), ${SLURM_CPUS_PER_TASK:-?} CPUs"',
    ]
    if lic:
        body.append(f'LIC={q(lic)}')
        mounts = '"$BASE:$BASE${LIC:+,$LIC:$LIC:ro}"'
        body.append('[ -f "$LIC" ] || LIC=""')
    if cfg.get("transport") == "local":            # a Slurm cluster without Pyxis
        body.append('bash "$JOBDIR/step.sh"')
    else:
        body += [
            "srun --container-image=\"$IMAGE\" \\",
            f"     --container-mounts={mounts} \\",
            "     --container-workdir=\"$BASE/repo\" \\",
            '     bash "$JOBDIR/step.sh"',
        ]
    return "\n".join(head + body) + "\n"


def local_runner_script(spec, paths):
    """No Slurm here: run the array tasks one after another."""
    n = len(spec["tasks"])
    return "\n".join([
        "#!/bin/bash",
        f"# run all {n} task(s) of {spec['name']} here, one after another",
        f"JOBDIR={q(paths['jobdir'])}",
        'mkdir -p "$JOBDIR/logs" "$JOBDIR/results"',
        f"for i in $(seq 0 {n - 1}); do",
        '  SLURM_ARRAY_TASK_ID=$i bash "$JOBDIR/step.sh" > "$JOBDIR/logs/local_$i.out" 2>&1',
        "done",
        'echo done > "$JOBDIR/logs/local.done"',
    ]) + "\n"


def prepare(payload):
    cfg = load_config()
    spec, files = build_spec(payload)
    res = resources_of(payload)
    paths = dict(layout(cfg))
    paths["jobdir"] = f"{paths['jobs']}/{spec['name']}"
    needs_solver = spec["mode"] in ("classical", "pipeline")
    spec["resources"] = res
    step = step_script(cfg, spec, res, paths, needs_solver)
    batch = sbatch_script(cfg, spec, res, paths)
    notes = []
    if res["backend"] == "cupy" and not res["gpu"]:
        notes.append("the CuPy backend needs a GPU: choose a GPU type")
    if res["gpu"] and res["backend"] != "cupy" and spec["mode"] != "classical":
        notes.append("a GPU is requested but the QAOA backend is not cupy, so it would idle")
    if res["gpu"] in GPU_MEMORY_GB and res["backend"] == "cupy":
        import math
        qubits = int(math.log2(GPU_MEMORY_GB[res["gpu"]] * 1e9 / 48))
        notes.append(f"{res['gpu']}: about {qubits} qubits fit in GPU memory")
    if res["partition"] == "dev":
        notes.append("dev is for testing; FRIDA does not allow production runs there")
    return {"spec": spec, "files": sorted(files), "step": step, "sbatch": batch,
            "local": local_runner_script(spec, paths) if cfg["transport"] == "local" else None,
            "jobdir": paths["jobdir"], "notes": notes, "_files": files,
            "_cfg": cfg, "_paths": paths}


def job_tarball(prep):
    buf = io.BytesIO()

    def add_text(arc, text, mode=0o644):
        data = text.encode()
        info = tarfile.TarInfo(arc)
        info.size, info.mode, info.mtime = len(data), mode, int(time.time())
        tar.addfile(info, io.BytesIO(data))

    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        add_text("spec.json", json.dumps(prep["spec"], indent=1))
        add_text("step.sh", prep["step"], 0o755)
        add_text("job.sbatch", prep["sbatch"], 0o755)
        if prep.get("local"):
            add_text("run_local.sh", prep["local"], 0o755)
        for rel, local in prep["_files"].items():
            tar.add(local, arcname=rel, recursive=False)
    return buf.getvalue()


def submit(payload):
    prep = prepare(payload)
    cfg, paths = prep["_cfg"], prep["_paths"]
    jobdir = prep["jobdir"]
    image = paths["imageGpu"] if prep["spec"]["resources"]["backend"] == "cupy" else paths["imageCpu"]
    data = job_tarball(prep)
    run(cfg, f"mkdir -p {q(jobdir)}/logs {q(jobdir)}/results && tar -xzf - -C {q(jobdir)}",
        data=data, timeout=300)
    record = {"name": prep["spec"]["name"], "kind": "job", "mode": prep["spec"]["mode"],
              "tasks": len(prep["spec"]["tasks"]), "remoteDir": jobdir,
              "partition": prep["spec"]["resources"]["partition"],
              "resources": prep["spec"]["resources"], "transport": cfg["transport"],
              "submitted": time.strftime("%Y-%m-%d %H:%M:%S"),
              "summary": describe_tasks(prep["spec"])}
    if cfg["transport"] == "ssh":
        out = run(cfg, f"test -f {q(image)} || {{ echo 'image missing: {image} -- build it "
                       f"first' >&2; exit 3; }}; cd {q(jobdir)} && sbatch --parsable job.sbatch",
                  timeout=120)
        record["slurmId"] = out.strip().split(";")[0]
    else:
        has_slurm = run(cfg, "command -v sbatch >/dev/null && echo yes || echo no").strip() == "yes"
        if has_slurm and payload.get("localSlurm", True):
            out = run(cfg, f"cd {q(jobdir)} && sbatch --parsable job.sbatch")
            record["slurmId"] = out.strip().split(";")[0]
        else:
            proc = subprocess.Popen(["bash", f"{jobdir}/run_local.sh"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    start_new_session=True)
            record["pid"] = proc.pid
            record["slurmId"] = f"local:{proc.pid}"
    record["state"] = "PENDING"
    remember(record)
    return record


def describe_tasks(spec):
    kinds = {}
    for t in spec["tasks"]:
        if t["kind"] == "anneal":
            label = "anneal " + (t["anneal"].get("method") or "sa")
        elif t["kind"] == "qaoa":
            label = f"QAOA p={t['qaoa'].get('reps') or 1}"
        else:
            label = t["kind"]
        kinds[label] = kinds.get(label, 0) + 1
    return ", ".join(f"{v}x {k}" for k, v in kinds.items())


# -------------------------------------------------------------- registry
def jobs():
    try:
        with open(JOBS_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def remember(record):
    with _lock:
        os.makedirs(HPC_DIR, exist_ok=True)
        items = [j for j in jobs() if j.get("name") != record["name"]]
        items.insert(0, record)
        with open(JOBS_FILE, "w") as f:
            json.dump(items[:200], f, indent=1)


def find(name):
    for j in jobs():
        if j.get("name") == name:
            return j
    raise HpcError(f"unknown job {name}")


FINAL = {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "OUT_OF_MEMORY",
         "NODE_FAIL", "PREEMPTED", "BOOT_FAIL", "DEADLINE"}


def refresh():
    """Ask Slurm about every job that is not finished, update the registry."""
    cfg = load_config()
    items = jobs()
    live = [j for j in items if j.get("state") not in ("DONE", "FAILED", "CANCELLED")
            and j.get("transport", "ssh") == cfg["transport"]]
    if not live:
        return items
    slurm_ids = [j["slurmId"] for j in live if not str(j.get("slurmId", "")).startswith("local:")]
    states = {}
    if slurm_ids:
        ids = ",".join(slurm_ids)
        script = (f"sacct -n -P -X -j {q(ids)} -o JobID,State,Elapsed,ExitCode 2>/dev/null; "
                  f"echo '---'; squeue -h -j {q(ids)} -o '%i|%T|%M|%R' 2>/dev/null; true")
        out = run(cfg, script, timeout=60)
        acct, _, queue = out.partition("---")
        for line in acct.splitlines() + queue.splitlines():
            parts = line.strip().split("|")
            if len(parts) < 2 or not parts[0]:
                continue
            base = parts[0].split("_")[0].split(".")[0]
            state = parts[1].split()[0] if parts[1] else "?"
            states.setdefault(base, {})[parts[0]] = state
    results = {}
    script = "; ".join(
        f"echo {q(j['name'])}=$(ls {q(j['remoteDir'])}/results 2>/dev/null | "
        f"grep -c '^[0-9].*\\.json$')" for j in live if j.get("kind") == "job")
    if script:
        for line in run(cfg, script, timeout=60).splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                results[k] = int(v or 0)
    for j in live:
        sid = str(j.get("slurmId", ""))
        if sid.startswith("local:"):
            pid = int(sid.split(":")[1])
            alive = _pid_alive(pid)
            j["counts"] = {"RUNNING" if alive else "COMPLETED": 1}
            j["state"] = "RUNNING" if alive else "DONE"
        else:
            per = states.get(sid, {})
            counts = {}
            for key, state in per.items():
                if key == sid and "_" not in key and j.get("kind") == "job" and len(per) > 1:
                    continue
                counts[state] = counts.get(state, 0) + 1
            j["counts"] = counts
            if counts and all(s in FINAL for s in counts):
                j["state"] = ("DONE" if set(counts) == {"COMPLETED"} else
                              "CANCELLED" if "CANCELLED" in counts else "FAILED")
            elif counts:
                j["state"] = "RUNNING" if "RUNNING" in counts else "PENDING"
        if j.get("kind") == "job":
            j["resultsReady"] = results.get(j["name"], 0)
        j["checked"] = time.strftime("%H:%M:%S")
    with _lock:
        with open(JOBS_FILE, "w") as f:
            json.dump(items, f, indent=1)
    return items


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:                                   # a finished child that is not reaped yet
        done = os.waitpid(pid, os.WNOHANG)
        return done == (0, 0)
    except ChildProcessError:
        return True


def cancel(name):
    cfg = load_config()
    j = find(name)
    sid = str(j.get("slurmId", ""))
    if sid.startswith("local:"):
        try:
            os.killpg(int(sid.split(":")[1]), 15)
        except OSError:
            pass
    else:
        run(cfg, f"scancel {q(sid)}", timeout=60)
    j["state"] = "CANCELLED"
    remember(j)
    return j


def log(name, task=0, lines=200):
    cfg = load_config()
    j = find(name)
    lines = max(10, min(int(lines), 2000))
    if j.get("kind") == "image":
        path = j["log"]
        script = f"tail -n {lines} {q(path)} 2>/dev/null || echo '(no log yet)'"
    else:
        d = j["remoteDir"]
        script = (f"f=$(ls -t {q(d)}/logs/*_{int(task)}.out 2>/dev/null | head -1); "
                  f"if [ -n \"$f\" ]; then echo \"== $f\"; tail -n {lines} \"$f\"; "
                  f"else echo '(no log yet for task {int(task)})'; fi")
    return {"text": run(cfg, script, timeout=60)}


def local_dir(name):
    return os.path.join(HPC_DIR, slug(name))


def fetch(name):
    """Copy the job directory back (results, logs, inputs, spec) and summarise."""
    cfg = load_config()
    j = find(name)
    d = j["remoteDir"]
    data = run_bytes(cfg, f"cd {q(d)} && tar -czf - --exclude='*.sqfs' .", timeout=900)
    target = local_dir(name)
    if os.path.isdir(target):
        shutil.rmtree(target)
    os.makedirs(target)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        members = [m for m in tar.getmembers()
                   if not (m.name.startswith("/") or ".." in m.name.split("/"))
                   and (m.isfile() or m.isdir())]
        try:
            tar.extractall(target, members=members, filter="data")
        except TypeError:                     # Python < 3.12
            tar.extractall(target, members=members)
    rows = summarize_local(name)
    j["fetched"] = time.strftime("%Y-%m-%d %H:%M:%S")
    j["fetchedRows"] = len(rows)
    remember(j)
    return results(name)


def summarize_local(name):
    hpc_dir = os.path.join(ROOT, "hpc")
    if hpc_dir not in sys.path:
        sys.path.insert(0, hpc_dir)
    import brpq_job
    target = local_dir(name)
    os.makedirs(os.path.join(target, "results"), exist_ok=True)
    return brpq_job.summarize(target)


def results(name):
    target = local_dir(name)
    path = os.path.join(target, "results", "summary.json")
    if not os.path.isfile(path):
        raise HpcError("fetch the results first")
    with open(path) as f:
        rows = json.load(f)
    qubos = sorted(os.path.relpath(p, target) for p in
                   glob.glob(os.path.join(target, "results", "qubo", "*.qubo")))
    return {"name": name, "rows": rows, "qubos": qubos, "dir": target,
            "spec": _read_json(os.path.join(target, "spec.json"))}


def task_result(name, task_id):
    target = local_dir(name)
    path = os.path.join(target, "results", f"{slug(task_id)}.json")
    record = _read_json(path)
    if record is None:
        raise HpcError(f"no result for task {task_id}")
    return record


def _read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def import_qubo(name, rel):
    """Copy a QUBO exported on the cluster (and its sidecar) into qubo/, so the
    Quantum tab can load it like a local export."""
    target = local_dir(name)
    src = os.path.normpath(os.path.join(target, rel))
    if not src.startswith(target + os.sep) or not src.endswith(".qubo") or not os.path.isfile(src):
        raise HpcError("not a fetched QUBO file")
    os.makedirs(os.path.join(ROOT, "qubo"), exist_ok=True)
    dest_name = f"hpc-{os.path.basename(src)}"
    dest = os.path.join(ROOT, "qubo", dest_name)
    shutil.copyfile(src, dest)
    if os.path.isfile(src + ".json"):
        shutil.copyfile(src + ".json", dest + ".json")
    return {"qubo": "qubo/" + dest_name}


def info():
    return {"config": public_config(), "partitions": PARTITIONS,
            "gpuMemoryGB": GPU_MEMORY_GB, "jobs": jobs()}
