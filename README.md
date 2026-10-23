# TransitStress

**How do processing choices and missing observations affect an artificial transit signal?**

TransitStress is a learning and analysis workbench. A light curve records brightness over time. Stars and instruments can vary slowly;
a planet passing in front of a star can produce a much smaller, repeated dip.
Detrending estimates and removes the slow variation, but can also weaken that dip.

TransitStress inserts artificial box-shaped dips into real, prepared TESS
observations and compares two rolling-median detrending windows: **A, 0.5 days**
and **B, 1.5 days**. Each processed light curve is searched with Astropy's
Box Least Squares (BLS), a method for finding repeated dips. The search is blind:
the injected timing is used only afterward to score and explain the saved result.
Synthetic injections are experiments, **not discovered planets**.

The revised interface leads through one saved worked example: observation →
artificial signal → processing comparison → interpretation → evidence.
[Current walkthrough and verification](docs/UI_REVISION.md). Historical T08/T10
screenshots and release candidates are retained, but do not verify this revision.

## What was built and measured

The project combines a bounded paired experiment, provenance and failure records,
a local workbench, and an offline browser of saved results. Astropy supplies BLS;
Lightkurve reads TESS products. This project does not invent those methods.

The historical evaluation used **five evaluation stars, 135 injection cases,
two detrending configurations and paired processing**. Each case is processed
both ways; these are not 135 independent stars.

| Saved synthetic evaluation | A / 0.5 d | B / 1.5 d |
| --- | ---: | ---: |
| Exact geometric top-peak matches / planned cases | 79/135 (58.5%) | 74/135 (54.8%) |
| Exact / coverage-eligible cases | 79/135 | 74/135 |
| Insufficient coverage / technical errors | 0 / 0 | 0 / 0 |
| Median paired depth retention ratio | 0.8144 | 0.9732 |

“Exact” requires agreement in period and timing. Eligibility requires at least
two fully contained events with at least three retained points each, and six
in-transit points overall. All 135 cases were eligible. Neither configuration
had an already-matching uninjected-background top peak among eligible injection
cases; this qualification is not proof of causality or authenticity.
Retention compares injected and uninjected processing on identical timestamps.
Keeping more depth and finding the correct strongest peak are different outcomes.

These are saved measurements from the historical frozen source, not a new run of
today's source. See [methods, per-star results and limitations](docs/METHODS_RESULTS.md),
[all result rows](reports/t07_evaluation/results.csv) and the
[independent results review](docs/reviews/T07.md).
The real **Pi Mensae** known-signal sanity check is separate from these synthetic cases.

## Limits and current status

Five stars, correlated repeated cases, simple additive boxes after binning, one
injected duration and limited gap patterns do not establish survey completeness
or a generally superior detrending method. BLS peaks and signal-to-noise ratios
are diagnostics, not planet confirmation or calibrated probabilities.

Accepted G3 review applies to [Linux/Ubuntu WSL](docs/reviews/G3_LINUX_FINAL_REVIEW.md)
with its [LNX-1 closure](docs/reviews/G3_LNX1_CLOSURE.md).
One later source-data self-reproduction completed its bounded comparison and received
[independent scoped outcome PASS](docs/reviews/T11_REPRODUCTION_V5_OUTCOME_REVIEW.md).
It was assistant-performed, not an external-human rerun, and does not validate all
inputs or reproduce the full evaluation. Original failures and distinct source
identities remain in [the evidence ledger](docs/T11_CLOSEOUT.md).
The historical freeze does not authorize corrected-source evaluation or resume.

**Local presentation candidate only: visual feedback and independent revision review
are pending. T11 remains WIP and independent G4 is pending.**
The current dependency audit has unresolved certifi/Pillow findings; remediation
and independent dependency review have not passed. Privacy/publication holds remain.
No dependency change or repeat advisory audit is part of this UI revision.
Author study is deferred and unassessed. Native Windows execution/link proof is
unavailable, not passed; hosted Ubuntu/Windows CI awaits a later authorized push.
Public repository, PyPI release and hosted demo destinations are pending.

## Setup and launch

Verified scope: **Linux x86-64 / Ubuntu WSL**, Python **3.12.15**, **uv 0.13.1**.
Use a source checkout or extracted source distribution for the dashboard.
The wheel supplies the library/CLI without dashboard assets. Install Python and
uv first; no API key, GPU or runtime AI is needed.

From the source root in an Ubuntu shell:

```sh
uv sync --locked --python 3.12.15
uv run --locked --offline --no-sync python -m transitstress doctor
TRANSITSTRESS_MODE=demo uv run --locked --offline --no-sync streamlit run app.py --server.address 127.0.0.1
```

Initial installation normally downloads dependencies unless the interpreter and
locked packages are cached. After setup, the precomputed demo runs offline.
Open the loopback URL printed by Streamlit; stop with Ctrl+C.
[Detailed setup and verification](docs/RUNNING.md);
[what each distribution contains](docs/DISTRIBUTION.md).

## Explore saved results or compute locally

**Precomputed demo, three steps:**

1. On **Start**, choose **Explore a worked example**. Read the real prepared TESS
   observation and the artificial signal added to it.
2. Use **Compare**, then **Interpret**. White A / 0.5-day and yellow B / 1.5-day
   plots share scales. In this deliberately selected disagreement case, B retains
   more depth while A recovers the intended period and timing. It is not typical
   of every case.
3. Open **Evidence** to download the complete saved pair, or **Browse benchmark**
   for all 390 rows, filter denominators, controls and summary-only cases.

Normalized flux is brightness relative to a reference level near 1. A depth in
ppm is parts per million; period is the time between repeated dips. The processing
window is the moving interval used to estimate slow variation. Three evaluation
cases have detailed arrays. Navigation, filters and display zoom do not run
science; demo mode denies uploads and new computation. **Methods** keeps precise
definitions and provenance accessible. Local analysis is an advanced capability
requiring a separate local-mode launch and explicit **Run comparison**.

**Local computation:** launch separately:

```sh
TRANSITSTRESS_MODE=local uv run --locked --offline --no-sync streamlit run app.py --server.address 127.0.0.1
```

Choose a development built-in with its required prepared cache, or upload a valid
CSV and confirm its units. Configure the bounded injection, explicitly press
**Run**, inspect the A/B timeline, trends, BLS peaks and folds, then export case
evidence. Input changes mark old output stale. Local results are exploratory and
never join the frozen evaluation. Built-ins need existing pinned prepared data;
startup does not download it. The development V4/V5 authorizations are spent;
old proposals are not standing permission for another run.

### CSV input

Use UTF-8 CSV with unique headers `time_days,flux,flux_err`. Time is in **days**;
flux is positive, normalized, dimensionless linear brightness (not magnitudes);
uncertainty is positive in the same flux units. At normalized flux 1, 750 ppm is
a relative dip of 0.000750. Absolute epoch may be unknown for an uploaded CSV.

The complete [minimal valid sample](examples/synthetic_constant.csv) has 3,001
points spanning 27 days. It is an explicitly synthetic constant-flux fixture,
not a measured TESS result. A three-row header preview alone would be too short.
[Sample details](examples/README.md).

| Common error | Corrective action |
| --- | --- |
| Missing/duplicate headers, malformed cells or formulas | Supply unique required columns containing finite numbers. |
| Nonpositive flux/error or duplicate times | Correct source data; invalid values are rejected, not silently repaired. |
| Wrong units or too little time support | Convert to days and normalized linear flux; require 24–40 days and at least 2,000 occupied prepared bins. |
| File too large | Keep uncompressed input at most 5 MiB and 50,000 data rows. |
| Missing/corrupt demo assets | Restore matching source assets; do not rerun evaluation to repair the demo. |
| Missing built-in cache | Use a valid CSV or restore the exact approved cache; no automatic acquisition. |

## Attribution and license

Original code/documentation retain the approved [MIT license](LICENSE).
TESS/MAST observations, derived arrays, dependencies and Plotly assets retain
their own rights and [notices](NOTICE); see [data/software attribution](docs/ATTRIBUTION.md).
The project license grants no new rights over those materials.

This is an author-directed student exploration with substantial AI assistance in
implementation, tests, documentation and model review. There is no runtime LLM.
[Contribution disclosure](CONTRIBUTIONS.md) distinguishes that assistance from
human validation, author understanding and institutional endorsement.
