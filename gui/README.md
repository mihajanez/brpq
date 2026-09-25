# RBRP solver GUI

A small browser front end for `rbrp_ip`: pick a test case from `data/`, set the
solver parameters, run the classical (Gurobi) IP model and step through the
resulting move plan.

## Run

    make gui                       # builds rbrp_ip if needed, then opens a browser

or directly:

    .venv/bin/python -m gui.server           # http://127.0.0.1:8765
    .venv/bin/python -m gui.server --port 9000 --open

If the port is already taken the server moves to the next free one and prints the
URL it ended up on. Under WSL, note that a *Windows* program listening on the same
port wins the `localhost` forward: the browser then shows that program instead of
this GUI, even though the server is running fine. Check with

    powershell.exe -NoProfile -Command "Get-NetTCPConnection -LocalPort 8765 -State Listen"

and pick another `--port` if something answers.

Only the Python standard library is used, so the system `python3` (>= 3.8) works
just as well as the project's `.venv`.

## What it shows

* **Test case** — every `.dat`/`.txt` instance in `data/` (plus `.dat` files in
  the project root), with stacks / tiers / blocks and a live preview of the bay.
* **Parameters** — one control per `rbrp_ip` flag (`-E -T -t -m -s -g -u -v -Q`),
  each with a short explanation. A blank field means the flag is not passed.
* **Design your own** — the second tab of *Test case* swaps the file list for an
  **instance designer**: set the number of stacks and tiers, type a priority into each
  slot (1 leaves the bay first), or hit *Random*, *Renumber*, *Copy selected file* and
  *Clear*. Blocks settle to the bottom of their stack, duplicates are flagged, and the
  slots are shaded like the bay. It runs like any other test case, and *Save to
  data/custom/* writes a `.dat` file that then shows up in the file list as
  `custom/<name>.dat`.
* **QUBO model** — loads a `.qubo` file exported with `-Q` and shows what is in it:
  a heatmap of the coefficient matrix (hover any cell for the two columns and their
  bias, with a line marking where the sequence columns end and the slack columns
  begin), the column count split into sequence and slack variables, the one-hot
  groups, how many selections the penalties admit, and a plain-language reading of
  what minimising the energy means. From there you can score a selection three ways:
  *cheapest column per block*, *find the best replayable selection* (walks every
  one-per-block selection, replaying each against the chosen instance), or by pasting
  column names or a sampler bitstring. Any selection that replays can be shown as a
  move plan in the same animated player as a solver result.
* **Settings** — light / dark / follow-system appearance.
* **Header** — the exact command that will run, the solver-binary status and the
  Run button. While a solve is in flight that button turns into **Stop solver**
  (the busy panel carries a second one, next to the elapsed time). Stopping
  signals the whole process group — `rbrp_ip` and anything it started — with
  SIGTERM, then SIGKILL if it does not go, and the GUI reports the best bounds
  the solver had printed before it died.
* **Results** — relocation count, lower bound, greedy upper bound, solve time and
  model size; the move plan as an animated bay (play / step / scrub); the
  relocation table; a plain-language interpretation; and the raw solver log.

Blocks are shaded by retrieval order — the darkest block leaves first. The red
outline marks the current target block, the orange outline the block in motion, and
a diagonal hatch marks the **blocking** blocks: those sitting above a more urgent
block, which must therefore be relocated. Their count is recomputed at every step
(shown beside the caption) and the initial count is a lower bound on the number of
relocations, so it is reported as its own stat tile.

## Deep links

The current selection can be put in the URL, which is handy for sharing a case or
re-running one:

    http://127.0.0.1:8765/?instance=data05-08-39.dat&E=2&t=60&run=1

Supported keys: `instance`, `E`, `T`, `t`, `m`, `s`, `g`, `u`, `run`. A saved design
is addressed as `instance=custom%2F<name>.dat`.

A designed instance that is only run, never saved, is written to a temporary file for
the duration of the run and deleted afterwards — nothing lands in `data/` unless you
press *Save*.

## Layout

| File | Role |
|------|------|
| `server.py` | stdlib HTTP server: static files + `/api/instances`, `/api/instance`, `/api/solve`, `/api/save-instance`, `/api/stop` |
| `brp_instance.py` | `.dat` readers (Caserta / Bacci / Exposito-Izquierdo), the `.dat` writer used by the designer, and the move-plan replay |
| `runner.py` | builds the `rbrp_ip` command line, runs it, parses stdout/stderr |
| `qubo.py` | reads an exported `.qubo` file, scores selections, replays one into a plan |
| `static/` | `index.html`, `styles.css`, `app.js`, `qubo.js` |

Each solve runs as a child in its own process group, registered under the run id the
browser generates, which is what makes a targeted stop possible. `POST /api/stop` with
`{"runId": "…"}` stops that run; with `{}` it stops every run in flight — useful if a
browser tab was closed while the solver was still going.

`runner.py` reads the relocations out of the solver's own output and
`brp_instance.replay()` re-derives every intermediate bay state the same way
`Solution::print()` walks a plan, so the animation matches what the solver prints.

## Reading a QUBO

A `.qubo` file records the coefficient matrix plus, per column, the blocking block it
belongs to, the relocations it stands for and what they cost — the same
`# var / # cost / # reloc` metadata `verify_solution.py` replays from the command
line. Two things it does *not* record: which instance it was exported from, and the
height limit that was in force.

The GUI works around both. It compares the QUBO's one-hot groups against the blocking
blocks of the selected test case and shows a badge saying whether they match, and it
replays plans under the height limit from the parameter panel, saying so. If a search
reports that no selection replays, the usual cause is that the export was made with
different `-E`/`-T` settings than the panel currently holds.

`qaoa_qubo.py` and `sample_qubo.py` are still command-line tools; the GUI covers the
export, the inspection and the interpretation of a returned bitstring.
