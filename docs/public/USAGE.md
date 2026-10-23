# Public usage guide

This concise public derivative replaces operational setup instructions in the
distribution. It is not a historical test record. Start with the root README's
locked Python 3.12 setup and loopback launch. The native dark/gold theme, CORS,
XSRF, 5 MB uploader setting, disabled usage telemetry and hidden public traceback
details are shipped in .streamlit/config.toml.

The source archive is the runnable application. Its demo/t10 directory contains
the manifest and 28 required transport members: saved rows, three detailed pairs,
tables, images, freeze/identity and review documentation. demo/t08 supplies the
earlier reference assets. Do not mix members from different candidates. Corrupt
or missing assets must be restored from the same archive; startup does not fetch
or recompute them.

The wheel installs the CLI/library: `python -m pip install transitstress-0.1.0-py3-none-any.whl`,
then `transitstress --help`. It does not contain source-root dashboard assets.
Ordinary pip resolution enforces the wheel floors; exact tested versions are
recorded in the source lock, not guaranteed by an unconstrained future resolver.

For optional local CSV analysis, use a Git checkout with a real current commit,
then launch separately from its root. Current computation records Git execution
provenance: an extracted sdist without .git cannot perform Run comparison.
The public saved demo and exports have no such requirement. This candidate does
not create a public Git repository or fabricate development commits:

```sh
TRANSITSTRESS_MODE=local uv run --locked --offline --no-sync streamlit run app.py --server.address 127.0.0.1 --server.headless true
```

Upload uncompressed UTF-8 CSV with unique time_days,flux,flux_err headers; confirm
time in days, positive normalized dimensionless linear flux and positive error
in the same units. Limits: 5 MiB, 50,000 rows, 24–40 days baseline and at least
2,000 occupied prepared bins. Duplicate times, invalid cells, formulas, nonfinite
values and nonpositive flux/errors are rejected, not repaired. Extra columns are
ignored with a notice and are not exported. The included sample has 3,001 rows
over 27 days and is synthetic constant flux, not a TESS measurement.

Choose bounded settings and press Run comparison explicitly. Changes mark prior
output stale. Local results remain exploratory, session-owned and separate from
the historical evaluation. Demo mode rejects uploads and computation at controller
entry points. Development built-ins need matching prepared cache; prefer a valid
CSV when it is absent. Local launch remains loopback only.

A 750 ppm box at normalized flux 1 subtracts 0.000750. Flux is not magnitude.
Plot zoom/overlay change display only. Detailed exports contain typed rows and
matching arrays/provenance/checksums; summary-only exports explicitly omit arrays.
No local result becomes part of the saved benchmark.

Offline fixtures are available under tests. With development dependencies installed,
`uv run --locked --offline --no-sync python -m pytest tests/unit tests/integration tests/security`
runs their bounded calculations with network-blocking fixtures in a Git checkout.
In an extracted sdist, the private-computation export fixture cannot obtain Git
provenance; this is an explicit limitation, not a skipped or passing test. The complete
development release wrapper additionally needs Git and the pinned Gitleaks binary;
that executable is deliberately not distributed. Do not interpret its missing-tool
failure as a test pass. Hosted CI and tool downloads are separate release steps.
