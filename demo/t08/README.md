# T08 packaged development example

Read-only copy of one already computed T06 development injection, not a fixture
and not a T07 evaluation case. No science was rerun to produce this bundle.

- Development run: `204272966b38f832b0e5bb309302d5d303352da669455d6d15aba983b56afa2c`.
- Case: `6bd4e804ffa7f720c4056a695b0b5a5e5db445e75778af0eb220bcee886af09e`.
- Selection: first planned development injection with `central_block`, chosen
  for visible gaps before inspecting recovery, not selected for an outcome.
- Source: retained local T06 `2026-10-20T135428.616208+0000/development` case
  checkpoint and numeric artifact, matching `reports/t06_development/identity.json`.
- `case.npz` is a byte-identical copy; `case.json` retains both result rows,
  case settings, numeric schema/content/transport hashes and source hashes.
  Bounded frozen readers validate numeric schema and checksum before use.
- Prepared background: TIC 8195886, sector 1, SPOC PDCSAP, normalized and
  10-minute binned; absolute epoch and original units remain in row metadata.
- Synthetic injection: 1.73 days, 250 ppm, 3 hours, epoch 0.2941 relative days.
  A exact; B other peak. All 3,104 retained points are displayed.

This small bundle is for T08 visual approval. T10's curated release bundle and
full demo validation remain unimplemented. Full run directories stay ignored.
