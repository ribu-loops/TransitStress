# TransitStress

**How do processing choices and missing observations affect an artificial transit signal?**

TransitStress inserts artificial box-shaped dips into prepared TESS light curves,
then compares two rolling-median detrending configurations: A / 0.5 days and
B / 1.5 days. Astropy supplies the blind Box Least Squares search. The project's
contribution is paired orchestration, bounded validation, provenance and an
offline guided browser. Synthetic injections are experiments, not planet discoveries.

The historical frozen evaluation contains **five evaluation stars, 135 injection
cases, two detrending configurations and paired processing** (390 rows including
controls). Exact period-and-timing matches were **79/135 for A and 74/135 for B**;
median paired depth retention was 0.8144 and 0.9732. All 135 cases were eligible;
zero technical errors were observed in that evaluation. Scientific misses remain
in the denominators. Read [methods and limits](docs/public/METHODS.md),
[all saved rows](demo/t10/results.csv), and the labelled
[public copy of the independent results review](demo/t10/T07.md).

These correlated cases, simple boxes, convenience sample and limited gap patterns
do not establish survey completeness or a generally superior method. BLS peaks
and depth SNR are diagnostics, not confirmation or calibrated probabilities.
Pi Mensae is a separate real known-signal sanity check.

**Local public candidate; not published or G4-approved.** DEP-R1 is independently
closed. E-1 interactions/true 200% zoom, author visual acceptance and study,
CI/action/tool advisory clearance and independent G4 remain open. Linux/Ubuntu
WSL is the verified platform; native Windows and hosted CI are not passed.
[Reproducibility and adverse outcomes](docs/public/REPRODUCIBILITY.md) distinguish
historical evaluation, corrected source and the scoped one-case self-reproduction.

## Install and launch

Use Python **3.12** (tested 3.12.15) and uv (tested 0.13.1). Extract the source
distribution and open a terminal in its root. No API key, GPU or runtime AI is needed.

```sh
uv sync --locked --python 3.12.15
uv run --locked --offline --no-sync python -m transitstress doctor
TRANSITSTRESS_MODE=demo uv run --locked --offline --no-sync streamlit run app.py --server.address 127.0.0.1 --server.headless true
```

Open the loopback URL printed by Streamlit; stop with Ctrl+C. Installation normally
needs downloads unless Python and locked dependencies are cached; use
`uv sync --locked --offline --python 3.12.15` when cached. Saved demo browsing
needs no network or owner cache. The wheel is the library/CLI; the source archive
contains the dashboard, native configuration and required precomputed assets.
Ordinary wheel requirements enforce the reviewed security floors; exact
reproduction uses the lock. See [usage](docs/public/USAGE.md) and
[distribution boundaries](docs/public/DISTRIBUTION.md).

## Explore

1. From Start choose **Explore a worked example**.
2. Follow Observation → Artificial signal → Compare → Interpret → Evidence.
   White A and gold B share scales. The selected disagreement shows that retaining
   more depth and finding the intended strongest peak are different outcomes.
3. Download saved evidence or browse all 390 rows. Three evaluation cases have
   detailed arrays; others are explicitly summary-only. Navigation never runs science.

Local CSV computation is an advanced separate mode with explicit Run; see
[usage and input limits](docs/public/USAGE.md) and the
[synthetic sample](examples/synthetic_constant.csv). Built-ins need the original
prepared cache, which is not distributed. Saved demo startup does not need it.

## Rights and contribution

Original code/documentation retain the approved [MIT license](LICENSE).
TESS/MAST data, derived arrays and dependencies retain their separate rights:
[NOTICE](NOTICE) and [attribution](docs/ATTRIBUTION.md).
There was substantial AI assistance and model review; neither establishes human
validation, author understanding or institutional endorsement.
[Contribution disclosure](CONTRIBUTIONS.md).
