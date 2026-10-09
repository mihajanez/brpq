# Running brpq on FRIDA (HPC)

FRIDA is the Slurm cluster of UL FRI ([docs.rdc.si](https://docs.rdc.si/)). brpq uses it
for two things that outgrow a workstation:

* **Classical solving**: the Tanaka–Voß IP with Gurobi on many test cases at once
  (one Slurm array task per test case), with long time limits and many threads.
* **Classical simulation of the quantum algorithms**: D-Wave samplers on many QUBOs and
  settings in parallel, and QAOA statevector simulation beyond the 24 qubits the GUI handles
  locally. That means about 33 qubits on a 2 TB CPU node with Qiskit Aer, or about 30–32 on a
  single H100/B200/B300 GPU with CuPy.

Real quantum hardware (D-Wave Leap, IBM Quantum) is still reached from the GUI's Quantum
tab. FRIDA runs the classical side and the simulations.

Everything can be driven from the GUI (**HPC · FRIDA** tab) or by hand with `sbatch`.

## How it fits together

| Where | What |
|---|---|
| your computer | the GUI; `ssh` through Teleport (`tsh`) to `login-frida` |
| `~/brpq/repo` on FRIDA | a copy of this project (GUI: *Copy project to cluster*) |
| `~/brpq/images/brpq-cpu.sqfs` | Enroot image: Ubuntu 24.04, Gurobi 13, Ocean, Qiskit, Aer |
| `~/brpq/images/brpq-gpu.sqfs` | the same plus CuPy for CUDA 12 (QAOA on NVIDIA GPUs) |
| `~/brpq/jobs/<name>/` | one job: `spec.json`, `job.sbatch`, `step.sh`, `inputs/`, `logs/`, `results/` |
| compute node | `srun --container-image=… bash step.sh` → `python hpc/brpq_job.py run spec.json --task $SLURM_ARRAY_TASK_ID` |

FRIDA has no environment modules and runs everything in containers (Enroot + Pyxis). The
image is built once, by a Slurm job, from `hpc/container_setup.sh`.

## 1. One-time setup on your computer

1. **Get an account.** A UL FRI employee asks <frida@rdc.si> (lab, project, duration).
   Accounts with no jobs for 6 months are disabled.
2. **Register with Teleport** from the e-mailed link: password and OTP device, optionally a
   hardware key (Touch ID, Windows Hello, YubiKey) for passwordless CLI login.
3. **Install `tsh`** (Teleport Community Edition) where the GUI runs. If the GUI runs in WSL,
   that means the Linux `tsh` inside WSL.
4. **Log in** (once per working day; the certificate expires):

       tsh --proxy=rdc.si --user=<username> login
       # without a registered hardware key add: --mfa-mode=otp --auth=local

5. **Configure SSH**: run `tsh config` and put its output at the top of `~/.ssh/config`. For a
   short name that the GUI and VS Code can use, add (Linux paths; adjust `tsh` and key paths):

       Host login-frida
           Hostname login-frida.rdc.si
           User <username>
           UserKnownHostsFile "/home/<you>/.tsh/known_hosts"
           IdentityFile "/home/<you>/.tsh/keys/rdc.si/<username>"
           CertificateFile "/home/<you>/.tsh/keys/rdc.si/<username>-ssh/rdc.si-cert.pub"
           Port 3022
           ProxyCommand "/usr/local/bin/tsh" proxy ssh --cluster=rdc.si --proxy=rdc.si:443 %r@%h:%p

6. **Test**: `ssh login-frida hostname` must answer without a prompt. The GUI runs `ssh` in
   batch mode and never asks for a password or OTP. When the Teleport certificate expires it
   tells you to run `tsh login` again.

## 2. One-time setup on FRIDA

1. **Gurobi licence.** A named-user academic licence is tied to one machine and does not work
   on compute nodes. Use a licence that works in containers, for example an academic **WLS**
   (Web License Service) licence from the Gurobi user portal, or your university's token
   server. Put the licence file at `~/gurobi.lic` on FRIDA, or point the GUI field
   *Gurobi licence file* at it. Jobs mount it read-only and set `GRB_LICENSE_FILE`.
   QUBO-only jobs (annealing, QAOA) do not need it.
2. **Copy the project**: GUI → *Save & test connection*, then *Copy project to cluster*.
   This copies the project as a tar over ssh, without `.git`, `.venv`, builds or `quantum_runs`.
   Repeat after code changes. By hand:

       tar --exclude=.git --exclude=.venv --exclude=quantum_runs -czf - . \
         | ssh login-frida 'mkdir -p ~/brpq/repo && tar -xzf - -C ~/brpq/repo'

3. **Build the images**: GUI → *Build CPU image* (and *Build GPU image* if you want CuPy). By
   hand:

       ssh login-frida
       cd ~/brpq/repo
       sbatch hpc/build_image.sbatch cpu      # ~/brpq/images/brpq-cpu.sqfs
       sbatch hpc/build_image.sbatch gpu      # ~/brpq/images/brpq-gpu.sqfs
       # another Gurobi release: GUROBI_VERSION=12.0.3 sbatch hpc/build_image.sbatch cpu

   The build runs on the `dev` partition (10–30 min). It pulls `ubuntu:24.04`, installs build
   tools, Boost, Gurobi under `/opt/gurobi1300` (the Makefile's path) and a Python venv with
   `hpc/requirements.txt`, and saves the container as squashfs. The first job that needs the
   solver compiles `rbrp_ip` inside the image; concurrent tasks wait on a lock.

## 3. Running jobs from the GUI

*HPC · FRIDA* tab → **2 · Build a job**:

| Mode | One array task per … | What each task does |
|---|---|---|
| Classical IP solve | selected test case | `rbrp_ip` with the sidebar's parameters; exports the QUBO |
| Full pipeline per test case | selected test case | classical solve + export, then every chosen sampler and QAOA depth on that QUBO |
| Quantum simulation of the loaded QUBO | sampler or QAOA depth | one run on the QUBO loaded in the Quantum tab (uploaded with the job) |

Choose resources per array task (partition, time, CPUs, memory, GPU, QAOA simulator,
maximum qubits, how many tasks may run at once), press **Preview job script** to see
`job.sbatch`, `step.sh` and `spec.json`, then **Submit to Slurm**.

**3 · Jobs** shows each job's array state (from `sacct`/`squeue`, refreshed every 30 s while
the tab is open), how many result files exist, and per-task logs. **Fetch** copies the job
directory into `quantum_runs/hpc/<name>/` and writes `results/summary.csv`. The results
table then lets you:

* open a classical result in the move-plan player (**Solution**);
* load a QUBO exported on FRIDA into the Quantum tab (**Load QUBO**, copied to
  `qubo/hpc-<name>.qubo` with its sidecar);
* play the best replayable sample of an annealing or QAOA run (**Plan**);
* add a run to the Quantum tab's *Results & comparison* when the same QUBO is loaded
  (**Compare**).

Transport *This machine* runs the same job directory locally: with `sbatch` if this machine
is a Slurm node, otherwise the tasks run one after another without a container. Use it to
test a spec, or when the GUI itself runs on a cluster.

## 4. Running jobs by hand

A job is just a directory. Write `spec.json` (format at the top of `hpc/brpq_job.py`):

    {"name": "sweep-05", "tasks": [
      {"kind": "pipeline", "instance": "data05-06-08.dat", "options": {"emptyTiers": 2, "timeLimit": 3600},
       "anneal": [{"method": "sa", "reads": 5000}, {"method": "tabu", "reads": 100}],
       "qaoa":   [{"reps": 1, "restarts": 8, "maxiter": 300}, {"reps": 2, "restarts": 8, "maxiter": 300}]},
      {"kind": "qaoa", "qubo": "inputs/big.qubo", "qaoa": {"reps": 1, "restarts": 4, "cvar": 0.25}}]}

The simplest way to get a matching `job.sbatch` and `step.sh` is *Preview* in the GUI. A
minimal hand-written array job, run from the job directory, looks like this:

    #!/bin/bash
    #SBATCH --job-name=brpq-sweep
    #SBATCH --partition=amd            # CPU-heavy work; frida for GPUs
    #SBATCH --time=1-00:00:00
    #SBATCH --cpus-per-task=32
    #SBATCH --mem=128G
    #SBATCH --array=0-39%10            # 40 tasks, at most 10 at once
    #SBATCH --output=logs/%A_%a.out
    BASE=$HOME/brpq
    srun --container-image=$BASE/images/brpq-cpu.sqfs \
         --container-mounts=$BASE:$BASE,$HOME/gurobi.lic:$HOME/gurobi.lic:ro \
         --container-workdir=$BASE/repo \
         bash -c ". /opt/brpq-image.env; export GRB_LICENSE_FILE=$HOME/gurobi.lic \
                  OMP_NUM_THREADS=\$SLURM_CPUS_PER_TASK BRPQ_EXACT_LIMIT=30; \
                  make -s rbrp_ip GUROBI_LIBS=\"\$GUROBI_LIBS\"; \
                  python hpc/brpq_job.py run $PWD/spec.json --task \$SLURM_ARRAY_TASK_ID"

Follow and collect:

    squeue --me                         # or: frida (status, last jobs, storage)
    sacct -j <jobid> -o JobID,State,Elapsed,MaxRSS
    tail -f logs/<jobid>_0.out
    python3 ~/brpq/repo/hpc/brpq_job.py summarize .     # results/summary.csv (login node is fine)
    scp -r login-frida:brpq/jobs/sweep-05/results .     # on your computer

## 5. Choosing resources

| Work | Partition | Notes |
|---|---|---|
| IP solves, annealing samplers | `amd` (2× EPYC 9684X, 368 vCPU) or `frida` | Gurobi threads = CPUs per task |
| QAOA, Aer on CPU | `frida` big-memory nodes (`axa`, `ixh`, `ixb*`: 2 TB) or `amd` (755 GiB) | `OMP_NUM_THREADS` = CPUs |
| QAOA, CuPy on GPU | `frida` with `--gres=gpu:H100:1`, `B200` or `B300` | needs the GPU image |
| testing a job | `dev` (max 12 h) | FRIDA does not allow production runs on `dev` |

Memory for exact QAOA simulation grows as 2^n (statevector, probabilities and the energy
table of all 2^n assignments):

| qubits n | CPU RAM (≈ 72 B · 2^n) | GPU memory (≈ 48 B · 2^n) |
|---|---|---|
| 26 | 4.8 GB | 3.2 GB |
| 28 | 19 GB | 13 GB |
| 30 | 77 GB | 52 GB (A100 80 GB, H100) |
| 31 | 155 GB | 103 GB (B200) |
| 32 | 309 GB | 206 GB (B300) |
| 33 | 618 GB | — |
| 34 | 1.2 TB | — |

Set *Max qubits simulated* (`BRPQ_EXACT_LIMIT`) to what the requested memory holds. Time
per optimiser evaluation grows about as n · 2^n, so plan restarts × iterations accordingly.
CVaR with α < 1 sorts all 2^n energies on every evaluation, which is noticeably slower above
about 26 qubits. Noisy (device-model) sampling simulates shot by shot and is practical only
for small circuits.

## 6. Troubleshooting

* **ssh fails / exit 255**: run `tsh login` again; check `ssh login-frida hostname`.
* **"image missing"**: build it (step 2.3). Check `~/brpq/images/build-*.out`.
* **Gurobi licence errors in `logs/build-*.log` or task logs**: the licence must be valid on
  compute nodes (WLS or token server), and WLS needs outbound internet from the node.
* **Jobs stay pending**: `sprio`, `squeue -p frida`, `frida` for notices and free GPUs;
  smaller `--mem`/`--time` start sooner.
* **A task failed**: its log is under *Jobs → Log* (choose the task number) and the
  traceback is in `results/<task>.json`.
