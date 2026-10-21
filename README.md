# TransitStress

See whether preprocessing preserves a signal deliberately added to a light curve.
This local workbench compares **two detrending configurations** using saved TESS
PDCSAP backgrounds and a frozen paired injection experiment. Version 0.1.0 is a
package version, not an approved public release. T10 is implemented; independent
G3 product/results and G4 release gates remain pending.

With the existing locked Python 3.12 environment, from this checkout in PowerShell:

```powershell
$env:TRANSITSTRESS_MODE = "demo"
uv run --locked streamlit run app.py --server.address 127.0.0.1
```

The four destinations are **Start**, **Workbench**, **Benchmark** and **Methods**.
Demo browses validated packaged evidence offline and denies uploads/new science,
including direct controller calls and URL/session mode attempts. Three detailed
curated examples accompany all 390 summary rows, honest filter denominators,
aggregate tables, downloads, provenance and a separate real Pi Mensae sanity check.
Uncurated cases explicitly offer summary-only evidence. Local exploratory work
uses session-private state and never enters the frozen table; see the
[T09 workbench guide](docs/T09_WORKBENCH.md).

Frozen T07 exact geometric matches: A **79/135**, B **74/135**. Eligible denominators
are also 135, with zero coverage exclusions or technical failures. These are
**135 correlated injection cases across five evaluation stars**, with paired
processing, not independent stars or a population superiority result. Matching
background qualifications remain visible. BLS/SNR are diagnostics, not planet
confirmation, calibrated probabilities or survey completeness.

[T10 guide/evidence](docs/T10_DEMO.md), [browser captures](docs/visual/T10/README.md)
and [unchanged T07 review](docs/reviews/T07.md) distinguish results, builder checks
and pending gates. No publication, deployment or hosted CI pass is claimed.

Read `AUTHOR_GUIDE.md`, `STATE.md`, `AGENTS.md` and `SECURITY.md` before continuing.
Environment evidence and setup limitations are recorded in `docs/ENVIRONMENT.md`.

In a new PowerShell terminal in this folder:

```powershell
python -m uv sync --locked
python -m uv run --locked python -m transitstress doctor
python -m uv run --locked python scripts/check.py --level quick
python -m uv run --locked python scripts/check.py --level security
```

These commands use uv's Python module without relying on its executable PATH.
Python 3.13 runs the uv launcher only; the project uses Python 3.12 in ignored
`.tools/python` and its own `.venv`. See the environment repair record if recreating it.

Doctor reads the protocol and reports allowlisted local setup facts without
acquiring data. T00 validation covers bootstrap identity, finite numbers, search
bounds, serial execution and analysis network prohibition. T01 adds complete typed
scientific contracts and validation of every nested setting. Neither doctor nor
the bootstrap validator authorizes a scientific run or protocol freeze.

`core` runs numerical unit/integration fixtures. `ui` runs AppTest regressions.
`uv run --locked python -m transitstress.ui.bundle` validates the complete packaged
demo offline. The existing release wrapper includes that validator, but a release
gate/clean-clone reproduction has not been performed by this T10 assignment.
`security-online` is a separate explicit
network advisory audit; ordinary checks are offline. Tests block unexpected socket
connections and DNS resolution and disable Astropy IERS auto-downloads.

The offline known-signal command consumes the exact local cached source:

```powershell
python -m uv run --locked python -m transitstress sanity --manifest data/manifest.json --output runs/sanity
```

See [T04 implementation](docs/T04_SEARCH.md) and
[actual completion evidence](reports/T04_COMPLETION.md). The small known-signal
artifact is separate from the seven-star benchmark and is not a planet discovery
or confirmation. Frozen development/evaluation runs now exist; the demo reads
their saved evidence and never starts another search. Current online audit,
all-history scanning, native-link proof and clone/source-data reproduction remain
separate pending evidence. The next assignment is the fresh-context, source-read-only
G3 review in `NEXT_SESSION_PROMPT.md`.
