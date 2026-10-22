# Data and software attribution

This work uses TESS mission observations obtained through MAST at STScI.
NASA's Explorer Program funds the mission's US institutional participation;
STScI is operated by AURA under NASA contract NAS5-26555.
This acknowledges the data infrastructure and does not claim project funding,
mission membership, sponsorship or endorsement. Checked against the official
[mission acknowledgements](https://archive.stsci.edu/publishing/mission-acknowledgements)
and [data-use policy](https://archive.stsci.edu/publishing/data-use) on 22 October 2026.
MAST's rights/attribution rules govern its products; the project's MIT license
does not relicense telescope data.

## Accepted source products

Every accepted product is sector 1, SPOC extracted LC, 120 seconds,
PDCSAP_FLUX / PDCSAP_FLUX_ERR, source processing spoc-5.0.10-20200904 / DR42.
These are not raw pixels. The manifest records archive/source version-token
reconciliation, source hashes, persistent mast:TESS/product URIs, source epoch/
units, QC and prepared-content hashes. Seven benchmark stars form an amended
convenience cohort; Pi Mensae is a separate sanity check. Historical rejected
inputs and spent reservations remain in the acquisition evidence.

| TIC | Role | Exact product filename |
| --- | --- | --- |
| 261136679 | sanity | tess2018206045859-s0001-0000000261136679-0120-s_lc.fits |
| 8195886 | development | tess2018206045859-s0001-0000000008195886-0120-s_lc.fits |
| 8196058 | development | tess2018206045859-s0001-0000000008196058-0120-s_lc.fits |
| 8196285 | evaluation | tess2018206045859-s0001-0000000008196285-0120-s_lc.fits |
| 8196324 | evaluation | tess2018206045859-s0001-0000000008196324-0120-s_lc.fits |
| 8196402 | evaluation | tess2018206045859-s0001-0000000008196402-0120-s_lc.fits |
| 24195569 | evaluation | tess2018206045859-s0001-0000000024195569-0120-s_lc.fits |
| 24231627 | evaluation | tess2018206045859-s0001-0000000024231627-0120-s_lc.fits |

The complete persistent identities and SHA-256 values are in
[data/manifest.json](../data/manifest.json); no original FITS is bundled in Git.
Original/prepared cache bytes stay ignored. Small prepared demo arrays and plots
are derived data, retain this attribution and do not become original MIT code.

## Methods and library credit

- Kovács, Zucker & Mazeh (2002), BLS:
  https://doi.org/10.1051/0004-6361:20020802. TransitStress uses Astropy's
  implementation; it does not implement or invent that algorithm.
- Astropy BoxLeastSquares documentation:
  https://docs.astropy.org/en/stable/timeseries/bls.html.
- Lightkurve extracted-product I/O:
  https://lightkurve.github.io/lightkurve/reference/api/lightkurve.io.read.html.
  Its Pi Mensae tutorial informed the sanity-example choice; the project uses
  extracted light curves, not the tutorial's pixel extraction.
- Gandolfi et al. (2018), version 2, Table 5:
  https://arxiv.org/abs/1809.07573. The stored reference is 6.26834 days and
  2458325.503055 BJD_TDB; truth is for post-search comparison only.
- TESS/SPOC processing and PDCSAP are upstream mission work, not this project's
  contribution. [Primary source catalog](SOURCES.md) records further references.

| Locked direct runtime package | Version | Upstream license / notice |
| --- | --- | --- |
| NumPy | 2.5.4 | BSD-3-Clause and bundled component licenses |
| pandas | 2.3.3 | BSD-3-Clause; preserve distribution notices |
| Astropy | 8.0.2 | BSD-3-Clause and bundled component notices |
| Lightkurve | 2.6.0 | MIT |
| Plotly.py | 7.1.0 | MIT; includes Plotly.js 4.1.1 |
| Streamlit | 1.66.0 | Apache-2.0 and distribution component notices |

Versions and license expressions/classifiers were inspected in the installed
locked distributions. This is a direct-package inventory, not a claim that all
transitive dependency obligations have been independently audited.
Distributions retain their own full licenses/notices; installing dependencies
does not convert them to the project's MIT license. NumPy/Astropy include
additional components whose licenses must remain with redistributed binaries.

The T11 source inspection found no attributed copied/adapted tutorial block in
owned src/scripts/tests; functions calling upstream APIs are original project
orchestration. This limited inspection is not proof about every historical
snippet. A future discovered adaptation needs exact source/license attribution
before release. Keep this as a G4 review question.

## License boundaries

[LICENSE](../LICENSE) applies to original project implementation/documentation.
[NOTICE](../NOTICE) separately credits dependencies/data and includes MIT notice
text for the bundled Plotly code. Generated self-contained HTML includes the
upstream Plotly.js bundle with its embedded notices; those notices are preserved.
Historical HTML/artifacts are not rewritten during T11.

The Python wheel is the scientific CLI/library. The source app, curated arrays
and reports are distributed through the source checkout, with these notices.
See [CONTRIBUTIONS](../CONTRIBUTIONS.md) for the material AI-assistance disclosure
and the distinction between automated/model review and human validation.
