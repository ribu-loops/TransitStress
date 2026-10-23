# Public methods, measurements and limitations

Labelled concise derivative of docs/METHODS_RESULTS.md and the original protocol
and T07 evidence, retained in the development repository. Source document hashes
are in [derivatives.json](derivatives.json); this summary has its own bytes/hash.
It reports historical saved measurements, not a new evaluation.

The amended convenience cohort has two development and five evaluation stars:
sector-1 SPOC 120-second PDCSAP flux/error. Pi Mensae is a separate sanity check.
Selection used a fixed amended schedule and basic QC/cache knowledge, not a
random sample or complete sector census. The shipped data manifest retains
product identities and exclusion/selection evidence; [attribution](../ATTRIBUTION.md)
names all eight accepted products.

Finite quality-selected positive samples are sorted, shifted to relative days,
normalized by a background median and inverse-variance binned into occupied
10-minute bins. Empty bins remain absent. Epoch/time scale/original units remain
metadata. An additive box is injected after binning: f1=f0-depth*I, depth=ppm*1e-6;
errors are unchanged. Centered phase r=((t-t0+P/2) mod P)-P/2 has half-open transit
membership -D/2 <= r < D/2. There is no new noise on real backgrounds.

The three period/epoch-fraction families are 1.73/0.17, 3.41/0.43, 6.83/0.71;
duration is 3 hours, depths 250/750/2250 ppm. Masks retain sampling, remove
floor(0.20*N) samples without replacement, or remove the central 20% of elapsed
baseline. PCG64 seeds and masks are paired. Each blind pipeline estimates a
centered time-distance median (0.5 or 1.5 days), splits gaps above 0.5 days and
requires five window samples. Flux and error are divided by the positive trend.
No truth mask, expected period, downward clipping or gap interpolation guides it.

Astropy BLS uses independent periods 0.75–10 days, durations 1/2/3/4/6 hours,
likelihood objective, fast method, oversample 10 and frequency_factor 1.
Original-baseline grids are reused across masks; select the globally strongest
finite peak with deterministic tie handling. Truth only scores completed searches.

Eligibility needs two fully bounded events with three retained samples each and
six in-transit samples overall. Exact recovery needs <=1% period error and fitted
center error <=D/2 at every sufficiently observed true center. Half/double aliases
are separate diagnostics. With independently processed background z0 and injected
z1 on identical timestamps, q=z0-z1, retained depth=mean(q[in])-mean(q[out]);
divide by injected depth. At least three samples inside/outside are required;
missing measures remain null/reason, ratios are not clamped. This truth use is
post-processing, never guidance for detection.

All 195 paired cases / 390 rows are present: 270 injection, 30 real background,
90 Gaussian null. All 135 injection cases were eligible; zero technical errors.
Exact A/B: 79/135 and 74/135; paired outcomes: 72 both, 7 A only, 2 B only,
54 neither. Median retention A=0.8143923949903341, B=0.9731988430024502;
median paired B-A=0.14624711519232125. No background coincidence among exact
matches. Other peaks: 56 and 61. Half/double non-exact peaks: zero; 45 F3 double
aliases per pipeline lie outside the searched range.

| TIC | A exact / planned | B exact / planned |
| --- | ---: | ---: |
| 24195569 | 3/27 | 3/27 |
| 24231627 | 9/27 | 3/27 |
| 8196285 | 22/27 | 21/27 |
| 8196324 | 24/27 | 26/27 |
| 8196402 | 21/27 | 21/27 |

The selected saved TIC24231627 central-gap/2250-ppm/6.83-day disagreement shows
greater B retention with an incorrect strongest peak while A matches period and
timing. It was deterministically selected; it is not a typical-case claim or
proof of the physical cause of competing structure. See the complete
[rows](../../demo/t10/results.csv), [paired table](../../demo/t10/paired_summary.csv),
[groups](../../demo/t10/group_counts.csv) and [review derivative](../../demo/t10/T07.md).

Five stars, correlated repetitions, simple additive boxes after binning, one
duration, bundled phase/period families, one mask realization, upstream PDCSAP
and unpropagated trend uncertainty limit interpretation. Real backgrounds are
not verified negatives; Gaussian nulls omit correlated noise. Peaks/SNR are
diagnostics, not calibrated probabilities, planet confirmation, completeness
or a population superiority result. Scientific misses remain in denominators.
