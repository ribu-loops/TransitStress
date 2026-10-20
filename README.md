# TransitStress

Local TESS transit preprocessing robustness experiment under development.
Version 0.1.0 is a package version, not a public release. T04 adds blind
detrending/BLS and a separately labelled real Pi Mensae sanity check to the
pinned preparation/injection/mask primitives. Independent G1 review is pending;
the evaluator, benchmark runs, dashboard and release are not complete.

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

Doctor reads the draft protocol and reports allowlisted local setup facts without
acquiring data. T00 validation covers bootstrap identity, finite numbers, search
bounds, serial execution and analysis network prohibition. T01 adds complete typed
scientific contracts and validation of every nested setting. Neither doctor nor
the bootstrap validator authorizes a scientific run or protocol freeze.

`core` runs the available numerical unit/integration fixtures. `ui` and `release`
remain incomplete while their required suites/artifact validators do not exist.
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
or confirmation. No development benchmark or evaluation search has run.
