# Superseded working notes

**Nothing in this folder is authoritative. `../../README.md` is.**

These are the chronological notes from 2026-09-22..24, written while the
instrument was still being worked out. They are kept for one reason: they hold
the raw evidence and, more usefully, the record of what was tried and did not
work. Several of their conclusions were later **disproved** — by the time
`../../README.md` §9 was written, the register-address bug had been found and most
of the earlier reasoning had to be re-examined.

If a statement here disagrees with `../../README.md`, the parent file wins.

| file | what it is |
|---|---|
| `FINDINGS.md` | register-level findings, labelled B1/C3/etc. The labels are still referenced from a few module docstrings |
| `REPORT.md` | mode-by-mode test report; its own register findings point back at `FINDINGS.md` |

## What was wrong with them, specifically

Both were written *before* the discovery that two register families carried one
extra hex zero (`../REGISTER_ADDRESS_BUG.md`). Every experiment that swept a
timebase or energy register before that fix was writing 16× away from the
register it named, so:

* **"The rate is locked at 318 Hz and cannot be controlled."** False. The period
  register was simply never being written. Rate is now verified flat to −0.0%
  from 4 to 31 kHz.
* **"The energy argument has no effect on amplitude."** False, same cause.
  Amplitude is now linear in the energy register, `V = 3.414e-5 × reg + 0.0205`.
* **Digital RC "produces nothing"** — the conclusion happens to have survived
  re-testing, but the reasoning that produced it was invalid at the time, because
  the energy register it depended on was misaddressed. Only the post-fix re-run
  is worth quoting.

A related failure mode runs through both files and is worth remembering: several
"dead output" and "no pulse train" conclusions were **scope artifacts** — a 2 µs
window looking for a 318 Hz signal, or 50 mV/div clipping a 1 V pulse. The
instrument was working; the measurement was not. `../../experiments/INDEX.md`
flags which experiments are affected (that folder is gitignored — it is on the
lab machine, not in the repo).

## Still useful here

* the USB capture analysis and protocol framing evidence
* the mode-by-mode survey in `REPORT.md`, read alongside the corrections
* the dead ends themselves, so they are not walked into a second time
