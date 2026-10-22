# TransitStress

**Does preprocessing preserve a signal deliberately added to a light curve?**
TransitStress compares two detrending configurations (0.5 and 1.5 day rolling
medians) on prepared TESS backgrounds, with blind Box Least Squares (BLS)
searches and post-search paired diagnostics.

## Contribution

The original contribution is the bounded experiment, truth-isolated orchestration,
paired retention/recovery evaluation, provenance and failure-preserving artifacts,
and a local workbench with an offline evidence browser. Astropy supplies BLS;
Lightkurve reads extracted TESS products. This project does not invent those
methods. See [methods/results](docs/METHODS_RESULTS.md),
[data/software attribution](docs/ATTRIBUTION.md) and
[contribution/AI notes](CONTRIBUTIONS.md).

## Evidence

The historical protocol-v1-cohort1 evaluation contains **five evaluation stars,
135 injection cases, two detrending configurations and paired processing**.
Its 390 rows include 270 injection, 30 real-background and 90 Gaussian-null rows.

| Historical evaluation | A / 0.5 d | B / 1.5 d |
| --- | ---: | ---: |
| Exact geometric top-peak matches / planned | 79/135 (58.5%) | 74/135 (54.8%) |
| Exact / coverage-eligible | 79/135 | 74/135 |
| Insufficient coverage / technical errors | 0 / 0 | 0 / 0 |
| Median paired depth retention ratio | 0.8144 | 0.9732 |

These are saved measured results, not a fresh execution of today's source.
[T07's independent results review](docs/reviews/T07.md) and
[all-row tables](reports/t07_evaluation/results.csv) remain available.
[The review pack](docs/review_pack/OVERVIEW.md) supplies one overview, one figure,
run instructions and three methodological questions.

## Limits and status

Repeated cases are correlated and reuse five stars; they are not 135 independent
stars. This convenience cohort, additive box injections after binning, one
duration, bundled period/phase families and one missingness realization do not
establish survey completeness or a generally superior detrending method.
BLS peaks/SNR are diagnostics, not planet confirmation or calibrated probability.

**Combined G3 PASS applies to accepted Linux/Ubuntu WSL scope**, through the
[original final review](docs/reviews/G3_LINUX_FINAL_REVIEW.md) plus its unchanged
[LNX-1 closure](docs/reviews/G3_LNX1_CLOSURE.md). T11 is WIP; G4 and public release
are not approved. Native Windows execution and link proof remain **unavailable,
not passed**. Hosted Ubuntu/Windows CI is pending a later authorized push.
No deployment or external endorsement is claimed.

Historical results were produced at `2c897282f6db9e959a373896f1b3201859d83e70`.
The later [g3-wsl-cache-envelope-v1 correction](docs/corrections/G3_WSL_CACHE_V1.md)
has a distinct scientific identity. T11's [package selection revision](docs/corrections/T11_PACKAGE_V1.md)
also versions the metadata fingerprint. The original freeze/tag does not authorize
corrected-source evaluation or resume. The authorized source-data attempt [stopped before preparation](docs/T11_REPRODUCTION_OUTCOME.md)
on a clone byte-identity mismatch. Reproduction and author
understanding evidence remain pending; see [the evidence ledger](docs/T11_VERIFICATION.md).

## Installation

Use Python **3.12.15** and **uv 0.13.1**, with the committed `uv.lock`.
The current supported execution scope is Linux x86-64 / Ubuntu WSL.
From a checkout root in an Ubuntu shell:

```sh
uv sync --locked --python 3.12.15
uv run --locked python -m transitstress doctor
```

Installation needs the exact cached distributions or registry access; application
demo browsing does not need network. Do not upgrade individual dependencies.
For the fresh local clone, wheel and verification procedure, including scanner
setup, see [run instructions](docs/RUNNING.md). The
[prepared CI matrix](.github/workflows/offline.yml) is an unexecuted workflow.

## Offline demo

```sh
TRANSITSTRESS_MODE=demo uv run --locked --offline --no-sync streamlit run app.py --server.address 127.0.0.1
```

Open the loopback URL printed by Streamlit. Start, Workbench, Benchmark and Methods
browse packaged evidence; demo rejects uploads and new computation. No MAST
access, API key or runtime AI is required. Three curated detailed cases accompany
all summary rows; other cases are labelled summary-only. Pi Mensae is a separate
real known-signal sanity check.

[Local input guide](docs/T09_WORKBENCH.md), [demo guide](docs/T10_DEMO.md) and
[sample-file guide](examples/README.md) explain the separate exploratory path.
Source-data execution requires the separately authorized
[bounded reproduction proposal](docs/T11_REPRODUCTION_PROPOSAL.md).
Original implementation is [MIT licensed](LICENSE); data and dependencies retain
their own rights and [notices](NOTICE).
