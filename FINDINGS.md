> **⚠️ WORKING NOTES — NOT THE CURRENT STATE.** Read `README.md` first; it is the
> consolidated, corrected reference. This file is kept for its evidence and dead
> ends. It was written by appending, so its sections are out of chronological
> order (C2, C5, C4, C3, C) and several conclusions below were later DISPROVED:
>
> * **B6** ("the 318 Hz lock is the library hardcoding the period") — wrong; it
>   was the register-address bug, see `REGISTER_ADDRESS_BUG.md`.
> * **A1** (`TransistorReset` = pulsed reset as "the headline" fix) — a real bug,
>   but not the blocker; fixing it does not make Digital RC work.
> * **C2/C3** conclusions about Digital RC — the tests were confounded by the
>   misaddressed energy register. The conclusion happened to survive a later,
>   properly controlled test, but the reasoning here does not.
> * **C5** "AWG bypasses the analog stage" is correct; the claim elsewhere that
>   ClockPerStep is unreliable is not.
>
> See `README.md` §9 for the full list of corrections.

# DT5810B — Fresh Start, 2026-09-22

Ground truth for this attempt is **the manual** (`UM5312 rev 5`, MinerU markdown),
not the earlier reverse-engineering notes. Where they disagree, the manual wins
unless hardware says otherwise.

Everything below is either quoted from the manual or measured on the board today.

---

## A. Where the manual contradicts the existing notes

### A1. `TransistorReset` is PULSED RESET — it is the ramp. ⭐ headline

Manual §12 *"Transistor reset"* block (md line 1827-1845):

> **Pre-amplifier mode**: select between Continuous reset and pulsed reset amplifier.
> **Amplitude scaling**: … Allowed values are 2,4,8,16,32,64,128
> **Ramp amplitude**: corresponds to the maximum - minimum value of the staircase.
> The output signal is the integrated amplitude of each input pulses. When the
> "Maximum Value" is reached, the output is instantaneously reset to the "Minimum Value".

Manual §10 *"Pulsed Reset"* (line 870-872):

> This block is an **integrator** that sums the pulses up to a fixed threshold.
> Then it resets it to the programmed minimum value.

`KNOWLEDGE_BASE.md` §3.2 says `TransistorReset` ⇒ "Shape generator (memory-based /
FAST / Custom) is the source". **That is wrong.** TR selects the pulsed-reset
integrator, which is a *third* datapath alongside custom-shape and Digital RC
(manual §10 *Shape Datapath*: "three types of shape generation: custom shape,
digital RC, and pulsed reset").

**Consequence:** `0x300010 = 1` explicitly switches the channel into the
staircase integrator. The "triangle / linear ramp / sawtooth" that has been the
headline blocker since June was never a *"DRC→DAC routing bug"* — it is the
pulsed-reset datapath doing exactly what it is documented to do, because the code
asked for it.

Corroboration: registers `0x300012` / `0x300013` map cleanly onto the manual's
"Ramp amplitude" and "Amplitude scaling" (allowed values 2…128 = `1<<(scale+1)`,
which is precisely the formula in `ConfigureTR`).

### A2. The "4 µs max shape length" limit is wrong — it ignores the interpolator

Manual §12 *Shape Interpolator* (line 1815-1817):

> Since the system clock is equal to 1GHz … and the compliant signal shapes are at
> maximum 4096 samples long, the maximum time duration of a signal is 4.09 us.
> **In order to get signals up to 26 ms of length**, the real signal can be ideally
> divided in two parts: the "rising" and "falling" regions divided by a "Corner" point.

Spec table §2: *"Shape length from 3 ns to 4 μs (w/o interpolation) / 4 ms (interp.)"*

`KNOWLEDGE_BASE.md` §10.2 claims Fast mode is capped at "~4 µs decay", and §10.3
concludes AWG is required for a 50 µs decay. **Both are unfounded.** 50 µs is
comfortably within range for *both* Fast and Digital RC.

The manual's own worked example (§11 *Practical Use* / OneTouch, line 1211) is
literally **Rise Time 0.01 µs, Fall Time 50.00 µs** on the Digital RC path — i.e.
the exact target signal, on the path that was assumed to be broken.

### A3. Clock: the manual is mostly written for the 1 GHz part

The manual says both "1.25 GS/s" (§10 overview, spec table) and "system clock is
equal to 1GHz, i.e. 1 ns of sampling period" (§12), and Fig 10.1 is captioned
"to achieve 1 GSPS emulation speed" while the text describes 4×250 MHz.

The decomp resolves it: `instrver 0x10005810` = release A = 1.0 GHz;
`0x1005810B` = release B = 1.25 GHz. **Any formula lifted verbatim from the manual
needs the clock substituted.**

Sanity check of the existing coefficient math — `drc_coeffs.compute_drc(fall=50000 ns,
clock_quarter=312.5 MHz)` gives `a = 0.99996741`, so `1/(1-a) = 30680` samples;
at 625 MS/s (half of 1.25 GHz) that is **49.1 µs** against a requested 50 µs.
Self-consistent. **The DRC coefficient computation appears correct** — consistent
with the June finding that it was verified line-by-line against `asm5340.txt`.

### A4. Smaller corrections

| Item | Notes said | Manual says |
|---|---|---|
| DRC min time constant | 16 ns | "do not use below **20 ns**" (§10 line 852); "shorter than 16 ns" (§12 line 1658) — manual disagrees with itself; use 20 ns |
| Constant rate range | 5 Mcps | 10⁻² → 5 Mcps (§12 Timebase); 11 Mcps (§12 Energy); 30 Mcps (spec table) — three different figures in one document |
| Pile-up limit | 16 | 1…15 selectable for Fast/custom; **Digital RC has no pile-up limit** (§12 Pile-up) |
| Auto shape threshold | τfall < 100 ns → FAST | confirmed (§12 line 1644) |

---

## B. Bugs proven in the existing code today

### B1. `rd()` uses the wrong address and count — reads are off by one word

`linux/dt5810.py:186`
```python
pkt = ... + struct.pack('<I',(addr-1)&0xFFFFFFFF) + struct.pack('<I',count+2)
```

Both fields are wrong.

* **Address must be verbatim.** `linux_port/core/dt5810_usb.cpp:155` says so in a
  comment ("DLL uses addr verbatim for single-word reads; param-1 only applies to
  streaming reads") and `CONNECT_SEQUENCE.md` says so in bold. Measured: reading
  `0xFFFF0000` verbatim → `00000001 ffffffff …`; with `addr-1` → the same values
  shifted one word later. Off-by-one confirmed on hardware.
* **Count field is N exactly**, not `count+2`. Measured: field=1 → 4 bytes returned,
  field=3 → 12 bytes. (`PROTOCOL.md` claims `N-1`, `CONNECT_SEQUENCE.md` claims
  `count+2`, the C++ uses `n`. The hardware says `n`.)

The "reads are flaky, retry 4×" logic in `board_id()` is a symptom of this, not a
property of the device. With correct framing reads are perfectly repeatable.

Also: `0xFFABBAFF` and `0xFFFFABBA` are **not two different markers** — they are
byte-rotations of the same repeating `FF FF BA AB` filler. Which one you parse
depends on alignment. The docs treat them as distinct; they aren't.

### B2. `set_drc_pulse()` selects the pulsed-reset integrator

`dt5810.py:331` → `self.wr(_reg(ch,0x00300010), 1)   # TR-enable`

Per A1 this is the staircase integrator, not Digital RC. `DRC_SESSION_CHECKPOINT.md`
records this being diagnosed and fixed on 2026-06-30 — but the fix landed only in the
standalone scripts. Current state across the three copies of this logic:

| File | `0x300010` (TR) | `0x30000b` (enable) | |
|---|---|---|---|
| `drc_pulse_decay.py` | `0` ✅ | `0xFFFFFFFF` ✅ | both fixed |
| `drc_pulse.py` | `0` ✅ | `1` ❌ | half fixed |
| **`linux/dt5810.py`** | **`1`** ❌ | **`1`** ❌ | **neither fixed** |

The library is the *only* copy still carrying the original bug — and it is the one
`dt5810_gui.py` and `dt5810_mcp.py` both import. So the standalone scripts and the
library have been diverging, and anything driven through the GUI or the MCP server
has been running the pulsed-reset integrator this whole time.

### B3. DRC enable written as `1`, should be `-1`

`dt5810.py:337` → `0x30000b = 1`. The C# `IENABLE` is `-1` (0xFFFFFFFF). Same story
as B2: fixed in the standalone, not in the library.

### B4. Pulsed-reset registers written in the DRC path

`dt5810.py:340-341` writes `0x300012` / `0x300013`. Per `FUN_10005960` these are
written only when TR≠0, and per the manual they are pulsed-reset parameters. They
have no business in a Digital RC configuration.

### B5. The `energy` argument never reaches the amplitude register

`dt5810.py:319`
```python
self.wr(_reg(ch,0x020f0005), 4200*2)   # pulse rate (~318 Hz, working default)
```

`0x020f0005` is the **ENERGY** register (value × 2) — the project's own
`KNOWLEDGE_BASE.md` glossary says so explicitly and flags the rate reading as "our
earlier error". The comment preserves the error. Meanwhile the caller's `energy`
argument is written to `0x300012`, the pulsed-reset ramp amplitude, which is inert
once TR=0.

**So with B2 fixed, amplitude would still be stuck at 4200** regardless of what the
caller asks for. This is almost certainly the "narrow spikes, amplitude tiny"
result recorded in the checkpoint after they set TR=0.

### B6. The `rate_hz` argument is ignored — and the 318 Hz "lock" is self-inflicted

`dt5810.py:323-324`
```python
self.wr(_reg(ch,0x0100000a), 0)
self.wr(_reg(ch,0x01000009), int(CQ/100 - 1))    # = 3,124,999, hardcoded
```

`rate_hz` is accepted by the signature and never used. The comment claims this value
"MUST stay … or the IIR turns into an integrator (triangle output)".

That causal claim looks like a misattribution: at the time it was written the code
was also in TR=1, where the event rate *directly* sets the staircase ramp slope — so
changing the rate changed the ramp, which looked like "the IIR broke".

Arithmetic: 3,125,000 ticks at ~1 GHz = 3.125 ms = **320 Hz**, against the long-observed
"locked ~318 Hz". **The rate is not locked by firmware. The library hardcodes it.**
This is the single most testable claim in this document.

### B7. `invert` argument ignored

`dt5810.py:316` writes `0x0f000004 = 1` unconditionally; the comment concedes it is
legacy.

---

## C2. Hardware results (supersedes C below)

**The FPGA is alive and our writes land.** Control experiment `t09`:

| | Vpp | Vmin | Vmax |
|---|---|---|---|
| run gate `0x01c00006` = 1 | 1.011 V | 1.822 | 2.833 |
| run gate `0x01c00006` = 0 | 0.087 V | 1.822 | 1.909 |

Toggling one register collapses the output and restores it. So configuration
reaches the FPGA, and the ~1 V signal on CH1 is genuinely ours.

Corollaries:

* **`board_id()` is a red herring.** It never returns `0x1005810B`, but the FPGA
  is demonstrably working. The identity read is simply broken (see B1); it is not
  a liveness indicator and should not be used as one.
* **Config registers are write-only.** Confirmed across the whole space. All
  verification has to be done on the analog output.
* The earlier "no pulse train" reading in this session was an artifact of the
  scope being at 2 µs/div and 50 mV/div — a ~1 V pulse was simply off-screen.
  Scalar `VPP` from a badly-scaled scope is not evidence of absence.

### Still wrong, with TR=0 applied

Corrected Digital RC (`t07` case C / `t08`) produces a ~1 V pulse, but the shape
is not right yet. Captured trace at 20 ns/sample, τ requested = 1 µs:

```
n=1000  span=0.9898 V   peak at sample 507 (trigger centre)
tail never falls to 1/e within the remaining 10 µs
```

At τ = 1 µs the tail should reach 1/e about 50 samples after the peak. It doesn't
decay measurably in 500 samples. So setting `0x300010 = 0` is necessary but **not
sufficient** — the decay is far slower than programmed.

### B6 (rate control) is UNPROVEN, not confirmed

Sweeping `0x01000009` over 2500 / 5000 / 10000 / 20000 left Vpp, Vmin and Vmax
identical to three decimals. But `FREQ` was never measurable, because a 20 µs
window cannot contain one period at these rates — so this neither confirms nor
refutes the hypothesis. A rate change with constant amplitude would look exactly
like this. **Do not record B6 as settled either way.** It needs a window wide
enough to measure period, or a Vavg/duty-cycle measurement.

### What is needed to finish

A scope window that can hold at least one full pulse period. Everything else is
in place. Current: 2 µs/div (20 µs). Useful: ~20 µs/div for 10 kHz-ish rates, or
~500 µs/div to see the native ~318 Hz. Vertical ~1 V/div at 1 MΩ is correct.

Operational note: the scope drops the TCP session if queried rapidly; use one
persistent socket, ≥1 s between measurement bursts, and retry on timeout.

---

## C5. The 318 Hz lock IS the generating mode (Ryan, 2026-09-23) ✅

Ryan was right. `ChannelMode` (`0xFA00100A`) selects Pulser vs AWG, and the
318 Hz belongs to the **Pulser** datapath. `linux/README.md` already said so —
"detector (triggered, ~318 Hz fixed) vs AWG (continuous, any rate)" — and this
session wasted a lot of effort hunting for a Pulser rate register that does not
exist, when the mode switch was the answer.

In AWG mode the rate is not a timebase register at all:

    rate = 312.5 MHz / (DataLen x ClockPerStep)

**Verified on hardware:** DataLen=1008, CPS=31 -> 10.0 kHz predicted,
**10.16 kHz measured**. DataLen=10080, CPS=31 -> 1000.06 Hz.

### AWG mode bypasses the whole analog-stage control set

Measured, all with no effect in AWG mode:

| register | | result |
|---|---|---|
| `0x0f000001` | digital gain | no effect (1107 -> 6 unchanged) |
| `0x0f000000` | digital offset | no effect (0 -> −20000 unchanged) |
| `0x0f000004` | invert | no effect (0 and 1 give an identical trace) |

So in AWG mode **amplitude, polarity and baseline must all be baked into the
sample array**. Measured transfer at CPS=31 into 1 MΩ:

    output_volts = -1.296e-4 * array_code - 0.12 V

The output is *inverted* with respect to the array (a positive array gives a
negative-going pulse), and a zero array sits at −0.12 V.

Amplitude via `peak_lsb` is clean and linear: 3000 -> 0.41 V, 7000 -> 0.887 V,
7065 -> 0.990 V.

**Baseline is NOT adjustable in AWG mode.** It stays at −0.1365 V whatever DC is
added to the array (swept −926 to −4085, no change). Either the path is
AC-coupled or the DC term is stripped. Unresolved.

### Two paths, three specs each

| | Pulser / shape-RAM | AWG |
|---|---|---|
| rate | ❌ 318 Hz fixed | ✅ 1 kHz (formula verified) |
| amplitude | ✅ 0.97 V | ✅ 0.99 V |
| rise 1 µs | ⚠️ bracketed 0.5/1.5 µs | ✅ 1.00 µs |
| decay 100 µs | ✅ 98 ± 3 µs | ⚠️ 58-98 µs, unstable |
| baseline 0 V | ✅ 0.01 V | ❌ −0.14 V, not adjustable |

Scripts: `deliver_pulse.py` (Pulser), `deliver_pulse_awg.py` (AWG).

### Why the AWG decay measurement is unstable

A 1 kHz period is 1000 µs and the scope window is 1000 µs, so the trace holds
exactly one period. The tail never has room to settle before the capture wraps,
so the baseline estimate (trace minimum) is biased and the tau fit swings between
58 and 98 µs on an unchanged configuration. `FREQ` is unmeasurable for the same
reason and occasionally returns a spurious 625 kHz.

**Fix: widen to ~200-500 µs/div (2-5 ms window).** Then one full period plus
several tail time-constants are visible, tau and rate both become directly
measurable, and the AWG calibration can be finished properly. That is the one
thing still blocking a clean 4-for-4.

---

## C4. Requested pulse — delivered state, Pulser path (2026-09-23)

Target: 1 kHz, 1 V, 1 µs rise, 100 µs decay. Reproduce with `deliver_pulse.py`.

| spec | target | achieved | |
|---|---|---|---|
| amplitude | 1 V | **0.97 ± 0.02 V** | ✅ |
| decay τ | 100 µs | **98 ± 3 µs** | ✅ |
| baseline | 0 V | **0.01 V** | ✅ |
| rise 10-90% | 1 µs | 0.5 µs (corner=4) / 1.5 µs (corner=3) | ⚠️ bracketed, see below |
| rate | 1 kHz | ~318 Hz, fixed | ❌ not controllable |

Working settings: `width_us=600, decay_us=102, corner=4, gain=1107,
offset=-55934, invert=1`, on the **shape-RAM path** (`set_detector_pulse` +
corner override). Run `deliver_pulse.py`.

### Zeroing the baseline

The pulse originally sat on a 1.81 V pedestal. Digital offset `0x0f000000` is
linear at **3.234e-5 V/count** (0 → 1.809 V, −20000 → 1.174 V, −55465 → 0.026 V),
so **−55934** puts the baseline at 0 V. Verified not clipping: amplitude and tau
are unchanged after the shift, and the pre-trigger baseline ripple is 0.068 V =
2 LSB of scope quantisation, not a rail.

Caveat: the manual (sec 12) says the offset is applied *before* the gain stage, so
this count is only valid at `gain=1107`. Change the gain and recalibrate with
`t16_zero_offset.py`.

### Measurement precision

At 1 V/div the scope quantises to 34 mV, which is 3.4% of a 1 V pulse — that is
the dominant uncertainty, and why repeat readings of an unchanged configuration
alternate between 0.956 and 0.990 V (exactly one LSB apart). Tau repeats within
±3%. Do not chase these numbers with an auto-calibration loop; an earlier version
of `deliver_pulse.py` did and talked itself down to 95 µs by "correcting" a noisy
reading. The constants are pinned instead.

### The rise is bracketed, not resolved

At the scope's 50 µs/div the trace samples at 500 ns, so the 10-90% rise can only
be reported in 500 ns steps. corner=3 gives 3 samples (1.5 µs), corner=4 gives 1
sample (≤0.5 µs) — 1 µs falls in the gap and cannot be distinguished here.
**To set it properly, measure the rise at ~1 µs/div** (the decay is already
established and does not need re-measuring at the same time).

### New knob: the interpolator corner point `0x50f005`

The vendor code leaves it at 0, which collapses the fine rise region and pins the
rise at ~1.5 µs. Raising it shortens the rise but steals samples from the tail, so
`decay_us` must be compensated (at corner≥100 the tail collapses: τ falls to
18 µs at corner=100, 1.9 µs at corner=400). corner 4-6 is the usable window here.

### Rate: not solved

Swept with no effect on the pulse rate: `0x01000009` (period) 10k/25k/50k,
`0x01000006` (alpha) 1e3…1e7, `0x0100000a` TimeMode 0 and 1, the `0x01000004`
LFSR strobe after the write, deadtime `0x01000007/8` = 0, and TimebaseMux
`0x0f000005` = 0 (mux=4 is external trigger and yields only noise). One pulse per
500 µs window in every case.

My hypothesis B6 — that the "locked 318 Hz" was just the library hardcoding the
period — is **refuted**. The original note in `KNOWLEDGE_BASE.md` §9 was right.
Rate control remains the open problem, and is now the main thing between this
setup and a fully specified pulse.

---

## C3. Day 2 (2026-09-23) — cold boot results

The board lost power overnight (PDU load 3 found OFF). Powered back on, FX3
firmware reloaded, clean cold FPGA state — shape RAM never written.

### Digital RC produces nothing (t12) — solid result

On a virgin board, with TR=0, enable=`0xFFFFFFFF`, correct coefficients and no
stale state:

| requested tau | amplitude | scope trigger |
|---|---|---|
| 1 µs | 0.102 V | AUTO (never triggered) |
| 2 µs | 0.102 V | AUTO |
| 4 µs | 0.102 V | AUTO |

Run gate ON 0.102 V vs OFF 0.085 V — both the noise floor. **Digital RC does not
drive the DAC.**

**This corrects the headline claim in section A1/B2.** The manual evidence that
`TransistorReset` = pulsed reset is still correct, and `linux/dt5810.py` does
still set TR=1 wrongly. But fixing that does *not* make DRC work. It was a real
bug, not the blocker. The long-standing open item stands.

It also retrospectively explains yesterday: the ~1 V "DRC" signal in t07-t10 was
the shape generators, loaded earlier by `set_detector_pulse`, which is why the
measured tau sat at ~53 µs regardless of the requested DRC tau (the shape RAM
held `decay_us=50`), and why blanking the shape generators in t11 killed it.

### What is confirmed working

* Writes reach the FPGA and the analog stage responds. The offset register moves
  the baseline exactly as predicted: `offset` = −55465 / −20000 / 0 gives
  Vmin = 0.026 / 1.174 / 1.820 V. (−55465 ≈ −1.87 V pushes the natural ~1.82 V
  baseline to ~0 and clips it — worth knowing, the vendor default is misleading.)
* Run gate `0x01c00006` gates the output.
* `0x50f000` gates the shape generator: driving it to 0 on all 16 sids kills the
  output. Do not write it to 0 after programming (the vendor code leaves it at 1).

### What is NOT working, and is now the blocker

**No pulses from either datapath today.** Cold board, shape RAM programmed via
the known-good `set_detector_pulse`, offset corrected to 0, rate register set,
run gate on, `0x300012`/`0x300013` tried at several values with TR=0 and TR=1 —
Vpp stays at ~0.09 V on a clean 1.82 V baseline, scope never triggers.

Yesterday the same board produced ~1 V pulses. That is not reproducible today and
I could not isolate the difference.

### Why I could not get further

Every measurement today was made through a 20 µs window at 2 µs/div with AUTO
trigger, on a signal whose repetition rate I have no independent way to verify
(the rate register is unconfirmed, and the live-stats registers `0x3f002-5` are
not readable — tried with the corrected framing). Under those conditions
"Vpp ≈ 0.09 V" cannot distinguish between *no pulse*, *a pulse outside the
window*, and *a pulse the scope isn't triggering on*. That ambiguity has cost
most of this session.

**What is needed:** a scope window that can hold at least one full pulse period —
roughly 200 µs/div to see a 1 kHz train, with ~500 mV/div at 1 MΩ — and a trigger
that holds. Or simply Ryan's eyes on the screen while I sweep settings.

---

## C. (earlier, superseded by C2/C3) Hardware state

What works: PDU power-on, FX3 firmware load (`000d` → `000e`), enumeration as
`21e1:000e Nuclear Instruments DT5810`, register writes accepted without error.

What doesn't:

* `board_id()` never returns the documented `0x1005810B`. Only `0xFFFF0000` responds
  (`00000001 ffffffff`); the whole FPGA config space returns filler.
* Config registers look genuinely **write-only**, so readback cannot confirm anything.
  All previous "verification" in this project was scope-based.
* Scope (read-only, 192.168.2.200): CH1 shows a DC level ~0.15 V that *did* move in
  response to register writes, but **no pulse train** — no 318 Hz, no ~1.1 V pulses,
  running the one path (`set_detector_pulse`) the notes say definitely worked.

Confounds I could not clear on my own:

1. The scope timebase is **2 µs/div** — far too fast to display a 318 Hz train.
   A 300 µs pulse at 3 ms spacing would be essentially invisible.
2. I don't know which scope channel the emulator is patched into, or whether it's
   the FAST or the HDR output.
3. Standing instruction is that I do not change scope settings.

So I cannot currently distinguish "FPGA not configured" from "signal present but
off-screen / not connected to the channel I'm reading".

**Questions for Ryan:** which scope channel and which output (FAST vs HDR) is the
emulator patched into, and may I set the timebase to ~1 ms/div for verification —
or would you rather drive the scope yourself?

---

## D. Suggested order of work once C is cleared

1. Fix `rd()` framing (verbatim addr, count = N). Cheap, unblocks all diagnostics.
2. Digital RC path, built from the manual rather than patched from the old code:
   `0x300010=0`, `0x30000b=0xFFFFFFFF`, coefficients from `drc_coeffs` (which checks
   out), amplitude via `0x020f0005 = energy*2`, no `0x300012/13`.
3. Test the rate hypothesis: sweep `0x01000009` and measure. If B6 is right, rate
   becomes controllable and the oldest open item in the project closes.
4. Only then revisit AWG — and probably don't, since A2 removes the reason for it.

## E. Files

* `t01_probe_existing.py` — runs existing bringup + set_drc_pulse, dumps registers
* `t02_read_matrix.py` — read-framing matrix (addr bias × count field)
* `t03_fpga_alive.py` — identity registers before/after bringup
* `t04_id_scan.py` — address-space scan for the model word
* `t05_output_check.py` — drives the board, reads scope measurements (read-only)
