# TransitStress

Local development bootstrap for a planned TESS transit preprocessing robustness
experiment. Version 0.1.0 is a package version, not a public release. There is no
scientific pipeline, application interface, downloaded dataset or measured result yet.

Read `AUTHOR_GUIDE.md`, `STATE.md`, `AGENTS.md` and `SECURITY.md` before continuing.
Environment evidence and setup limitations are recorded in `docs/ENVIRONMENT.md`.

In a new PowerShell terminal in this folder:

```powershell
uv sync --locked
uv run --locked python -m transitstress doctor
uv run --locked python scripts/check.py --level quick
uv run --locked python scripts/check.py --level security
```

If the current terminal has not picked up uv's user PATH installation, substitute
`python -m uv` for `uv`. Python 3.13 runs the uv launcher only; the project uses
uv-managed Python 3.12 and its own `.venv`.

Doctor reads the draft protocol and reports allowlisted local setup facts without
acquiring data. T00 validation covers bootstrap identity, finite numbers, search
bounds, serial execution and analysis network prohibition. T01 adds complete typed
scientific contracts and validation of every nested setting. Neither doctor nor
the bootstrap validator authorizes a scientific run or protocol freeze.

`core`, `ui` and `release` report incomplete with nonzero status while their required
suites/artifact validators do not exist. `security-online` is a separate explicit
network advisory audit; ordinary checks are offline. Tests block unexpected socket
connections and DNS resolution and disable Astropy IERS auto-downloads.
