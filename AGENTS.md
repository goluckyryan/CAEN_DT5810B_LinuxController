# Working on this repository

Read this first, then `README.md`.

This is a Linux replacement for the Windows control software of a CAEN /
Nuclear Instruments **DT5810B** detector emulator. It was built by
reverse-engineering the vendor software and checking every claim against an
oscilloscope. It drives real hardware on one bench.

---

## THIS REPOSITORY IS HALF OF A TWO-PART SYSTEM

The repo is deliberately **emulator control and nothing else**. Four things it
needs are *outside* it, on the lab machine, under `~/caen_signal_emulator/`:

| what | where | without it you cannot |
|---|---|---|
| `linux/firmware/dt5810usb.img` | 132 KB CAEN binary, not vendored | **power the emulator up at all** |
| `CAEN/.../ControlCenter/DDE3.dll` | the vendor DLL | check any register claim |
| `decompiled/DDE-Control.decompiled.cs` | its C# front end, decompiled | see what the vendor does, in what order |
| `pdu/pduOnOff.sh` | PDU control; holds credentials, so it stays out of the repo | switch the emulator on |

Two directories are gitignored and present only on the lab machine:

* **`scope/`** — read-only Rigol DHO4804 access, plus `plan.py`, `zoom.py`,
  `fitpulse.py`, `monitor.py`. All verification runs through here.
* **`experiments/`** — the 27 numbered experiments the findings rest on, with
  `INDEX.md` recording which are valid and which were confounded. `README.md`
  §9 cites them by number.

**If you are working from a clone without these, say so plainly rather than
guessing.** You can read and reason; you cannot bring up hardware, verify a
measurement, or check the vendor's behaviour.

---

## Rules

1. **Never `git commit` or push without explicit approval** for that specific
   commit. Do the work, stage it, report, and wait. An instruction to change
   files covers the filesystem, never the history.
2. **Check the Windows program before exploring on hardware.** Every advance
   here came from the decompilation; every blind-guess phase wasted hours. Work
   the C# first (*what*, and in *what order*), then the DLL (*which registers*).
   Setting a mode register is never the whole story on this board — three
   separate features needed a companion step only the vendor code revealed.
3. **Naming: `emulator` = DT5810B, `scope` = Rigol DHO4804.** Never "board" for
   either — README §0.
4. **The scope write whitelist is six commands** (`:TRIG:EDGE:LEV`,
   `:CHANn:SCAL`, `:TIM:MAIN:SCAL`). Everything else raises `PermissionError`.
   **Do not widen it without asking.** It has already caught real mistakes.
5. **Report measurements faithfully.** If something is inconclusive, say
   inconclusive — do not turn it into a design decision. That has happened here
   and produced wrong code.

---

## The verification loop — and how it keeps going wrong

The single recurring failure in this project is **trusting a computed number
over the actual samples**. It has produced three false "the emulator is dead"
reports. Every one was the scope, not the emulator.

Before concluding anything from a scope reading:

* **Check the trigger actually fired.** `:TRIG:STAT?` returning `AUTO` means it
  did not, and you are looking at random windows. A trigger level *below* the
  baseline can never fire on a rising edge — that caused the most recent false
  alarm.
* **Check for clipping from the ADC codes, not from volts.** `zoom.check()`
  counts samples on code 0/255. The ADC rail is **±4.34 divisions**, not the ±4
  the graticule suggests, and a volts-based test called a hard-railed trace
  "ok" while 499 samples were pinned.
* **Frame for the feature.** You cannot see the edge and the tail at once.
  Rise: s/div ≈ the rise time. Decay: s/div ≈ decay/2.5. A unipolar pulse on a
  centred origin gets only 4.34 divisions, so `V/div ≥ amplitude/4.34`.
* **Do not median over random captures.** At a few kHz in a 20 µs window the
  pulse occupies ~4 % of the time, so the median of untriggered captures is the
  baseline, and a healthy signal vanishes.
* **Take τ from a whole-trace fit** (`scope/fitpulse.py`), not a 1/e crossing.
  The peak is not the amplitude: `y_pk/A = (d/(d+r))·(r/(d+r))^(r/d)`.
* **Two channels at the same rate are phase-locked**, not independent — they
  divide the same clock. The one that is not the trigger source can sit
  permanently outside the window and read as dead. Detune it ~0.5 %.

When a reading looks like a dead emulator, the control experiment is the **run
gate**: toggle it and see whether the output changes. If it does not, *then*
suspect the hardware — after confirming the trigger fired and nothing is railed.

---

## Hardware bring-up

```bash
~/caen_signal_emulator/pdu/pduOnOff.sh on 3   # load 3 is the emulator
python3 fx3_firmware_loader.py                # 21e1:000d -> 000e
```

The FX3 firmware is **volatile** — this is needed after every cold power-up.
For a genuine power cycle, `off 3`, **wait 12 s**, `on 3`, and confirm it
returns at `000d`; that is the proof it actually lost power. The script's own
`cycle` sleeps 1 s, which is too short.

Power cycling is pre-authorised. Changing scope settings beyond the whitelist
is not.

---

## Known limits worth not rediscovering

* **Amplitude below 0.30 V produces nothing at all** — not a small pulse,
  nothing. `auto_gain()` raises rather than returning silently.
* **Decay** needs `request − 3.86 µs`; the error is additive, not a scale
  factor (§9l).
* **Config space is write-only.** Every config register reads back as
  `FF FF BA AB` filler. Verification has to happen on the analog output.
* **`board_id()` never returns the model word**, even on a healthy emulator.
  Do not use it as a liveness check.

---

## Where to read next

| file | for |
|---|---|
| `README.md` | the authoritative reference: register map, calibrations, §8 traps, §9a–9l findings |
| `docs/REGISTER_ADDRESS_BUG.md` | the extra-hex-zero bug that explains the project's history |
| `docs/letsClaw_finding.md` | scope geometry, framing rules, pulse fitting |
| `docs/superseded/` | earlier notes whose conclusions were **disproved**; kept as a record |
| `reference/README.md` | vendor traces captured from the Windows software, with their precision caveats |
| `README.md` §9k | what the vendor software does that we still do not |
