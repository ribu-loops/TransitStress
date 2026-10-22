# Sample input and commands

`synthetic_constant.csv` is an explicitly illustrative **synthetic fixture**:
3,001 points over 27 days, constant normalized linear flux 1, uncertainty 0.001,
time in days. It is not TESS data, a measured result or a benchmark negative.
It demonstrates the required UTF-8 CSV headers and input units; it is unsuitable
for scientific conclusions.

Local input requires unique headers `time_days,flux,flux_err`, finite positive
flux/errors, unique times, <=5 MiB and <=50,000 data rows; after preparation,
24–40 days baseline and >=2,000 occupied bins. Extra columns are ignored with a
notice and are never executed/exported.

Offline inspection, without preparation or search:

```sh
uv run --locked --offline --no-sync python -m transitstress case --help
```

The following is a **future explicitly authorized exploratory command**. It
computes science, so it is not part of the T11 documentation/installation checks:

```sh
uv run --locked --offline --no-sync python -m transitstress case --csv examples/synthetic_constant.csv --confirm-days-linear-units --period-days 2.37 --depth-ppm 750 --duration-hours 3 --epoch-fraction 0.31 --mask original --output runs/synthetic-example
```

Its output would be exploratory synthetic-fixture evidence, never an addition to
the frozen 390-row evaluation. No output or expected recovery is claimed here.
