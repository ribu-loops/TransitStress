# Toolchain setup

Use official uv 0.13.5. Its CA-store repair supersedes vulnerable uv 0.13.1;
upgrading project certifi alone does not repair the uv binary.
Obtain the archive and matching SHA-256 sidecar from the
[official release](https://github.com/astral-sh/uv/releases/tag/0.13.5), over
verified system TLS. Verify SHA-256 before extracting or running uv.
Do not bootstrap using an insecure-host flag or disabled TLS.
Linux x86_64 GNU archive SHA-256:
`f68d291c75783122dad58ab77def0e13f3233f358731db5d6d7e8715603b928f`.
Other platforms must use their own official artifact checksum.

Python remains 3.12.15. New managed setups should use the official
[python-build-standalone 20261021 release](https://github.com/astral-sh/python-build-standalone/releases/tag/20261021)
selected by uv 0.13.5, with pip/ensurepip 26.2.2 and its repaired certifi 2026.10.19.
Existing environments do not automatically adopt that build. Preserve historical
environments; do not run their older bundled pip/ensurepip over the network as a
bootstrap workaround. The unchanged project lock installs pip 26.2.1, whose
vendored certifi 2026.06.17 still contains the affected root even in a new project
venv. Its network trust remains uncleared: use repaired uv for installation,
never that pip for network access. This is a separate tool limitation; Python
DEP-R1 remains closed for its independently reviewed distribution scope.
The local candidate verifies a separate same-patch build,
not a project Python or dependency upgrade. Official TLS release digests/checksums
are verified; independent Sigstore attestation verification is not claimed.

Run `uv sync --locked --python 3.12.15` in the source root. Reuse existing caches
with `--offline` when complete. Exact package versions come from uv.lock.
The source archive runs saved browsing/exports; private CSV Run additionally
requires a real Git checkout/commit, as the [usage guide](USAGE.md) explains.
No raw FITS or generic light-curve format support is implied.

The prepared CI matrix retains Ubuntu 24.04 and Windows 2022, read-only repository
permissions, no persisted checkout credentials and disabled dependency/Python
caching. Managed Python uses a fresh runner-temporary installation directory
and UV_MANAGED_PYTHON requirement so an older runner interpreter cannot satisfy the pin.
Checkout v5.1.0 and setup-uv v10.3.2 declare Node24 and are pinned by commit.
Setup uses explicit per-platform uv archive checksums and no activated environment.
Hosted offline CI passed on Ubuntu 24.04 and Windows 2022 at workflow commit
[247b2e7](https://github.com/ribu-loops/TransitStress/actions/runs/40217523662).
That result does not certify desktop/browser behaviour or a different toolchain.
