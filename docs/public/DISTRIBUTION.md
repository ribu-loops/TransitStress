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

A clean snapshot does not make the existing branch safe to push. The proposed
current branch and historical G2 tag reach personal workstation paths, operational
records and session material. .gitignore cannot remove those historical blobs.
Git publication remains HELD. No existing refs/history/timestamps were changed.

Concrete proposal, requiring author disposition and independent review: publish
the inspected snapshot as a clearly labelled release snapshot with a new, honest
publication-time initial commit in a separate repository, explicitly disclose
that it is an export of the preserved private development history, and retain
the public outcome/provenance summaries. Do not fabricate development history or
claim this has already been approved. This is a proposed exception to the current
plan's authentic-history push sequence and needs an explicit plan disposition.
Alternative: publish the existing lineage only after explicit review/consent for
all identified personal/operational history; the snapshot scan alone is insufficient.
No history rewrite, force-push, ref deletion or publication is part of this work.
