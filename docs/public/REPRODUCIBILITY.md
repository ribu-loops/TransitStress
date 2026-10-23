# Public reproducibility and adverse-outcome summary

This labelled public derivative summarizes original operational/evidence documents
preserved locally. [derivatives.json](derivatives.json) records their hashes.
The summary does not replace original reports, invent new checks or use their
hashes as its own. Excluding operational logs does not erase their failures.

Historical evaluation producing commit:
2c897282f6db9e959a373896f1b3201859d83e70; run identity:
515a09b38ad7fd79a83d2cf7009af385660f768fcdcd83e0e0d985b09650c85a.
Its exact frozen identity and inputs remain in demo/t10/identity.json and
freeze.json. Current packaging/dependency/source identities are distinct.
Do not resume the historical run or label current source as its producer.

The original G1 failure and targeted repairs, conditional G3 followed by Linux
LNX-1 closure, stopped T11 reproduction attempts and source-identity corrections
remain part of the development record. A clean clone differed in raw line
endings from the original WSL working bytes; the first identity guard correctly
stopped before preparation. Later staging, replay and provenance guard problems
were separately reviewed and repaired. No stopped attempt is counted as a
successful numerical reproduction. Original thresholds were not widened.

V5 eventually compared one development source-data case against saved evidence:
925 fields, 301 scalars, 82 arrays, identical counts/settings/categories, numeric
rtol=1e-8 and atol=1e-10. Producing commit aac770e7eeb23875f7d861c231a7432fb808dd63;
executing identity 5101c4517228076d7f8d71e255f32b66a02509a8b502d1472e5f95fbbbc47ad2.
The independent outcome review is scoped PASS (original SHA-256
ffe12287961a012d98099921fcbee15cfa678c732937d41deccf180f78d86468).
This was assistant-performed self-reproduction using retained exact FITS,
not an external-human rerun, live reacquisition proof or full held-out evaluation.
V4/V5 are spent; no new reproduction or replacement freeze is authorized.

The dependency repair changed certifi to 2026.10.19, Pillow to 13.0.0 and
Streamlit to 1.66.1. The saved 2026-10-23 pinned audit found zero records across
120 locked third-party versions plus the build backend. Earlier 36 advisory
records were preserved and addressed, not suppressed. certifi advisory wording
uncertainty and attributed Pillow release-note/native-patch evidence remain
limitations; zero findings do not prove absence of every vulnerability.
DEP-R1 is independently CLOSED after 491 unit, 98 integration and 269 security
passes, zero skips/deselections, plus lint/Bandit/redacted current-file scan.
The original CONDITIONAL review remains unchanged; closure is a separate report.
Current packaging keeps every resolved package version and adds ordinary wheel
security floors; it does not authorize historical resume.

The dark/gold guided UI is retained. UI-1/original UI-2 are closed; UI-3 is closed
at caption-only scope. E-1 remaining interactions and true 200% browser zoom,
author visual acceptance, deferred/unassessed author study, current CI/action/tool
advisories and independent G4 remain open. Native Windows execution is unavailable,
not passed; hosted Ubuntu/Windows CI and public URL checks await separately
authorized publication. No published repository, PyPI release or hosted service
is claimed. These limits prevent treating a usable local candidate as a completed
public release.

Candidate-boundary verification exposed an existing requirement: fresh local
CSV computation records Git execution provenance. An extracted sdist with no .git
supports the saved demo/export, but not Run comparison. An archive security run
retained one failure at Git HEAD lookup (268 passed); this is not represented as
a green full suite. The same existing fixtures are checked in the development
Git checkout. No fake commit or relaxed provenance rule was introduced.
