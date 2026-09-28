# Documentation

The entry point is **`../README.md`** — the consolidated, current reference for
the instrument. This folder holds the longer-form documents it points at.

| | |
|---|---|
| [`REGISTER_ADDRESS_BUG.md`](REGISTER_ADDRESS_BUG.md) | **Current and authoritative.** The extra-hex-zero discovery in full, with the disassembly evidence. `../README.md` §2 is the summary; this is the long form. |
| [`superseded/`](superseded/) | **Not authoritative.** Chronological working notes from 2026-09-22..24, written before the bug above was found. Kept for their evidence and for the record of what was tried and failed. `superseded/README.md` lists which conclusions were disproved and why. |

Two categories, deliberately separated: one document describes a bug in the
*instrument code* and is correct; the folder beside it contains documents that
were themselves *wrong*, and are kept as a record rather than a reference. They
should not sit at the same level, and neither should sit next to `../README.md`.

Related indexes elsewhere:

* `../experiments/INDEX.md` — all 26 numbered experiments, each marked valid,
  confounded, or superseded. Not in the repo: `experiments/` is gitignored and
  lives on the lab machine only
* `../reference/README.md` — the vendor traces captured from the Windows
  software, and the precision caveats that apply to them
