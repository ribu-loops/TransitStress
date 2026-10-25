# TransitStress

Explore how processing choices and missing observations affect recovery of
artificial transit signals inserted into real TESS telescope measurements.

[Live demo](https://transit-stress.streamlit.app/) · [Quick start](#quick-start) · [Methods](docs/public/METHODS.md)

## What you can explore

- Follow three saved worked examples through Observation, Artificial signal,
  Compare, Interpret and Evidence, with light curves, trends and periodograms.
- Filter the 390 saved result rows and download available evidence. Cases without
  detailed arrays are clearly marked summary-only.
- Run a bounded exploratory CSV comparison locally, in a separate mode with an
  explicit Run action and Git-based provenance.

Saved demo browsing uses precomputed results: it does not accept uploads or run
new searches. Local CSV analysis is separate from the frozen evaluation.

In the guided charts, yellow represents artificially injected observations and
cyan shows the prepared background.

## How the experiment works

**Real observations → known artificial dimming → two processing windows →
compare recovered timing and retained depth.**

Artificial box-shaped dips are added to prepared TESS light curves. Two
rolling-median detrending configurations use windows of **0.5 days (A)** and
**1.5 days (B)**. Astropy's Box Least Squares (BLS) search runs without the known
injection settings; those settings are used only after the search to score
period-and-timing recovery and measure retained depth. Gaps are not interpolated.

The worked examples show why retaining more depth and selecting the intended
strongest periodogram peak can be different outcomes.

## Results and limitations

The historical frozen evaluation contains **135 correlated injection cases
across five evaluation stars**, with every case processed by both configurations.
These are paired cases, not 135 independent stars.

| Detrending configuration | Exact period-and-timing matches |
| --- | ---: |
| A: 0.5-day window | 79 / 135 |
| B: 1.5-day window | 74 / 135 |

All 135 cases were eligible, and scientific misses remain in the denominators.
The full 390-row bundle also includes real-background and Gaussian-null controls.
See the [saved rows](demo/t10/results.csv), [evaluation record](demo/t10/T07.md)
and [methods and limits](docs/public/METHODS.md).

Five stars, correlated repetitions, simple box signals, limited gap patterns
and upstream TESS processing constrain interpretation. This is not a survey
completeness estimate or evidence that one method is generally superior.
BLS peaks and depth SNR are diagnostics, not planet confirmation or calibrated
probabilities. Pi Mensae is a separate known-signal sanity check.

### Release status

Initial local release acceptance passed under amended criteria: accepted
technical and scoped visual evidence plus author-reported presentation and
browser-zoom checks. Additional independent live-browser observations remain
deferred; accessibility certification and author mastery are not established.

[Hosted Ubuntu 24.04 and Windows 2022 CI passed](https://github.com/ribu-loops/TransitStress/actions/runs/40217523662)
at workflow commit [`247b2e7`](https://github.com/ribu-loops/TransitStress/commit/247b2e7621b2ad8fdaac812425c7496e25e98639).
This is hosted check evidence, not a new scientific evaluation or external human
validation. Hosted availability and browser observations are author-reported;
independent hosted-browser and download verification remain outstanding.

## Quick start

Install Git and follow the [toolchain guidance](docs/public/TOOLCHAIN.md) for
**Python 3.12.15** and **uv 0.13.5**, including the repaired managed Python build.
Use uv for installation; the locked pip is not cleared for network access.
No API key, GPU or runtime AI is required.

In a Linux or Ubuntu WSL shell:

```bash
git clone https://github.com/ribu-loops/TransitStress.git
cd TransitStress
uv sync --locked --python 3.12.15
uv run --locked --offline --no-sync python -m transitstress doctor
TRANSITSTRESS_MODE=demo uv run --locked --offline --no-sync streamlit run app.py --server.address 127.0.0.1 --server.headless true
```

Open the local address printed by Streamlit; stop with Ctrl+C. Initial setup
normally downloads Python/dependencies unless compatible caches are complete.
After setup, saved-demo browsing needs neither network access nor the original
prepared-data cache.

The dashboard needs the source tree and bundled assets; the wheel alone contains
the library/CLI. A source archive can browse saved examples, but fresh CSV Run
requires a Git checkout with a real commit. Development built-ins additionally
need the original prepared cache, which is not distributed.

For CSV columns, limits and the separate local-mode launch command, read the
[usage guide](docs/public/USAGE.md). The included
[synthetic CSV sample](examples/synthetic_constant.csv) is not a TESS measurement.

## Methods, data and credits

- [Methods and scientific limitations](docs/public/METHODS.md)
- [Historical reproducibility and adverse outcomes](docs/public/REPRODUCIBILITY.md)
- [Distribution notes: pre-publication snapshot](docs/public/DISTRIBUTION.md)
- [TESS/MAST and software attribution](docs/ATTRIBUTION.md) · [Third-party notices](NOTICE)

Original implementation and documentation use the [MIT license](LICENSE).
TESS/MAST products, derived arrays and dependencies retain their separate rights.

OpenAI Codex assistants materially assisted planning, implementation, tests,
debugging, documentation and model reviews. Model review is not independent
human scientific validation or institutional endorsement. See the
[AI-assistance and contribution disclosure](CONTRIBUTIONS.md).

### Development history

Public history was privacy-filtered while preserving genuine development events
and original timestamps. Some retained commits have no public diff; earlier
revisions can retain bugs or require excluded private files and are not all
claimed runnable.

Scientific producing hashes remain historical provenance references; rewritten
public hashes do not claim to have produced those experiments. The original
freeze tag remains private and is not published or recreated.
