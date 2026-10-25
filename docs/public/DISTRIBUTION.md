# Public-source inclusion and publication policy

This local candidate is prepared for review, not published or approved by G4.

The source snapshot and sdist include application source, app.py, native Streamlit
configuration, pinned build metadata/lock/Python selection, scientific configs,
public manifests, all required saved demo assets, examples, unit/integration/
security/AppTest fixtures, their owned scripts/scanner installer, the unchanged
offline CI workflow, licenses/notices, attribution and concise public documentation.
The t04 sanity report/arrays remain because existing security fixtures use them.
No numerical source, array, result, freeze or validator is edited.

The distribution excludes environments, tool binaries, caches, raw FITS, private
uploads/notes, session prompts, STATE/WORKLOG, reproduction clones/harnesses,
raw diagnostics, internal reviews/operational documents and unrelated screenshots.
Originals remain in the development repository/history. The allowlist in
pyproject.toml and exact candidate inventories, rather than copying the full
working folder, define the boundary. All tests needed for the shipped surfaces
remain; excluding internal harnesses does not claim their failures never happened.

demo/t10/T07.md is a labelled public documentation derivative: only one personal
workstation-path line is replaced by <project-root>, with an explicit banner.
Its transport record and bundle presentation identity differ. The original review,
bundle, scientific run identity and every numerical payload remain untouched.
The strict loader validates the derivative checksum; it is never presented as
the original evidence hash. The package force-include mapping is explicit and
rebuildable from the public source. [Provenance](derivatives.json).

The wheel contains library/CLI/UI Python modules and package metadata/licenses.
The source-root dashboard/assets are in the sdist; install the source for the
application. Dependencies are separately installed with their own notices;
original MIT terms do not relicense TESS data, derived arrays or dependencies.

This candidate contains privacy-filtered development history, preserving genuine
development events and their original timestamps. Commit hashes changed through
filtering. Some preserved historical events have no public file changes after
private material was removed; these remain genuine development events.

The original private development history and original scientific freeze tag
remain private. Historical scientific producing hashes remain provenance
references; the rewritten public commits do not claim to have produced those
experiments. The original freeze tag is not included or recreated.

This filtered-history candidate replaces the earlier one-commit release-snapshot
proposal. It does not propose publishing the original operational lineage or
original G2 tag. Earlier revisions can retain original bugs or require excluded
private files and are not all claimed runnable.

Preparation and scoped independent review are local. G4 and separate publication
authorization remain required; nothing has been pushed, hosted or deployed by
this preparation. Any later authorized transfer must contain only the reviewed
public branch, excluding private evidence, original refs/tags and raw .git storage.
