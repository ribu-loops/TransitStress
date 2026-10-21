# T10 packaged public evidence

Scientific outputs are copies of existing artifacts. `bundle.json` binds the
fixed inventory, source-relative provenance, sizes and SHA-256 digests, plus the
original evaluation completion and presentation packaging dates. This README is
explanatory and outside the executable member inventory.

- `results.json` / `results.csv`: all 390 original evaluation result rows.
- `plan.json`: the original 195-case plan, copied without running it.
- Aggregate/case/control tables and JSON: original frozen T07 analysis artifacts.
- `example1`–`example3`: byte-copied checkpoints, arrays and static plots for the
  unchanged T07 selection rules. Full case IDs/roles are in the manifest.
- `sanity.json` / `sanity.png`: separate T04 real Pi Mensae evidence; no injection,
  not one of the five evaluation stars. Original reference notes are historical
  pre-search metadata; recorded searches/comparison are complete.
- `identity.json`, `freeze.json`, `T07.md`: original provenance/freeze/review copies.

`uv run --locked python -m transitstress.ui.bundle` performs full offline validation.
Runtime never reads `runs/`, an acquisition cache or a remote archive. The bounded
public cache rechecks every member's bytes/hash before lookup, includes complete
content/validator identity, holds at most two entries in memory and returns copies.
Private workbench objects never enter it.

`python -m transitstress.ui.package_demo` is a one-time fixed-source adapter,
never a dashboard import. It refuses overwrite. Restore a verified checkout to
repair presentation; do not recompute or silently replace scientific evidence.
Original frozen HTML and its generic heading remain outside this bundle.
