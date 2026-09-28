# DT5810B on Linux — consolidated reference

**This file is the entry point and the current, corrected state of knowledge.**
`docs/` holds the longer-form documents: `REGISTER_ADDRESS_BUG.md` (current),
and `docs/superseded/` — the chronological working notes, kept for their
evidence and for the record of what was tried and failed. Several of their early
conclusions were later disproved; each is flagged in §9, and
`docs/superseded/README.md` lists the main ones. Where they disagree with this
file, this file wins.

Board: CAEN / Nuclear Instruments DT5810B, USB `21e1:000e`, CH1 and CH2 into a
Rigol DHO4804 at 1 MΩ. Everything below was measured on this setup,
2026-09-22..28.

---

## 1. Quick start

```bash
~/caen_signal_emulator/pdu/pduOnOff.sh on 3          # power (PDU load 3)
python3 ~/caen_signal_emulator/linux/fx3_firmware_loader.py   # 000d -> 000e
python3 pulser_gui.py        # detector-emulator GUI  (recommended)
python3 awg_gui.py           # arbitrary-waveform GUI
```

Headless, the reference signal (1 kHz, 1 V, 100 ns rise, 50 µs decay, 0 V base):

```bash
python3 pulser.py --rate 1000 --amp 1.0 --decay 50
```
```python
from pulser import Pulser
p = Pulser().open()
p.set_pulse(rate_hz=1000, amplitude_v=1.0, decay_us=50, rise_us=0.1)

# both channels, fired together, CH2 trailing CH1 by 250 ns (see §9e)
p.set_pulse(rate_hz=1000, amplitude_v=1.0, decay_us=50, rise_us=0.1, ch=0)
p.set_pulse(rate_hz=1000, amplitude_v=0.6, decay_us=50, rise_us=0.1, ch=1)
p.set_correlation(CORR_TIMEBASE, delay_ns=250)
```

After a cold power-up the FX3 firmware is volatile and the board comes up at
`21e1:000d`; the loader must run before anything else works.

---

## 2. ⭐ The register-address bug

**Two whole register families in the pre-existing project code and docs carry one
extra hex zero, so every write to them landed 16× away from the real register.**
This is the single most important thing in this folder. It is why the pulse rate
appeared "locked at 318 Hz" for months and why the `energy` argument never
affected amplitude.

| block | old project code | **DDE3.dll actually uses** |
|---|---|---|
| Timebase | `0x0100000a`, `0x01000009`, `0x01000006`, `0x01000007/8` | **`0x10000a`, `0x100009`, `0x100006`, `0x100007/8`** |
| Energy | `0x020f0002/4/5` | **`0x20f002/4/5`** |

The trap that hid it: `0x01c00006 == 0x1c00006` — the *same number* — so run
control always worked and the addressing looked fine. But `0x01000009` =
16,777,225 while `0x100009` = 1,048,585.

Evidence: `tb2_asm.txt` (`LEA EBX,[ESI + 0x100009]` with `ESI = ch<<28`),
corroborated by `ghidra_tb.txt` and `w_decomp.txt`; energy from
`ghidra_decomp2.txt` (`param_1 * 0x10000000 + 0x20f004`).

All other families (`0xf00000x`, `0x1c0000x`, `0x30000x`, `0x50f00x`) are
correct as-written.

---

## 3. Corrected register map

All offsets OR-ed with `channel << 28`.

### Timebase — `ConfigureTimebase` / `FUN_10005080`
| reg | meaning |
|---|---|
| `0x10000a` | TimeMode: 0 constant, 1 Poisson, 2 sequence. **Written first.** |
| `0x100009` | period = `round(312.5e6 / rate) - 1` |
| `0x100006` | Poisson α = `2^32 · 0.25 · rate / clock` |
| `0x100007` | paralyzable flag |
| `0x100008` | dead time |

Rate is clamped to ≥ 0.01 cps in the DLL, matching the manual's range.

### Energy
| reg | meaning |
|---|---|
| `0x20f002` | energy LFSR strobe (1 then 0) |
| `0x20f003` | **spectrum RAM gate** (1 open, 0 close) — see §9f |
| `0x20f004` | EnergyMode: 0 fixed, 1 spectrum, 2 sequence |
| `0x20f005` | energy value = **LSB × 2**, must stay ≤ 32767 (40000 wraps) |
| `0x200000` | **spectrum RAM**, 16384 words of cumulative histogram, one burst |

### Correlation block — see §9e. **Absolute addresses, not channel-scoped.**
| reg | meaning |
|---|---|
| `0x2E000000` | `(correlation_mode & 7) \| (enable_ch3 << 3)`; mode 2 = shared timebase |
| `0x2E000001` | CH2 delay, in 1.25 GS/s DAC samples (0.8 ns) |

### Analog output stage
| reg | meaning |
|---|---|
| `0x0f000000` | digital offset (baseline), signed |
| `0x0f000001` | digital gain |
| `0x0f000002` | channel enable |
| `0x0f000004` | invert |
| `0x0f000005` | TimebaseMux: 0 internal, 4 external trigger, 6/7 sequence |
| `0x0f000006` | EnergyMux |
| `0xF00000C2` | analog mux — **decoded 2026-09-24, see below** |
| `0xF00000C3` | `0xF1CA` commit, end of bringup |

### Run control
| reg | meaning |
|---|---|
| `0x01c00003` | reset-start |
| `0x01c00005` | run mode (0 = free run) |
| `0x01c00006` | **the RUN gate** (1 run, 0 stop) |

### Shape generator — `ConfigureShapeGenerator` / `FUN_10005a30`
Base per shape: `((ch<<8) | sid) << 20`, sid 0..15, **all 16 must be programmed**.

| reg | meaning |
|---|---|
| `0x50f000` | **enable_shape** — writing 0 across all sids kills the output |
| `0x50f001` | multishape id |
| `0x50f002` | RAM gate (1 open, 0 close) |
| `0x50f003` | length |
| `0x50f004` | interpolator enable: bit0 rising, bit1 falling |
| `0x50f005` | interpolator **crosspoint** (corner) |
| `0x50f006` | **falling/tail** factor, packed `(0x10000/(f+1)) << 16 \| f` |
| `0x50f007` | **rising** factor, same packing |
| `0x500000` | shape RAM block (0x800 words) |

**`0x50f005` is a 7-BIT field — it wraps above 127.** Measured: corner 114 gave a
rise ratio of 0.89, corner 133 gave 1.64, a hard discontinuity across 128. That is
why the DLL writes the corner as `crosspoint >> 1` — halving fits a larger range
into 7 bits. We instead cap the corner at 127 and buy extra region length with the
*factor*, which removes the wrap without halving the resolution.

The DLL's other two conventions (factors as `f-1`, decimate-by-4 packing when
interpolation is active) were tested individually and are *not* used — see §8.

### Analog mux `0xF00000C2` — decoded from `FUN_10007650`

```c
ch0:  value = (cached & 0x3) + (filter + analogsel*2) * 4     // bits 2-3
ch1:  value = (cached & 0xc) +  analogsel + filter*2          // bits 0-1
then: write(analogsel, 0xF0000043 + channel)                  // HP/HR range
```

| value | CH0 filter | CH0 analogsel |
|---|---|---|
| `0x0F` | ON | 1 — **what we run** |
| `0x0B` | **OFF** | 1 — same connector, filter removed |
| `0x07` | ON | 0 — *different output connector* |

**`invert` is NOT in this register** (it is `0x0f000004`). Two older notes are
wrong about it: `KNOWLEDGE_BASE.md`'s `(range&3)+(invert<<2)+(filter<<3)`, and
`DRC_SESSION_CHECKPOINT.md`'s "0xF=pos, 0xB=neg".

This also corrects something I concluded earlier in this work: writing `0x07`
did not "kill the output" — it switched `analogsel`, i.e. moved the signal to the
**other output connector**. Untested prediction: `0x0B` removes the 30 MHz filter
while keeping the same connector, which per manual Tab 9.1 should take the rise
from 25 ns to ~1 ns. Note the vendor reference captures show a ~10 ns hardware
floor with whatever filter setting the Windows software uses, so this may already
be off there.
`experiments/t20_analog_filter.py` is written and ready to run.

### Digital RC
`0x300000`..`0x300009` coefficients, `0x30000a` latch strobe, `0x30000b` enable
(`-1` = 0xFFFFFFFF), `0x30000d` rise prescaler, `0x300014` prescaler,
`0x300010` TR enable (0 = Digital RC, 1 = pulsed reset),
`0x300012/13` pulsed-reset ramp amplitude and scaling — **only valid when TR=1**.

### AWG
`0xFA00100A` ChannelMode (1 = AWG), `0xFA001000` DDR pointer, `0xFA001001`
reset/commit/enable (8 = play), `0xFA001002` DataLen/16−1, `0xFA001003`
`0x2000000`, `0xFA001004` 99, `0xFA001005` ClockPerStep−1, `0xFA000000` DDR data.

---

## 4. USB protocol

```
write : f1 ff ba ab | addr(LE) | count(LE) | value(LE)
read  : f0 ff ba ab | addr(LE) | N(LE)          -> N words back on EP 0x81
```
EP OUT `0x02`, EP IN `0x81`.

**Address verbatim, count = N exactly.** `linux/dt5810.py:186` sends `addr-1` and
`count+2`; both are wrong — proven by reading `0xFFFF0000` both ways and seeing
the response shift by one word. The "reads are flaky, retry 4×" logic in
`board_id()` is a symptom of that, not a device trait.

**Config space is write-only.** Every config register returns the repeating
`FF FF BA AB` filler. `0xFFABBAFF` and `0xFFFFABBA` are the *same* filler at
different byte alignments, not two distinct markers. Verification must be done on
the analog output.

**`board_id()` is a red herring** — it never returns `0x1005810B` even on a
perfectly working board. Do not use it as a liveness check.

---

## 5. Measured calibrations

At gain 1184, 1 MΩ, CH0.

| quantity | relation | source |
|---|---|---|
| rate | `312.5e6 / (period + 1)` | verified −0.0% at 4/8/16/31 kHz |
| amplitude | `V = 3.414e-5 × energy_reg + 0.0205` | linear 4000→30000 |
| baseline | `3.234e-5 V/count`; `offset = -55512` → 0 V | 0/−20000/−55465 → 1.809/1.174/0.026 V |
| CH2 baseline | `3.401e-5 V/count`; `offset = +58371` → 0 V | −30000/0/+30000 → −2.999/−1.986/−0.958 V |
| CH1→CH2 delay | `0.800 ns/count`, zero at register 48 | 3.200 µs over 4000 counts; residual flat, mean error +2.0 ns |
| decay | `DECAY_SCALE = 1.048` (request τ/1.048) | 20/50/100/200 µs all within 10% |
| rise | region spans `N_TAU = 4` tau, past the peak; array ≤254 samples, write n/2 | 100→179, 200→350, 500→836, 1000→1414, 2000→2329 ns (runs 1.2-1.8x long) |
| interpolation step | **3.2 ns** = 1/312.5 MHz, one quarter-clock tick | from `interp = width_us/3.185` |
| AWG rate | `312.5e6 / (DataLen × ClockPerStep)` | DataLen 1008/CPS 31 → 10000 Hz |
| AWG amplitude | `≈ 1.40e-4 V per array count` | 3000→0.41, 7000→0.887 V |
| AWG transfer | `V = -1.296e-4 × code - 0.12` (inverted) | |

Timebase, AWG and the shape interpolator all run on the same **312.5 MHz quarter
clock** (1.25 GHz / 4).

---

## 6. What works

**Pulser + shape RAM** — the working detector-emulator path. Triggered, supports
Poisson statistics and pile-up. Reference signal verified:

| parameter | measured | target |
|---|---|---|
| rate | 1000.0 Hz | 1 kHz |
| amplitude | 1.05 V | 1 V |
| baseline | −0.01 V | 0 V |
| rise 10-90 | 102 ns | 100 ns |
| decay τ | 49.4 µs | 50 µs |

**AWG** — arbitrary waveforms at any rate, 1 Hz to 100 kHz within 0.16%. Good for
function-generator work; not a detector emulator (it loops, so no statistics) and
it has visible chunk-boundary glitches every ~2048 samples.

**Both channels together** — CH1 and CH2 each with their own amplitude, shape,
polarity and baseline (CH2's analog stage is inverted; §9d). With the correlation
block they fire from one timebase with a programmable CH2 delay, 0.8 ns steps to
4 µs, mean timing error +2.0 ns (§9e). Without it they free-run, which at equal
rates means a fixed but arbitrary phase offset, not independence.

**Energy spectra** — per pulse amplitudes drawn from a histogram instead of a
fixed value (§9f): Gaussian peaks, continua, or a CSV, 16384 bins up to 1.18 V at
the default gain. A Gaussian of σ 0.1 V measures σ 0.105 V.

---

## 7. What does not work

**Digital RC produces nothing.** Clean, controlled negative: correct energy and
timebase addresses, all 16 shape generators disabled the vendor's way, `0x300010=0`,
coefficients loaded and strobed, `0x30000b=0xFFFFFFFF` → 0.020 V, scope never
triggers. The coefficient maths itself is verified correct against `asm5340.txt`.
This is the main unsolved item, and it matters because **DRC is the path the
Windows software would use** for a long-tailed pulse (Auto picks FAST only below
τfall = 100 ns).

**AWG baseline** is pinned at ≈ −0.14 V and cannot be moved. Gain, offset *and*
invert are all bypassed in AWG mode; array DC was swept ~0.6 V worth with no
effect. Path looks AC-coupled or strips DC.

**Rate in Pulser is fine now**, but sequence timebase (`TimebaseMux` 6/7) and
external trigger (mux 4) are untested — the former needs the `ProgramDDR`
time-sequence format, the latter needs a cable.

---

## 8. Traps that cost real time

1. **The extra-zero addresses** (§2). Still present in `linux/dt5810.py`,
   `linux_port/core/`, `REGISTER_MAP.md`, `HOW_IT_WORKS.md`, `PROTOCOL.md` and
   `KNOWLEDGE_BASE.md` §7.2/7.3.
2. **The first shape programming after bringup does not take effect.** A second
   identical call does. Seen repeatedly (Vpp 0.019 then 1.012 on identical
   parameters). Cause not identified -- config space is write-only so there is
   nothing to poll. **Handled automatically since 2026-09-24:** `Pulser.set_pulse`
   programs twice on its first call after `open()` and once thereafter; the pass
   count is reported as `programming_passes` in the returned dict, and
   `force_reprogram=True` forces a double pass. The GUI inherits this. Note this
   quirk probably invalidates some older "no output" results in the working notes.
3. **`0x50f000 = 0` gates the generator off**, it is not just a reconfigure
   window. Do not write it to 0 after programming.
4. **The vendor's default `offset = -55465` clips.** It is ≈ −1.87 V and pushes
   the natural 1.81 V baseline below zero.
5. **Scope scale before code.** Several hours went into "dead output" that was
   the scope at 50 mV/div clipping a 1 V pulse, and into "no pulse train" that
   was a 20 µs window missing a 318 Hz signal. *Always read the timebase and
   vertical before concluding anything about the hardware.*
6. **Don't auto-calibrate against a noisy measurement.** A loop that adjusts from
   measured values will chase quantisation noise; one talked itself from 100 µs
   down to 95 µs. Pin calibrated constants instead.
7. **THE FPGA WEDGES.** Three times in one session: USB still enumerates, writes
   are accepted without error, and the analog output freezes on the last good
   configuration. A frozen trace looks exactly like a good measurement, and
   conclusions were twice drawn from one before it was noticed. Recovery is a
   PDU power cycle with a **12 s** off period — the script's default 1 s is too
   short, the FX3 never loses power and comes back at `000e` instead of `000d` —
   then reload the FX3 firmware.

   **Guard:** `Pulser.assert_alive(scope)` toggles the run gate and raises if the
   output does not collapse. Call it before any measurement run.

8. The Rigol drops the TCP session if queried rapidly — one persistent socket,
   ≥1 s between measurement bursts.

---

## 9. Corrections to earlier conclusions

Stated in the working notes and **later disproved**:

| claim | status |
|---|---|
| "The 318 Hz lock is the library hardcoding the period" (FINDINGS B6) | **wrong** — it was the register address |
| "`TransistorReset` = pulsed reset is the headline fix for the triangle" (FINDINGS A1) | real bug, but **not** the blocker; fixing it does not make DRC work |
| "AWG's ClockPerStep is unreliable" | **wrong** — a broken peak detector; CPS is exact (implied 3.00 / 31.00) |
| "`AWG_REGISTERS.md`: gain/offset registers work in AWG mode" | **wrong** — measured no effect; that law was from Pulser mode |
| "DRC produces nothing" (t12, first version) | right conclusion, but that test was confounded by the energy address; re-proved properly later |
| "Rise can be set via the interpolator corner" | corner affects it, but rise is properly set by **host-side IIR filtering** |

Still-correct manual findings from the early work: `TransistorReset` really is
the pulsed-reset integrator (manual §10); the interpolator reaches 26 ms so the
"4 µs max shape" claim in `KNOWLEDGE_BASE.md` §10.2 is wrong; DRC minimum time
constant is 20 ns.

---

## 9b. Matching the vendor's edge shape ⭐

The single most useful thing in this work: comparing our output against the
**vendor reference traces** in `reference/` (the Windows software driving this same
board) found two real bugs that no amount of reasoning had.

### Bug 1 — we rendered only half the curve

`corner_t = rise / RISE_SCALE` sampled the ideal shape over 353 ns when tau was
455 ns, reaching only **54% up the edge**. The hardware then stretched that partial
curve across the whole rise, giving a near-linear ramp that jumped when the coarse
tail took over:

```
ideal single pole, 10%->90%:   0.10  0.42  0.63  0.76  0.85  0.90
vendor:                        0.17  0.40  0.63  0.71  0.85  0.91
ours (before):                 0.11  0.26  0.32  0.49  0.51  0.91
ours (after):                  0.14  0.34  0.55  0.73  0.78  0.90
```

**Fix:** size the rise region to `N_TAU = 4` time constants so the edge completes
inside it. Shape rms difference against the vendor went **0.23 -> 0.04**.

### Bug 2 — the corner register is 7 bits

See §3. Capping at 127 removed a hard discontinuity at 700 ns.

### Result

| requested | before | after | vendor |
|---|---|---|---|
| 30 ns | 273 ns | **48 ns** | 31 ns |
| 40 ns | 160 ns | **44 ns** | — |
| 50 ns | 58 ns | 54 ns | 48 ns |
| 100 ns | 102 ns | 124 ns | 90 ns |
| 1000 ns | 978 ns | 1157 ns | 1000 ns |

Ratios run 0.94-1.24 over 30 ns to 1.5 µs against a measurement precision of
±10-15%, so roughly one error bar from the vendor everywhere, with no catastrophic
region. `RISE_SCALE` is gone; the geometry is derived.

**Rise floor is now ~32 ns** (`2.197 × MIN_RISE_SAMPLES × RISE_STEP_S / N_TAU`),
below which the corner clamps and the edge stops getting faster. The GUI warns
there and says so.

Outstanding: we are ~20% long at 100-200 ns where the vendor is ~10% short, and an
800 ns measurement disagreed with 1000 ns despite identical hardware geometry — one
of those is a bad reading. A final scale trim wants repeat measurements first.

---

## 9c. The peak kink — solved (2026-09-25)

Ryan reported a hump at the peak of a 2 V / 1 µs rise / 50 µs decay pulse, then
pinned the first-derivative discontinuity at **~7 µs**. That one number solved it.
Two independent geometry bugs, both in where the fine rise region ends.

### Bug 1 — the region ended before the peak

For a shaped exponential the peak is at `tau_r * ln(1 + tau_d/tau_r)` = 2143 ns
for these settings, but the fine region ended at 1626 ns. The last part of the
climb was therefore drawn by the **coarse tail interpolator** (74 ns steps instead
of 9.6 ns), and that resolution change is a kink right at the peak.

It was guaranteed by construction: the code had `corner_t = min(peak_t, ...)`,
which explicitly capped the corner *at* the peak. Wrong for every rise/decay pair.

**Fix:** `PEAK_MARGIN = 1.25` — put the corner a quarter past the peak.

### Bug 2 — the hardware corner unit is 2 RAM samples

With corner=127 and rise factor=7 the transition should have been at
`127 x 7 x 3.2ns = 2.84 us`. Ryan measured 7 µs. The 2x variants bracket it:

| | |
|---|---|
| `corner x rf x 3.2ns` | 2.84 µs |
| `2 x corner x rf x 3.2ns` | 5.69 µs |
| `2 x corner x (rf+1) x 3.2ns` | 6.50 µs |
| **measured** | **~7 µs** |

So writing 127 gave the hardware 254 samples. This is exactly why `FUN_10005a30`
writes `crosspoint >> 1` — **two independent lines of evidence agreeing**, the DLL
disassembly and the scope.

**Fix:** the written field is 7 bits, and the hardware doubles it, so the array
can carry up to **254** rise samples. Build for `n_rise` (even, ≤254) and write
`n_rise // 2`. Both halves must change together — halving the written value alone
pinned the rise at ~1.25 µs for every request, because it shrank the fine region
to half of what the array was built for.

### Result

**Confirmed smooth on the scope.** The kink is gone.

The cost is rise-time accuracy: ratios are now **1.16-1.79** over 100 ns - 2 µs
(they were 0.96-1.75 before the corner-unit change, and a mix of measurements
earlier in the session suggested 0.94-1.24, which was almost certainly optimistic
— some of those runs were on a partly-wedged board).

So the shape is right and the magnitude needs a scale trim. That is the remaining
work on the rise, and it is a one-parameter fit rather than a structural problem.

### Method note

Four attempts were made at this geometry. The `N_TAU` change was clearly right —
it fixed the edge shape, rms 0.23 -> 0.04 against the vendor traces. The corner
work after it was largely trial and error, and the deciding evidence in the end
was a single number measured by eye off the scope, not anything inferred from the
output. When the geometry is wrong, ask for the time of the discontinuity.

---

## 9d. CH2 — one inversion, three symptoms (2026-09-25)

Driving CH2 with CH1's constants gave a **negative** pulse sitting at **−2 V**.
Three things looked wrong; there is only one cause.

**CH2's analog output stage is inverted relative to CH1.**

| | CH1 | CH2 |
|---|---|---|
| `0x0f000004` value for a positive-going pulse | **1** | **0** |
| offset slope | +3.234e-5 V/count | +3.401e-5 V/count |
| offset giving a 0 V baseline | −55512 | **+58371** |

The slopes agree to 5% once the polarity bit is right. The −3.44e-5 V/count first
measured on CH2 was the inversion showing through the fit, not a different gain —
and because CH1's offset law then solved the baseline in the wrong direction, it
drove the output to the rail. Fix the invert bit and the offset law follows.

This lives in `pulser.CH_OFFSET` and `pulser.invert_for_ch(ch, positive_going)`,
so `set_pulse(..., ch=1)` needs nothing extra from the caller.

Verified both channels simultaneously, 2 kHz, 20 µs decay, 1 µs rise, 0 V baseline:

| | requested | measured | baseline |
|---|---|---|---|
| CH1 | 1.00 V | 1.029 V | +0.004 V |
| CH2 | 0.60 V | 0.614 V | +0.008 V |

### Two measurement traps found on the way

**`scope.waveform()` silently returned CH1 data when asked for CH2.** Making
`scope.py` read-only removed its ability to send `:WAV:SOUR`, and it then returned
whatever source the instrument had selected without complaining. A CH1 trace was
reported as CH2 on the back of it. It now raises; `:MEAS:ITEM?` queries are
per-channel and need no source switch, so the GUI uses those.

**Two channels at the same rate are phase-locked, not independent.** Both
timebases divide the same 312.5 MHz clock by the same period register, so they run
at *identical* frequency with a fixed phase offset. With the scope triggered on
CH2, CH1's pulse sits at a constant position — permanently outside the window, in
our case, which read as a collapsed 0.09 V amplitude. Detune one channel by ~0.5%
and its phase walks through the window. This is a measurement artifact only; both
outputs were fine throughout.

---

## 9e. Channel sync and the CH2 delay (2026-09-25) ⭐

The two channels do **not** free-run independently in any useful sense. Both
timebases divide the same 312.5 MHz clock, so at equal rates they run at
*identical* frequency with a fixed but arbitrary phase offset, set by whichever
microsecond each run gate happened to be written in. There is no way to place
CH2 relative to CH1 that way.

The hardware has a proper answer: the **correlation block** (manual §9.3 "Delay
generation"), a master-slave mode where CH1 drives CH2's timing through a
circular buffer in the FPGA, one DAC sample per step.

### Registers

Found in `DDE3.dll`, export `DelayAndCorrelationControl` @`0x1000bac0` (a thunk
to the body at `0x10007fe0`). Both are **absolute** addresses, not channel-scoped:

| register | value |
|---|---|
| `0x2E000000` | `(correlation_mode & 7) \| (enable_ch3 << 3)` |
| `0x2E000001` | delay, in DAC samples |

`correlation_mode`: 0 disabled, 1 "CH2 follows exactly CH1", **2 "shared time
base generator"**, and the Ch3 mode is written as mode 0 with bit 3 set.

### The part that is easy to miss

Writing those two registers **does nothing on its own.** `DDE-Control`'s
`Update_Muxes()` sets `mode4 = 1` for `CorrelationMode.Timebase` and calls
`DT_TimebaseMux(mode4, handle, 1)` — CH2's timebase mux must be switched to take
its trigger from the correlation block:

```
(1 << 28) | 0x0f000005  =  0x1f000005   ->  1
```

That is the same `R_TB_MUX` the pulser already writes, which `_apply` used to
hardcode to 0 — so every `set_pulse` on CH2 silently dropped it back onto its own
timebase. `_tb_mux_for()` now derives it from the correlation state.

### Calibration

| quantity | value | evidence |
|---|---|---|
| delay step | **0.800 ns** = one 1.25 GS/s DAC sample | 3.200 µs over 4000 counts (t24) |
| zero point | `DELAY_ZERO_COUNTS = 48` | see below |
| range | −38 ns to +3.96 µs | manual says 4 µs |

At register 0 the outputs are *not* aligned — CH2 leads by a fixed ~38 ns of
pipeline skew. The zero point was estimated at 72 from a 7-point scan, then
trimmed by averaging the residual over 0–2400 ns, 14 captures per point: it came
out flat at **+18.9 ns (sd 6.2) with no slope**, which corrects the intercept and
independently confirms the 0.8 ns step. Since
`residual = (DELAY_ZERO_COUNTS − true) × 0.8`, a positive residual means the
constant is too high: 72 − 24 = **48**.

Verified after the trim, mean of 12 captures per point:

| asked | CH2 lags | error |
|---|---|---|
| 0 ns | 9.2 ns | +9.2 |
| 50 ns | 69.9 ns | +19.9 |
| 200 ns | 198.4 ns | −1.6 |
| 500 ns | 489.6 ns | −10.4 |
| 1500 ns | 1499.9 ns | −0.1 |
| 3000 ns | 2995.2 ns | −4.8 |

Mean error +2.0 ns. The ±10-20 ns scatter is the measurement, not the hardware:
the scope was at 2 µs/div = 20 ns per sample. **A faster timebase would let this
be trimmed further** — the scale is already exact, only the intercept is soft.

### What mode 2 leaves alone

Measured, not assumed:

* **Amplitude stays independent** — CH2 set to 0.6/0.9/0.4 V read 0.597/0.899/0.405
  with CH1 untouched at 1.01 V. Shape and polarity likewise.
* **Rate does not** — CH2 follows CH1 and ignores its own setting. With CH2
  programmed for 100 kHz throughout: correlation off, CH2 stayed at 100.0/99.9 kHz
  while CH1 went to 150 and 250 kHz; correlation on, CH2 tracked to 150.9 and
  250.8 kHz. The GUI disables CH2's rate box while synced.

### How it was measured, given a read-only scope

No scope writes were needed. The scope triggers on CH2 and `:WAV:SOUR` is CHAN1,
so **t=0 in the CH1 record is the instant CH2 crossed its trigger level** — the
time of CH1's own edge in that record is the skew. Only the *change* was used for
the scale, which cancels the threshold difference between the channels.

`:MEAS:ITEM? RDELay/FDELay,CHAN1,CHAN2` is **not supported** on this DHO4804
(the query times out); `RRPHase` is accepted but returned the 9.9e37 invalid
sentinel. Hence the edge-time method above.

---

## 9f. Energy spectrum mode (2026-09-28) ⭐

Instead of one fixed amplitude per pulse (`EnergyMode 0`), the board can draw
each pulse's height from a user-supplied distribution (`EnergyMode 1`). This is
what makes it a *source* emulator rather than a pulser.

### How the hardware does it

Manual §10, "From custom distributions to a set of values": a LUT-SR
pseudo-random generator produces a uniform 32-bit number and the board finds
which bin of the stored **cumulative** spectrum brackets it. Bin heights are
therefore probabilities — a bin twice as tall is drawn twice as often — and the
cumulative form means one memory cell per bin suffices.

### Registers

From `DDE3.dll` `ProgramSpectrum` @`0x1000b1a0` (body @`0x10004db0`):

| register | meaning |
|---|---|
| `0x20f003` | spectrum RAM gate, 1 open / 0 close |
| `0x200000` | spectrum RAM, **16384 words** |
| `0x20f004` | EnergyMode: 0 fixed, **1 spectrum**, 2 sequence |
| `0x20f002` | energy LFSR strobe, 1 then 0 |

All channel-scoped by `ch << 28`. The sequence is: open the gate, write the
block, close the gate, set mode 1, strobe.

### What the host computes

The DLL does the cumulative-and-normalise itself, so its caller hands over a
**raw histogram** and `spectrum.py` does the same:

```
cum[0] = 0
cum[i] = sum(hist[1..i])              # hist[0] never contributes
cdf[i] = round(cum[i] / cum[16383] * (2**32 - 1))
```

Two details worth keeping: **bin 0 is unusable** (the DLL's cumulative loop
starts at 1), and the top entry saturates the full unsigned 32-bit range.

`ConfigureLFSR(seed, LFSR_ENERGY, LFSR_REPROGRAM)` turns out to be *only* that
strobe of `0x20f002` — the seed argument is not written anywhere on that path,
so despite the manual's talk of a 64-bit reproducible seed there is no seed
control to reproduce here.

### Bin numbering

**Bin i drives energy register 2i**, consistent with the register being
documented as LSB × 2. Confirmed with delta spectra: bins 6393 / 13716 / 16383
produced 0.501 / 1.005 / 1.186 V against 0.500 / 1.000 / 1.182 V predicted. The
ceiling is bin 16383 → **1.18 V at the default gain**, rising with gain.

### The trap: one bulk transfer, not several

The 16384-word CDF must go in **one** `wr_block`. Splitting it into 1024-word
blocks at advancing addresses does not work — a delta spectrum then came back as
a mixture, median 0.63 V instead of a clean 1.00 V:

| upload | delta @ bin 13716 |
|---|---|
| 16 × 1024-word blocks | median 0.629 V, sd 0.188 |
| same, plus poking word 0/1 | median 0.626 V, sd 0.170 |
| **one 16384-word block** | **median 1.003 V, sd 0.007** |

The RAM evidently latches on one contiguous burst rather than honouring the
address on each successive block. The DLL does a single write of `0x4000` words,
which is the clue that was there all along.

### Verified

| test | expected | measured |
|---|---|---|
| delta spectrum | one amplitude | sd **0.007 V** |
| two equal bins | 50 / 50 | **51 / 49** (n=80) |
| two bins, 3:1 | 75 / 25 | **79 / 21** (n=90) |
| Gaussian, σ = 0.1 V | sd 0.10 V | sd **0.105 V** (n=80) |
| via the GUI, σ = 0.08 V | sd 0.08 V | sd **0.085 V** (n=70) |

### How to sample an amplitude distribution without biasing it

This is why the channel-delay work had to come first. If the spectrum channel is
also the scope's trigger source, the trigger level censors every pulse below it
and the sample is silently truncated. So:

* **CH2** fixed amplitude, the trigger source — an unbiased, reliable trigger
* **CH1** the spectrum, synced to CH2 by the correlation block (§9e) so every
  CH1 pulse lands in the window *whatever its height*

Then `:MEAS:ITEM? VMAX,CHAN1` samples CH1 with no selection effect.

### Using it

```python
import spectrum as S
p = Pulser().open()
p.set_pulse(rate_hz=2000, amplitude_v=1.0, decay_us=2, rise_us=0.1, ch=0)
h = S.peaks((p.bin_for_volts(1.05), 700, 1.0),     # (centre bin, sigma, area)
            (p.bin_for_volts(0.55), 700, 3.0))
p.set_energy_spectrum(h, ch=0)
...
p.set_energy_fixed(27431, ch=0)                    # back to one amplitude
```

`spectrum.py` also has `add_gaussian`, `add_flat`, `delta` and `from_csv` (the
same two-column shape as the vendor's "Import an Energy Spectrum from File").
In the GUI each channel has an **Energy** selector: Fixed, Gaussian peak, Two
peaks, Flat continuum, or a CSV file.

As with the timebase mux in §9e, `set_pulse` had to be taught not to clobber
this: it used to write `EnergyMode 0` unconditionally, which dropped a channel
back to fixed amplitude every time its shape was reprogrammed.

---

## 10. How the Windows software builds a shape

`DDE-Control.decompiled.cs:12921`, the FAST/exponential branch:

```csharp
num2 = TimePerSame*2 / (RiseTime/5 * 1E-05/2.97 + TimePerSame*2);   // IIR coeff
num5 = exp(-(n-4) / (FallTime*1E-05/(TimePerSame*2*2*PI))) * 32575;
array[n] = array[n-1]*(1-num2) + num5*num2;                          // low-pass
```

`TimePerSame = 1e-9`, so it builds at 2 ns/sample and **the rise time is baked in
by filtering** — it is not a register. τ_rise = `RiseTime × 6.734e-7` s.

But that array is 4096 × 2 ns = **8.192 µs total**, which covers only 0.103 τ at
a 50 µs decay. The FAST path is for short pulses; for long tails the vendor uses
Digital RC, where the rise is the second IIR pole in the FPGA. Our shape-RAM
approach drives FAST outside its design domain. `DT_GetShapeMode` is pure managed C# and writes nothing.

---

## 11. Files

| file | purpose |
|---|---|
| `pulser.py` / `pulser_gui.py` | **Pulser mode** — corrected registers, the working path. The GUI is a panel per channel; each runs independently |
| `spectrum.py` | **Energy spectrum mode** — histogram → cumulative → spectrum RAM, plus builders (Gaussian, flat, delta, CSV) |
| `shaperam.py` / `analyse_trace.py` | shape-RAM helper used by `pulser.py`; trace analysis used by the experiments |
| `awg_backend.py` / `awg_gui.py` | **AWG mode** |
| `tworegion.py` | two-region interpolated shape builder (arbitrary rise + long tail) |
| `fastshape.py` | vendor FAST formula, ported verbatim |
| `shaperam.py` | shape-RAM packing helpers |
| `scope.py` | strictly read-only scope access (only `:TRIG:EDGE:LEV` may be written) |
| `analyse_trace.py` | waveform capture and shape characterisation |
| `experiments/` | every experiment run, with `INDEX.md` saying what each one proved and which are confounded |
| `tools/fix_register_addresses.py` | the one-shot address rewriter (already applied) |
| `docs/REGISTER_ADDRESS_BUG.md` | the §2 discovery in full, with evidence |
| `docs/superseded/` | working notes written before that bug was found — kept for their evidence and dead ends; its `README.md` lists which conclusions were disproved |

Scope rule: Ryan drives the scope. `scope.py` enforces it — queries plus
`:TRIG:EDGE:LEV` only, everything else raises.
