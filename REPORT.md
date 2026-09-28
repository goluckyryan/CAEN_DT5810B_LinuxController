> **⚠️ WORKING NOTES — NOT THE CURRENT STATE.** Read `README.md` first. This file
> was appended to over three days; later sections correct earlier ones. In
> particular §1's verdict is superseded (it predates the register-address
> discovery), and the "rate is not controllable" conclusion in §2.3 is wrong —
> the rate register was simply being written to the wrong address.
>
> Sections 8-10 are current. See `README.md` §9 for the full correction list.

# DT5810B — Mode Survey and Target-Spec Report

**Target:** 1 kHz, 1 V, 100 ns rise, 50 µs decay, 0 V baseline
**Date:** 2026-09-23 · board `21e1:000e`, CH1 into Rigol DHO4804 @ 1 MΩ

---

## 1. Verdict up front

**SUPERSEDED 2026-09-23 — see REGISTER_ADDRESS_BUG.md.** The rate was never
uncontrollable; the project has been writing the timebase and energy registers to
the wrong addresses (one extra hex zero, so 16× off). With the addresses the
Windows DLL actually uses, four of the five parameters are met on the Pulser path
alone, and the fifth is only unverified for want of a fast timebase.

| parameter | achieved (Pulser) | target | |
|---|---|---|---|
| rate | **1000 Hz** (period 312499) | 1 kHz | ✅ formula verified −0.0% |
| amplitude | **1.0036 V** | 1 V | ✅ |
| baseline | **−0.0137 V** | 0 V | ✅ |
| decay τ | **50.2 µs** | 50 µs | ✅ |
| rise | not measurable at 50 µs/div | 100 ns | ⚠️ needs ~100 ns/div |

Reproduce with `python3 pulser.py --rate 1000 --amp 1.0 --decay 50`.

The section below is the pre-discovery survey, kept because its mode-by-mode
results remain valid; only the "rate is uncontrollable" conclusion is wrong.

---

## 2. Every mode tested

### 2.1 `ChannelMode` — the top-level axis (register `0xFA00100A`)

Only two values exist: **Pulser** and **AWG**. Both tested.

### 2.2 Pulser shape datapaths (manual §10 — three of them)

| datapath | selected by | result |
|---|---|---|
| Custom / memory ("Exponential-Fast") | shape RAM at `0x50f00x` + `0x500000` | ✅ **works** — the only Pulser path that drives the DAC |
| Digital RC | `0x300010=0`, coeffs, `0x30000b=-1` | ❌ **no output** (0.068 V = noise floor) |
| Pulsed reset | `0x300010=1`, `0x300012/13` | ❌ **no output** |

Digital RC was retested here with coefficients for the new spec
(rise=100 ns, fall=50 µs) on a cold board — still nothing. This is consistent
across every attempt this session and is the project's long-standing open item.

### 2.3 Timebase modes — the rate axis

| mode | `TimeMode` / `TimebaseMux` | result |
|---|---|---|
| Constant rate | 0 / 0 | output ✅, **rate does not respond** |
| Poisson | 1 / 0 | output ✅, rate does not respond |
| Sequence | 2 / 6 | output ✅ but identical to constant — no sequence data uploaded |
| Sequence | 2 / 7 | ❌ no output |
| External trigger | 0 / 4 | ❌ no output (expected — no trigger source connected) |

**The period register `0x01000009` was swept 2000 → 1,000,000 — a 500× change —
with no effect whatsoever on the output.** Together with the earlier sweeps of
the Poisson α register `0x01000006`, the LFSR strobe, the deadtime registers and
`TimeMode`, this closes the question: **rate is not controllable in the Pulser
datapath by any register found so far.**

### 2.4 AWG mode

Rate **is** controllable, via waveform length:

    rate = 312.5 MHz / (DataLen × ClockPerStep)

Verified cleanly at ClockPerStep=31 by varying DataLen:

| DataLen | predicted | measured |
|---|---|---|
| 1008 | 10001 Hz | **10000 Hz** |
| 2016 | 5000 Hz | 5394 Hz |
| 5040 | 2000 Hz | 2168 Hz |
| 10080 | 1000 Hz | 1125 Hz |

The agreement degrades as the period approaches the 1000 µs scope window — that
is a measurement limit, not a device limit (see §4).

**ClockPerStep is exact** (corrected 2026-09-23). Reaching the same rate two
ways confirms it: CPS=3/DataLen=20832 → 5003 Hz measured, CPS=31/DataLen=2016 →
5000 Hz measured, implied CPS 3.00 and 31.00. A 20 kHz cross-check gives 2.98 and
30.99. An earlier note here called CPS unreliable; that was a broken peak
detector splitting one wide pulse, not the device. So CPS=3 (9.6 ns/sample) is
usable and a 100 ns rise is 10 samples.

**AWG bypasses the analog stage entirely.** Measured, all with no effect:
`0x0f000001` gain, `0x0f000000` offset, `0x0f000004` invert. Amplitude, polarity
and baseline must all be baked into the sample array. Transfer measured at
CPS=31, 1 MΩ:

    output_volts = −1.296e-4 × array_code − 0.12 V

The output is inverted with respect to the array. **The −0.14 V baseline could
not be removed** — array DC was swept −926 to −4085 with no change, so the path
appears AC-coupled or strips the DC term.

---

## 3. What is solidly achieved

On the **shape-RAM path**, verified repeatedly:

```
width_us=300, decay_us=50, corner=4, gain=1107, offset=-55934, invert=1
  amplitude  0.99 V      (target 1 V)
  baseline   0.000 V     (target 0 V)
  decay tau  49-51 us    (target 50 us)
```

The decay tracks its argument almost 1:1 and the baseline sits exactly on zero.

Two enabling discoveries this session:

* **`0x50f005`, the interpolator corner point.** The vendor code leaves it at 0,
  which collapses the fine rise region. Raising it shortens the rise but steals
  samples from the tail (τ collapses to 18 µs at corner=100, 1.9 µs at corner=400).
  corner=4-6 is the usable window.
* **Digital offset is linear at 3.234e-5 V/count** at gain=1107, so −55934 puts
  the baseline on zero. Only valid at that gain — the manual notes offset is
  applied *before* the gain stage.

---

## 4. Why the remaining parameters are unmeasured, not just unmet

Everything in this report was measured through a **1000 µs window sampling at
1 µs**. That single setting makes two of the five parameters physically
unmeasurable:

* **100 ns rise** is one tenth of one sample. Cannot be seen at all.
* **Rate near 1 kHz** has a 1000 µs period against a 1000 µs window — exactly one
  period, so the scope's `FREQ`/`PER` return nothing and peak-counting cannot
  separate one pulse from two. With a 50 µs decay the pulse is ~35 µs wide above
  threshold, which also defeats naive peak detection.

These are the two parameters still open. That is not a coincidence.

**To finish, three scope settings are needed** (I can only change trigger level):

| to measure | timebase | why |
|---|---|---|
| rise 100 ns | **~100 ns/div** | 1 µs window, ~1 ns sampling |
| decay 50 µs | ~20 µs/div | already effectively confirmed |
| rate 1 kHz | **~500 µs/div** | 5 ms window = 5 periods, `FREQ` becomes valid |

---

## 5. Recommended route to all five parameters

**Option A — fix AWG's baseline (most likely to work).**
AWG already gives rate and has the sample resolution for a 100 ns rise. The only
blocker is the fixed −0.14 V pedestal. The lead worth chasing is that AWG
probably has its own offset/gain registers in the `0xFA0010xx` block rather than
using `0x0f00000x`; the survey only touched `0xfa001000-5`. Also worth fixing the
ClockPerStep encoding, since CPS ≤ 8 is required for the rise.

**Option B — give the shape-RAM path an external trigger.**
Shape-RAM already nails amplitude, baseline and decay. `TimebaseMux=4` selects an
external trigger sampled at 4 ns (manual §10), so a 1 kHz square wave into the
digital input would set the rate exactly. This needs a cable — either a signal
generator, or loop the board's own digital output back via `DT_SetDIO`. This is
the shortest path to all five if a trigger source is available.

**Option C — sequence timebase.**
`TimebaseMux=6` produced output but behaved identically to constant rate because
no sequence data was uploaded. Real support needs the `DT_ProgramDDR` time-
sequence format, which the project notes flag as only partly reverse-engineered
(`FUN_10008710`). Highest effort, but software-only.

My recommendation is **B if you can patch a cable, A otherwise.**

---

## 6. Scripts

| file | purpose |
|---|---|
| `t17_mode_survey.py` | sweeps every mode, records output/amplitude/baseline/τ |
| `t18_rate_robust.py` | rate measurement with de-duplicated peak detection |
| `deliver_pulse.py` | shape-RAM path — best amplitude/baseline/decay |
| `deliver_pulse_awg.py` | AWG path — rate control, capable rise |
| `scope.py` | strictly read-only scope access (only `:TRIG:EDGE:LEV` permitted) |
| `analyse_trace.py` | waveform capture and shape characterisation |

Full background, including the corrected understanding of the datapaths and the
register-level findings, is in `FINDINGS.md`.

---

## 7. AWG GUI (added 2026-09-23)

`awg_gui.py` + `awg_backend.py` — PyQt6 control panel for AWG mode.

```bash
python3 awg_gui.py
```

* Shapes: detector pulse (rise + exponential decay), sine, square, triangle,
  sawtooth, pulse, sinc, DC, noise.
* Rate entered directly in Hz. `plan_rate` picks ClockPerStep and DataLen from
  `rate = 312.5 MHz / (DataLen × ClockPerStep)`, defaulting to the smallest CPS
  for the finest time resolution. Verified 1 Hz → 100 kHz to within 0.16%.
* Live preview of the exact array to be uploaded, drawn with the polarity
  inversion applied so it shows what the scope will show.
* "Shape resolution" warns in red when the requested rise spans fewer than three
  array samples — the one way to silently ask for a rise the array cannot carry.
* Uploads run on a worker thread; a 104160-sample 1 kHz array takes ~0.1 s.
* Optional scope panel polls the Rigol read-only (the only write anywhere in the
  GUI is `:TRIG:EDGE:LEV`).

Verified end-to-end on hardware: 1 kHz plan (CPS=3, DataLen=104160) uploads and
runs, giving 0.92 V amplitude. The generated array measures 96 ns rise and
50.05 µs tau against targets of 100 ns and 50 µs.

**Known limitation carried into the GUI:** the AWG baseline sits at about
−0.14 V and cannot be moved. Analog gain `0x0f000001` (300 → 1500) and offset
`0x0f000000` (0 → −70000) were both swept with no effect on the output, as was
`0x0f000004` invert — AWG bypasses the analog stage entirely. Array DC was also
swept ~0.6 V worth with no effect. `AWG_REGISTERS.md` lines 38-39 claim those
registers work in AWG mode; that is wrong and was most likely measured in Pulser
mode. The GUI states the baseline in its status bar rather than offering a
control that does nothing.

---

## 8. Two-region interpolator — arbitrary rise with a long tail (2026-09-24)

Ryan's observation that the Windows program sets rise time arbitrarily was
correct, and tracking it down closed the last shape gap.

### How DDE-Control does it

Rise time is **not a register**. `DDE-Control.decompiled.cs:12921` low-pass
filters the ideal exponential on the host before upload:

```csharp
num2 = TimePerSame*2 / (RiseTime/5 * 1E-05/2.97 + TimePerSame*2);
num5 = exp(-(n-4) / (FallTime*1E-05/(TimePerSame*2*2*PI))) * 32575;
array[n] = array[n-1]*(1-num2) + num5*num2;      // first-order IIR
```

`num2` is the filter coefficient and comes from the rise time, which is why it is
continuous and arbitrary. Exactly manual §10 step 2.

Our `set_detector_pulse` wrote a bare exponential with an **instantaneous rise**,
so rise was never controllable — and the interpolator corner was being abused as
a substitute for it.

### Why one array is not enough

A 100 ns rise with a 250 µs tail is 2500:1. 4096 uniform samples cannot carry
both. The vendor's answer (manual §10 steps 4-5) is to split the array at a
corner and give each region its own linear-interpolation factor: *"500 points are
reserved to the rising edge, the remaining 3596 are for the tail."*

`tworegion.py` implements this:

| register | meaning |
|---|---|
| `0x50f005` | corner, in RAM samples |
| `0x50f007` | **rise** region interpolation factor, packed `((0x10000/(f+1))<<16)\|f` |
| `0x50f006` | **tail** region factor, same packing |
| `0x50f004` | `3` = both regions enabled |

One adaptation: a fixed 500 rise points would need sub-nanosecond spacing for a
100 ns rise, so the rise point count adapts to the finest usable factor and is
capped at 500.

### The interpolation step is 3.2 ns

Getting this wrong made the decay come out long. Working back from the vendor's
own magic constant (`interp = width_us/3.185`) the step is
**1/312.5 MHz = 3.2 ns** — one quarter-clock tick, the same clock the timebase
and AWG use. With 1 ns the decay rendered 2.3× long; with 2 ns, 1.5× long; with
3.2 ns it lands within ±10%.

### Verified on hardware

| requested decay | measured |
|---|---|
| 20 µs | 22.1 µs |
| 50 µs | 49.1 µs |
| 100 µs | 93.7 µs |
| 200 µs | 179.9 µs |

And the target spec, `Pulser().set_pulse(rate_hz=1000, amplitude_v=1.0, decay_us=50, rise_us=0.1)`:

| parameter | measured | target |
|---|---|---|
| rate | 1000.0 Hz (period 312499) | 1 kHz ✅ |
| amplitude | 1.0104 V | 1 V ✅ |
| baseline | −0.0137 V | 0 V ✅ |
| decay τ | **49.4 µs** | 50 µs ✅ |
| rise | **114 ns** (at 5 µs/div) | 100 ns ✅ |

### Rise calibration (2026-09-24, scope at 5 µs/div = 50 ns/sample)

Two corrections were needed before the rise tracked.

1. **Size the corner to the 10-90 time, not the time-to-peak.** Measurement showed
   the rendered edge fills essentially the whole rise region (measured/region =
   0.96 at factor 1), and the time-to-peak is ~2.7x the 10-90 time, so every rise
   came out ~3x slow. After this the ratio became a consistent ~2.8x instead of
   drifting 3.1 -> 1.4.
2. **An empirical `RISE_SCALE = 2.83`.** The residual constant factor is *not*
   explained. The decay calibrates correctly at the same 3.2 ns step, so it is
   specific to the fine rise region — possibly a minimum dwell in the
   interpolator. It is divided out so the requested value is what appears.

| requested | measured | error |
|---|---|---|
| 100 ns | 107 ns | +7% |
| 200 ns | 207 ns | +4% |
| 500 ns | 514 ns | +3% |
| 1 µs | 1000 ns | 0% |
| 2 µs | 1993 ns | 0% |

### All five parameters — but not in one scope view

| parameter | measured | at |
|---|---|---|
| rate 1 kHz | 1000.0 Hz, formula −0.0% | counted pulses |
| amplitude 1 V | 1.0036 V | any |
| baseline 0 V | −0.0137 V | any |
| rise 100 ns | 114 ns | 5 µs/div |
| decay 50 µs | 49.4 µs | 200 µs/div |

Rise and decay differ by 500:1, so no single timebase resolves both — that is a
property of the measurement, not the signal.

### Operational quirk worth knowing

**The first shape programming after bringup does not take effect; a second
identical call does.** Seen repeatedly (Vpp 0.019 then 1.012 on the same
parameters). `set_pulse` should be called twice after a fresh bringup, or the
programming sequence needs a settle/latch step that has not been identified.
This very likely accounts for several confusing "no output" results earlier in
this work.

---

## 9. How Windows sets arbitrary rise — and why our 2.83 factor exists (2026-09-24)

### The vendor has two mechanisms, not one

**FAST / shape-RAM** (`DDE-Control.decompiled.cs:12921`): rise is baked in by a
host-side first-order IIR low-pass. Exact coefficient:

    num2 = 2e-9 / (RiseTime*6.734e-7 + 2e-9)     ->  tau_rise = RiseTime * 6.734e-7 s

(so with RiseTime in µs, the 10-90 is 1.48 × RiseTime — the vendor's "rise time"
is not literally the 10-90 figure. I had assumed tau = rise/2.197.)

**But this path cannot carry a long tail.** The array is 4096 samples at 2 ns =
**8.192 µs total**:

| FallTime | tau | array covers |
|---|---|---|
| 0.05 µs | 0.08 µs | 103 tau |
| 1 µs | 1.59 µs | 5.1 tau |
| **50 µs** | 79.6 µs | **0.103 tau** |

At 50 µs the shape barely decays. This is exactly why the Auto rule is
*τfall < 100 ns → FAST, else Digital RC*, and why manual §11 says the OneTouch
controls force Digital RC.

**So for 1 kHz / 100 ns / 50 µs, the Windows software would use Digital RC**,
where the rise is the second IIR filter's pole *in the FPGA* (rise_time/0.35,
manual §10), not a host array at all.

### Which means our approach is off-piste

We drive the FAST/shape-RAM path far outside its design domain (a 50 µs tail in a
path built for ≤1 µs). The two-region interpolator makes it work, and the
`RISE_SCALE = 2.83` is the price of that — an artifact of using the wrong path,
not a property of the instrument.

### Digital RC: clean negative result

Previous DRC conclusions were confounded — every earlier test ran with the
misaddressed energy register, so DRC had zero amplitude regardless. Retested
properly:

* energy at the correct `0x20f005` (verified to drive amplitude elsewhere)
* timebase at the correct `0x100009`
* all 16 shape generators explicitly disabled the vendor's way — `FUN_10005a30`
  shows `enable_shape=false` writes `0x50f000 = 0`
* `0x300010 = 0`, coefficients loaded and strobed, `0x30000b = 0xFFFFFFFF`

Result: **0.020 V, scope never triggers.** Digital RC produces nothing. This is
now a controlled result rather than a confounded one, and it is the single thing
standing between this port and doing the job the way the vendor does.

### What would remove the fudge factor

Make Digital RC work. Leads not yet exhausted:
* `DT_GetShapeMode` is assumed pure (no writes) — worth confirming in the DLL; if
  it writes a shape-datapath select register, that is the missing mux.
* the analog mux `0xF00000C2` was only ever tested at `0xF`; clearing bit 3
  killed the output entirely, so its encoding is not understood.
* the first-programming-does-not-take quirk may also apply to the DRC path.

---

## 10. Chasing the 2.83 — what it is not (2026-09-24)

### `DT_GetShapeMode` is pure — lead closed

It is **managed C#**, not a DLL export: a switch that sets four enable flags and
`return 0` (`DDE-Control.decompiled.cs:6281`). It touches no register despite
taking a connection handle. There is no hidden shape-datapath mux there.

Incidentally it also shows `ShapeMode.Auto` sets `EnableDRC = -1` unconditionally
— the τfall<100 ns rule lives elsewhere, or not at all in this build.

### Three conventions our ConfigureShapeGenerator port got wrong

From `FUN_10005a30` (`sg_decomp.txt`):

```c
0x50f005 = crosspoint >> 1                              // corner HALVED
0x50f007 = (0x10000/(rising+1))  << 16 | (rising - 1)   // low half is f-1
0x50f006 = (0x10000/(falling+1)) << 16 | (falling - 1)
0x50f004 = (rising != 0) | (falling != 0) << 1
// with interpolation active the packing DECIMATES BY 4:
word[u+2] = shape[8u+4] << 16 | shape[8u];  u < length>>3
0x50f003  = (length >> 3) + 3
```

Also confirmed here: for pure Digital RC the vendor **does** disable all 16 shape
generators (`enable_shape: flag6 && (flag3||flag2)`, both false for DRC), so the
earlier DRC test method was right and that negative result stands.

### Measured, one convention at a time (`t19_convention_matrix.py`)

Scope at 2 µs/div (20 ns/sample), 0.5 V/div, `RISE_SCALE` forced to 1:

| conventions | 100 ns | 500 ns |
|---|---|---|
| none (our default) | 2.84 | 2.70 |
| corner_halved | 8.72 | 3.61 |
| factor_minus_one | 1.92 | 1.86 |
| **decimated** | **0.96** | 0.72 |
| corner_halved + factor_minus_one | 6.98 | 2.53 |
| all three | 4.82 | 1.53 |

`decimated` alone is much the closest and needs no fudge factor at 100 ns. But
extending the sweep showed it is **erratic outside 100-500 ns** — 50 ns → 2.56,
1 µs → 0.24, 2 µs → 0.51, and non-monotonic (1 µs rendered faster than 500 ns).
Re-deriving the region geometry in RAM samples to match the decimation put the
ratio straight back to 2.8, so the existing geometry and that packing are already
mutually consistent.

### Decision

Kept the existing (non-decimated) sequence with the empirical `RISE_SCALE = 2.83`,
because it is monotonic and accurate to **±6% from 100 ns to 2 µs** — measured
106 / 204 / 496 / 978 / 1904 ns for 100 ns / 200 ns / 500 ns / 1 µs / 2 µs.

So 2.83 is still empirical, but it is now bounded: it is **not** the corner
halving and **not** the factor off-by-one, both of which make things worse. The
decimated packing is the only candidate that removes it, and only over a narrow
range. All three remain switchable in `tworegion.program()` for future work.

The switches are `corner_halved`, `factor_minus_one`, `decimated`; defaults
reproduce the verified-working behaviour.
