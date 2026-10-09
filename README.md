# DT5810B on Linux — consolidated reference

**This file is the entry point and the current, corrected state of knowledge.**
`docs/` holds the longer-form documents: `REGISTER_ADDRESS_BUG.md` (current),
and `docs/superseded/` — the chronological working notes, kept for their
evidence and for the record of what was tried and failed. Several of their early
conclusions were later disproved; each is flagged in §9, and
`docs/superseded/README.md` lists the main ones. Where they disagree with this
file, this file wins.

## 0. Naming — two instruments, never one word for both

| term | means | never call it |
|---|---|---|
| **emulator**, or **DT5810B** | CAEN / Nuclear Instruments DT5810B, USB `21e1:000e`, PDU load 3. Generates the pulse. | ~~board~~ |
| **scope**, or **DHO4804** | Rigol DHO4804, `192.168.2.200:5555`, CH1/CH2 at 1 MΩ. Measures the pulse. | ~~board~~ |
| **PDU** | PADM20 at `192.168.203.29`, powers the emulator | |

**Do not write "board".** It read as either instrument and caused a real
misunderstanding (2026-09-29). The word is gone from this repository; the sole
survivor is the API method `DT5810.board_id()`, kept only because
`../dt5810_gui.py` and `../dt5810_mcp.py` call it.

Note the scope is **DHO**4804 — letter O, "Digital High-resolution
Oscilloscope" — not DH0/zero. Confirmed from `*IDN?`:
`RIGOL TECHNOLOGIES,DHO4804,HDO4A262900576,00.02.13`.

Everything below was measured on this setup, 2026-09-22..29.

---

## 1. Quick start

```bash
~/caen_signal_emulator/pdu/pduOnOff.sh on 3   # power (PDU load 3, outside this repo)
python3 fx3_firmware_loader.py               # 000d -> 000e, volatile firmware
python3 pulser_gui.py        # detector-emulator GUI -- this is the software
python3 scope/monitor.py     # optional live scope read-back (local, see below)
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

After a cold power-up the FX3 firmware is volatile and the emulator comes up at
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

| value | CH0 filter | CH0 analogsel | drives connector |
|---|---|---|---|
| `0x0F` | ON | 1 | **OUT1 HDR** — what we run |
| `0x0B` | **OFF** | 1 | OUT1 HDR, filter removed |
| `0x07` | ON | 0 | **OUT1** (FAST) |

**`invert` is NOT in this register** (it is `0x0f000004`). Two older notes are
wrong about it: `KNOWLEDGE_BASE.md`'s `(range&3)+(invert<<2)+(filter<<3)`, and
`DRC_SESSION_CHECKPOINT.md`'s "0xF=pos, 0xB=neg". The same wrong decode is still
live in `../dt5810.py:343` (the DRC path), which is one more reason that path is
suspect — see §7.

This also corrects something I concluded earlier in this work: writing `0x07`
did not "kill the output" — it switched `analogsel`, i.e. moved the signal to the
**other output connector**. See the next subsection for which connector is which.

`0x0B` removes the 30 MHz filter while keeping the same connector. Earlier I
predicted that takes the rise from 25 ns to ~1 ns; **that prediction was wrong**,
because it assumed we were on the FAST output. We are on HDR, where manual Tab 9.1
gives 42 ns filter-ON and 25 ns filter-OFF — so expect roughly 42 → 25 ns, not
25 → 1 ns. The ~1 ns figure belongs to FAST with the filter off, i.e. `0x03`.
Note the vendor reference captures show a ~10 ns hardware floor with whatever
setting the Windows software uses, which does not fit HDR-with-filter and is
itself unexplained. `experiments/t20_analog_filter.py` is written and ready to run.

### Output connectors — FAST vs HDR

The emulator has **six outputs on the front panel: four analog (two per channel)
and two digital.** Manual §8 Panel Description:

| front-panel label | per channel | type |
|---|---|---|
| OUT1 / OUT2 | Fast analog output | LEMO 00 |
| OUT1 HDR / OUT2 HDR | **H**igh **D**ynamic **R**ange analog output | LEMO 00 |
| GPO1 / GPO2 | digital output, LVCMOS 0–3.3 V | LEMO 00 |

(Inputs, for completeness: GPI1/GPI2 digital, plus one shared analog SE input.)

**HDR is a second analog front-end fed by the same 16-bit DAC and the same
datapath — it trades bandwidth for voltage swing, and is intended for emulating
PMT-like signals.** The two are **mutually exclusive**: one bit per channel
selects which front-end, and therefore which connector, is live. You cannot take
FAST and HDR out of the same channel at the same time.

| | FAST (`OUTn`) | HDR (`OUTn HDR`) |
|---|---|---|
| range | ±2 V @ 50 Ω, ±4 V high-Z | ±8 V high-Z (see caveat below) |
| rise, filter OFF | 1 ns | 25 ns |
| rise, filter ON | 25 ns | 42 ns |
| front-end | CFA op-amp, >2.5 GHz, 4000 V/µs | "more relaxed electronics" |
| manual name | High Speed | High Voltage |

**That bit is `analogsel`.** The vendor C# makes the identification direct —
the 5th argument of `ConfigureGeneral` is literally a cast of the GUI's
Channel Range setting:

```csharp
// DDE-Control.decompiled.cs:12603
PHY.DT_ConfigureGeneral(gAIN, round(a), ...Invert, ...FilterOut,
                        (uint)cfg.Config.Channel[ch].OutputConfiguration.ChannelRange,
                        ConnectionHandle, ch);
// :7438   ConfigureGeneral(double GAIN, int OFFSET, uint INVERT, uint OUTFILTER,
//                          uint ANALOGSEL, int handle, int CHANNEL)
// :7994   enum ChannelRange { V2, V10 }     // V2 = 0, V10 = 1
```

So **`analogsel 0` = `ChannelRange.V2` = High Speed = FAST connector**, and
**`analogsel 1` = `ChannelRange.V10` = High Voltage = HDR connector**. The DLL
writes it both into the `0xF00000C2` mux and to `0xF0000043 + channel`
(Ghidra labelled those two "HP/HR analog sel"; the vendor's own naming is HS/HV).

The conversion code confirms the split all the way up to volts — separate LSB
scales per range, and a factor 2 for the termination:

```csharp
// :8537  LSBToV
if (ichannelRange == ChannelRange.V2) return LSB/2.0 * HS_LSB_V / ImpedanceToDivison(Impedance);
                                      return LSB/2.0 * HV_LSB_V / ImpedanceToDivison(Impedance);
// :8529  ImpedanceToDivison: index 0 -> 0.5 (50 Ω), else 1.0 (High-Z)
```

The `LSB/2.0` here is the other side of our `0x20f005 = LSB × 2` (§3 Energy).

⚠️ **We have been running on HDR this whole time, not FAST.** `dt5810.py:234`
writes `0xF00000C2 = 0xF`, which is `analogsel = 1`. Every calibration in §5 and
every measurement in §9 is therefore an **HDR-output** measurement. This is a
code-reading result, not a bench result — nobody has yet confirmed which LEMO the
scope cable is actually in. **Check that before trusting the pairing**, and if we
want the 1 ns edge the FAST output exists and is one register write away (`0x07`,
or `0x03` for FAST with the filter off).

⚠️ **The HDR voltage is quoted four different ways** and the manual contradicts
itself. Design to ±8 V:

| source | value |
|---|---|
| Table 0.1 operating limits (p.10) | −8 V … +8 V (High-Z) |
| Technical Specifications §2 (p.14) | ±8 V high-Z |
| §8 panel caution (p.21) | "12 V the HDR Output" |
| §9 Analog Outputs (p.26) | "range of 24 V (−12 to +12 V) … at high impedance (1 kΩ)" |
| vendor enum | `ChannelRange.V10` |

Best reading: the stage is physically capable of ±12 V into 1 kΩ, the GUI calls
it 10 V, and the rated/guaranteed figure is ±8 V. Treat anything above 8 V as
untested headroom.

Also from §9: with high impedance, sharp edges can produce multiple reflections,
and CAEN **strongly recommends enabling the 30 MHz filter** when using high-Z —
which is the normal HDR case, and is what `0x0F` already does.

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

**Address verbatim, count = N exactly.** Proven by reading `0xFFFF0000` both
ways and seeing the response shift by one word. The "reads are flaky, retry 4×"
logic in `board_id()` is a symptom of the wrong framing, not a device trait.

The copy of the driver **in this repo** (`dt5810.py`) sends the correct framing
and returns exactly N words. The older copy at `~/caen_signal_emulator/linux/
dt5810.py:186` still sends `(addr-1)` and `(count+2)`; it is left alone because
`dt5810_gui.py` and `dt5810_mcp.py` still import it.

**Config space is write-only.** Every config register returns the repeating
`FF FF BA AB` filler. `0xFFABBAFF` and `0xFFFFABBA` are the *same* filler at
different byte alignments, not two distinct markers. Verification must be done on
the analog output.

**`board_id()` is a red herring** — it never returns `0x1005810B` even on a
perfectly working emulator. Do not use it as a liveness check.

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
| decay | **additive**: program `request − 3.86 µs` | 10/20/50/100/200 µs → +7.8/−3.3/−1.2/−1.3/+0.2 % (§9l) |
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

**AWG** — *secondary; moved to `experiments/` and no longer part of the shipped
software.* Arbitrary waveforms at any rate, 1 Hz to 100 kHz within 0.16% — sine,
square, triangle, sawtooth, sinc, noise, DC. Useful as a bench function
generator and nothing more: it is **not** a detector emulator, because it loops
a fixed array and so has no statistics by construction. Two unsolved defects:
the baseline is pinned at ≈ −0.14 V and will not move, and there are visible
chunk-boundary glitches every ~2048 samples. Run it with
`python3 experiments/awg_gui.py`.

Note that the Pulser shape RAM is a general 4096-sample array —
`tworegion.program()` takes whatever samples it is given, and we only ever feed
it the exponential builder. Arbitrary waveforms *with* triggered statistics are
therefore already reachable on the Pulser path; that would make AWG redundant
even as a function generator, and it is the obvious place to go if the need
arises.

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
emulator) found two real bugs that no amount of reasoning had.

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
— some of those runs were on a partly-wedged DT5810B).

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

Instead of one fixed amplitude per pulse (`EnergyMode 0`), the emulator can draw
each pulse's height from a user-supplied distribution (`EnergyMode 1`). This is
what makes it a *source* emulator rather than a pulser.

### How the hardware does it

Manual §10, "From custom distributions to a set of values": a LUT-SR
pseudo-random generator produces a uniform 32-bit number and the emulator finds
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

## 9g. Correlating the ENERGY between channels (2026-09-28)

§9e shares the *timebase*, so the channels fire together but each draws its own
amplitude. The correlation block has two further modes intended to correlate the
energy as well. One works; one does not.

| mode | `0x2E000000` | CH1 en/tb mux | CH2 en/tb mux | result |
|---|---|---|---|---|
| `CORR_TIMEBASE` | 2 | 0 / 0 | 0 / **1** | time only, energies independent |
| `CORR_SAME` | 1 | 0 / 0 | 0 / 0 | **does not engage** — see below |
| `CORR_CH3` | **8** | **2 / 2** | **2 / 2** | **energies correlated** ✅ |

The mux columns come from `Update_Muxes()`, and as with §9e the mode register
alone does nothing without them. Note the Ch3 mode is register **8**, not 3:
the field is `(mode & 7) | (enable_ch3 << 3)`, and DDE-Control passes
`correlation_mode = 0` with `enableCchannel = 1`.

### `CORR_CH3` — a third generator feeding both outputs

A third internal channel, in register space `ch = 2`, has its own rate and its
own energy (fixed or from a spectrum) and injects the **same event** into CH1
and CH2 together.

```python
p.set_correlated_source(rate_hz=500, amplitude_v=1.0)        # fixed energy
p.set_correlated_source(rate_hz=500, hist=two_peak_spectrum)  # or a spectrum
```

Measured: third channel at 500 Hz / 1.0 V gave **30 of 30** acquisitions with
both outputs carrying it — CH1 mean 1.004 V, CH2 mean 0.985 V, in the same
capture window.

This takes over both channels: every energy and timebase mux goes to 2, so CH1
and CH2 stop using their own generators. Their analog settings and their shape
RAM still apply, so the outputs can still differ in shape and amplitude scale
while carrying the same event.

### How the correlation was proved without a two-channel capture

Reading `VMAX,CHAN1` then `VMAX,CHAN2` does **not** work: the scope re-triggers
between the two queries, so with a spectrum running the samples come from
different events. Doing it anyway gave r = +0.51 — suggestive but weak, and the
weakness is the measurement, not the hardware.

The clean test uses the trigger as a discriminator. Put a **two-peak** source on
the third channel and set CH2's trigger level *between* the peaks, so only
events where CH2 is in the high peak are captured. Then look at CH1:

| CH2 gate | CH1 in the low peak |
|---|---|
| 0.50 V (accepts both) | 38 % |
| 0.90 V (accepts only CH2-high) | **0 %** |

Gating CH2 high removed CH1's low peak entirely. The same test under
`CORR_TIMEBASE` left CH1 at 36 % low — both peaks intact. That is the
distinction, measured: **Ch3 correlates the energy, shared-timebase does not.**

### `CORR_SAME` — unresolved

"Channel 2 follows exactly channel 1" (register 1, all muxes 0, exactly what
`Update_Muxes()` does) does not engage. Both outputs drop to a very low
effective rate and the scope stops triggering even with the level at 0.15 V.

The suggestive detail: while this happens the two channels' amplitude ranges
track each other almost exactly (0.068–0.501 V against 0.071–0.502 V, identical
means), which hints the replication itself is working and it is the firing rate
that collapses. That is a hypothesis, not a result.

Tried and did not help: CH2 with fixed energy instead of a spectrum, CH2
`tb_mux = 1`, CH2 `en_mux = 1`, both muxes 1. Something is still missing, the
way the timebase mux was for `CORR_TIMEBASE`. Since `CORR_CH3` delivers energy
correlation and is verified, this was not chased further.

---

## 9h. What channel 3 can do (2026-09-28)

The third generator lives in register space **`ch = 2`** — the same space the
correlation registers sit in — and has the full per-channel register set. Its
output is not a connector: it reaches the outside world *through* CH1 and CH2.

| capability | status |
|---|---|
| own timebase, constant rate | ✅ verified 50 Hz – 1 kHz |
| own timebase, **Poisson** | ✅ but needs the LFSR strobe below |
| own energy, fixed | ✅ |
| own energy, **full 16384-bin spectrum** | ✅ |
| injects the same event into CH1 **and** CH2 | ✅ 30/30 acquisitions |
| energies correlated between channels | ✅ 38 % → 0 % discriminator test (§9g) |
| CH1/CH2 keep their own background events | ✅ see below |
| CH2 delay applies to ch3 events | ❌ no effect |
| channel 3 has its **own shape generator** | ✅ the vendor programs one |
| channel 3 has its **own noise generator** | ✅ the vendor's Noise form is unrestricted for it |
| channel 3 has its own baseline drift | ✅ programmed by the vendor; drift itself unexplored |
| which shaper renders a ch3 event | ❓ still unverified |
| whether ch3 noise/baseline reach the outputs | ❓ not yet measured (emulator down) |

```python
p.set_correlated_source(rate_hz=500, amplitude_v=1.0)          # fixed
p.set_correlated_source(rate_hz=500, hist=spec, poisson=True)  # spectrum + Poisson
```

In the GUI this is the **Correlation** selector set to "Coincidence — channel 3
injects into both", which reveals the third channel's rate, amplitude, peak
width and Poisson controls. The CH2 delay box greys out there, because the
delay has no effect on ch3 events. Verified through the GUI: 25/25 acquisitions
with the event on both outputs.

### Backgrounds coexist with the correlated events

This is the arrangement the manual's Fig 9.4 describes, and it does work: CH1
and CH2 keep emitting their *own* uncorrelated events while ch3 injects a
correlated one into both. With CH1 and CH2 each at 0.4 V and ch3 at 1.0 V, and
CH1 detuned 0.5 % so its phase walks through the window:

| | CH1 own 0.4 V band | ch3 1.0 V band |
|---|---|---|
| correlation off | 5 | 0 |
| `CORR_CH3` | 5 | 7 |

The background rate is untouched; the correlated events are added on top. An
earlier run concluded the opposite — the trigger was at 0.6 V, which filtered
the 0.4 V background out, and CH1 was phase-locked outside the window. Both
artifacts at once, and the wrong conclusion looked clean.

### The CH2 delay does not reach ch3 events

`delay_ns` shifts CH2 in `CORR_TIMEBASE` (§9e) but has **no effect** on ch3
events: 0, 500, 1500 and 3000 ns all measured the same ~−47 ns skew, with CH2
*leading* CH1 by that fixed amount. The delay buffer sits in CH2's own
datapath, not in the third channel's fan-out. So the coincidence pair arrives
with a fixed offset that cannot currently be programmed — worth knowing if you
want a time-of-flight spread.

### Two things not established

**Which shaper renders a channel-3 event.** Not resolved: the measured decay
did not track the setting (2 µs read 6.6 µs, and 8/20 µs found no pulse at
all). The measurement is the suspect part — a 20 µs scope window is too short
for the longer decays. Still owed.

What *is* settled is that **channel 3 has a shape generator of its own**, and
the vendor programs it. `DDE-Control` allocates
`ChannelConfiguration[NChannels + 1]` — three channels for a two-channel emulator
— and its configuration loop runs `Update_Generals`, `Update_Energy`,
`Update_Shape`, `Update_Shape_Custom` and `Update_Timebase` over all three,
channel 2 included. The `ReducedChannel` flag it sets on channel 2 strips only
the *Sequence* modes; it does not touch the shape.

So channel 3 takes rate, energy **and** shape, and `set_correlated_source`
accepts `rise_us` / `decay_us` to program its shape RAM at `ch = 2`. An earlier
version of this document and of the GUI treated channel 3 as shapeless, which
was an assumption drawn from the inconclusive measurement above rather than
from the vendor code — the vendor code says otherwise.

**Noise and baseline as well.** The same loop runs `Update_Noise`,
`Update_Noise_Interference`, `Update_Baseline`, `Update_BaselinePoints` and
`Update_Random` over channel 2. Of the whole vendor UI only three forms check
`ReducedChannel` — `EnergyCtrl`, `TimeCtrl` and `RandomCtrl` — and all they do
is remove the *Sequence* options. The Noise and Baseline forms do not check it
at all, so channel 3 gets them unrestricted.

This is a real distinction, not a curiosity: noise injected at channel 3 is
**common-mode**, landing on both outputs together, where the noise set on the
CH1 and CH2 panels is independent per channel. For coincidence work that is the
difference between correlated and uncorrelated noise on the pair.

`set_correlated_source(noise_mv=...)` programs it, and the GUI exposes a Noise
row on the channel-3 panel. **Neither is verified on hardware** — the emulator
froze before it could be measured. Baseline *drift* (`ConfigureBaselineDrift`)
remains unexplored for every channel, not just this one.

What stays off the channel-3 panel is the baseline DC level and the polarity:
those act on the physical output stage, and the vendor's `UpdateCalibration`
calibrates only channels 0 and 1, so there is no third stage for them to reach.

**Poisson statistics.** Poisson mode now *emits* at the right average rate, but
the interval distribution was never verified to be exponential. The scope, with
one pulse per 20 µs window, cannot measure inter-pulse intervals.

---

## 9i. ⭐ Poisson mode never worked — the timebase LFSR was never started

Found while characterising ch3, and it applies to **every** channel.

`ConfigureLFSR` @`0x10006da0` maps `LFSR_TIMEBASE` to a strobe of
**`0x100004`** (1 then 0). We strobe the *energy* LFSR at `0x20f002` and always
have, but never this one. Without it the Poisson generator never starts and the
channel emits **nothing at all** — not a wrong rate, no output:

| | pulses seen | trigger |
|---|---|---|
| constant rate | 30/30 | TD |
| Poisson, no strobe | **0/30** | AUTO |
| Poisson + strobe | 30/30 | TD |

Measured on CH2, which is the scope's trigger source, so this is direct rather
than inferred. `pulser.py` now strobes `R_TB_LFSR` whenever Poisson is selected,
on both the per-channel path and the third channel.

This had been silently broken for the whole project, and `pulser_gui.py` has
offered a Poisson timebase the entire time. It went unnoticed because nothing
ever tested it against the scope: the mode was selected, no pulses came out, and
that is indistinguishable from the many other "no output" states seen along the
way. Same lesson as §9e and §9f — for this DT5810B, setting the mode register is
never the whole story.

---

## 9j. Baseline noise (2026-09-28)

`ConfigureNOISE` @`0x1000b3e0` takes
`(RANDM, GAUSS, DRIFTM, FLIKERM, FLIKERCorner, handle, ch)` — four independent
generators, each an amplitude with 0 = off, plus a corner frequency for the
flicker one. `Update_Noise()` just reads the four amplitudes out of the config
and passes them straight through.

### Finding the registers

Unlike every other function decoded here, this one **does not push its register
addresses as immediates** — they are computed — and `objdump` loses sync partway
through because the body builds ~3.5 KB of flicker filter coefficients on the
stack. The route that worked:

1. parse the PE section table to map VA `0x10005fc0` to its file offset
2. scan the 3545-byte function body for any 4-byte value shaped like a register
3. ignore the coefficient noise and look for a **cluster of consecutive
   addresses** — `0x1400000/4/5/7/8/9` stood out, and `0x1400009` was the
   already-known `LFSR_NOISE_GAUSS` strobe, which confirmed the family
4. write a large magnitude to each candidate and watch the scope baseline

Step 4 is what actually settled it; steps 1–3 only produced the shortlist.

### What was found

| register | effect | scale |
|---|---|---|
| `0x1400000` | broadband noise, `LFSR_NOISE_GAUSS` family | **3.55 µV rms/count**, to 232 mV |
| `0x1700002` | broadband noise, stronger | **10.68 µV rms/count**, to ~700 mV |
| `0x1400004/5/7/8`, `0x15/16/18/19/1a xxxxx` offsets 0–5 | nothing | — |

Both are **broadband, not drift**: the sample-to-sample difference measured
~1.65 × the standard deviation, close to the √2 of uncorrelated noise. Neither
behaves like a slow wander, so the random-walk and flicker generators are
presumably the two that were not found.

`0x1400000` is linear to better than 5 % across the whole range. The emulator has
an intrinsic **~20 mV rms floor** that adds in quadrature:

| asked | predicted with floor | measured |
|---|---|---|
| 0 mV | 20.1 | 19.9 |
| 30 mV | 36.1 | 36.6 |
| 60 mV | 63.3 | 63.7 |
| 120 mV | 121.7 | 121.5 |
| 200 mV | 201.0 | 200.7 |

```python
p.set_noise(60, ch=0)          # 60 mV rms of baseline noise
p.set_noise(60, ch=0, generator='b')   # the coarser, stronger source
p.noise_off(ch=0)
```

---

## 9k. Coverage against the Windows software

Everything `DDE3.dll` exports in the `Configure*` family, and what we do with
it. This is the map of what is left.

| vendor entry point | status |
|---|---|
| `ConfigureTimebase` | ✅ rate, Poisson, dead time, paralyzable (§9i) |
| `ConfigureEnergy` | ✅ fixed amplitude |
| `ProgramSpectrum` | ✅ full 16384-bin spectra (§9f) |
| `ConfigureShapeGenerator` | ✅ arbitrary rise + decay, two-region interpolator (§9b/9c) |
| `DelayAndCorrelationControl` | ✅ shared timebase + delay (§9e), Ch3 coincidence (§9g/9h); `CORR_SAME` unresolved |
| `ConfigureLFSR` | ✅ energy, timebase and gauss-noise strobes |
| `ConfigureNOISE` | ⚠️ 2 of 4 generators found (§9j); random-walk and flicker not located |
| `ConfigureGeneral` | ✅ gain, offset, invert, analog mux |
| `EmulatorAWGModeControl` / `ProgramDDR` | ✅ AWG mode (secondary, §6) |
| `ConfigureDRC` | ❌ produces no output; clean negative, re-proved after the address fix |
| `ConfigureBaselineDrift` | ❌ **not explored** — `(nodes[], length, interp_slow, interp_fast, reconfigure, enable, reset)`, a node-interpolated baseline wander |
| `ConfigureMultishape` | ❌ **not explored** — `(prob2, prob3, prob4, enable)`, picks among shape slots by probability. We program all 16 slots identically, so this is free capability |
| `ConfigureTR` | ❌ **not explored** — pulsed/transistor reset, and `FEAT_PULSED_RESET` *is* declared for this DT5810B |
| sequence modes | ❌ **not explored** — `EnergyMode 2` / `TimeMode 2`, with `FEAT_SEQUENCE_AMP` and `FEAT_SEQUENCE_TIME` declared |
| `ConfigureDIO` / `SetDIO` | ❌ not explored — digital I/O and external trigger (`TimebaseMux = 4`) |
| `GetSignalLoopback` | ❌ not explored — **would read the generated signal back over USB**, i.e. verification without the scope |
| `DPP_*`, `MCA_ReadPreview` | ❌ not explored — the emulator can *digitise* an input (`FEAT_ANALOG_IN`) |
| HV channel | ❌ not explored (`FEAT_HVCH`) |
| flash / activation / security | ❌ deliberately untouched |

Two of these look worth doing next: **`ConfigureMultishape`**, because the shape
slots are already programmed and only the selection probabilities are missing,
and **`GetSignalLoopback`**, because reading the generated waveform back over
USB would remove the scope from the verification loop entirely — and most of the
wrong turns in this project were scope artifacts.

---

## 9l. The decay correction was never connected (2026-09-30)

Two faults, one masking the other.

**`compensate_decay` did nothing.** `_apply()` computed `req_decay` and then
built the shape with `tworegion.build(rise_us, decay_us)` — the *raw* request.
Only the legacy bare-exponential branch used `req_decay`, and nothing uses that
branch. So `DECAY_SCALE` had no effect on any pulse this software has produced.

**And the correction was the wrong shape anyway.** Sweeping the request and
fitting each whole trace:

| requested | 10 | 20 | 50 | 100 | 200 µs |
|---|---|---|---|---|---|
| measured | 15.06 | 23.55 | 53.60 | 102.76 | 204.74 µs |

Least squares gives **measured = 1.001 × requested + 3.86 µs**. The slope is
unity to 0.1 %: the error is a constant **additive** offset, not a scale
factor. `DECAY_SCALE = 1.048` was fitted at 50 µs alone, where a 4 µs offset
does look like a 1.08 multiplier — but it is badly wrong elsewhere, and a 10 µs
request came out at 15 µs, a 50 % error.

Fixed by subtracting `DECAY_OFFSET_US = 3.86` before programming, and by
passing `req_decay` to the two-region builder. Verified:

| requested | 10 | 20 | 50 | 100 | 200 µs |
|---|---|---|---|---|---|
| measured | 10.78 | 19.34 | **49.39** | 98.70 | 200.39 µs |
| error | +7.8 % | −3.3 % | −1.2 % | −1.3 % | +0.2 % |

Worst case 7.8 % at the short end, against 50 % before.

**This also settles the 52.9 vs 49.4 µs disagreement** in
`docs/letsClaw_finding.md` §7 and §10.2. Both numbers were right: 52.9–53.6 µs
is what an *uncompensated* 50 µs request produces, which is what the software
was actually doing, and 49.4 µs is what it produces once the compensation is
connected. The measurements never conflicted — the code did not do what both
documents said it did.

Measurement method throughout: 0.5 V/div (1 V needs ≥ 1/4.34 div), timebase
from `zoom.timebase(goal="decay")`, clipping confirmed absent from the ADC
codes, and the decay taken from a 5-parameter whole-trace fit rather than a
1/e crossing — all per `docs/letsClaw_finding.md` §3-§6.

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

**The repository is the emulator control software and nothing else.** Everything
tracked below drives the DT5810B. Bench apparatus and lab scratch work live
beside it on disk but are gitignored, and are marked *(local)*.

### The software

| file | purpose |
|---|---|
| `dt5810.py` | **USB driver** — the low-level layer everything sits on. Corrected read framing; see §4 |
| `fx3_firmware_loader.py` | loads the volatile FX3 firmware, `000d` → `000e`, needed after every cold power-up |
| `pulser.py` | **Pulser mode** — the working detector-emulator path, with the corrected registers |
| `pulser_gui.py` | PyQt6 front end. Per channel: rate, amplitude, energy mode (fixed / Gaussian / two peaks / continuum / CSV), rise, decay, baseline, **noise**, polarity, dead time. Across both: a **Correlation** selector — off, shared timebase with CH2 delay, or **coincidence via channel 3** with its own rate, amplitude, peak width and Poisson |
| `tworegion.py` | two-region interpolated shape builder — arbitrary rise with a long tail |
| `spectrum.py` | **energy spectrum mode** — histogram → cumulative → spectrum RAM, plus builders (Gaussian, flat, delta, CSV) |
| `shaperam.py` | shape-RAM packing helpers, used by `pulser.py` |
| `tools/fix_register_addresses.py` | the one-shot address rewriter (already applied) |

### Documentation and evidence

| path | purpose |
|---|---|
| `AGENTS.md` | **read first** — working rules, what lives outside this repo, and the verification loop |
| `README.md` | this file — the authoritative reference |
| `docs/REGISTER_ADDRESS_BUG.md` | the §2 discovery in full, with the disassembly evidence |
| `docs/letsClaw_finding.md` | scope control and pulse fitting (2026-09-29/30): write whitelist, measured scope geometry, edge-vs-decay framing, ADC-code clipping check, the 5-parameter pulse fit, and the amplitude dead-band below ~0.3 V |
| `docs/superseded/` | working notes written before that bug was found; its `README.md` lists which conclusions were disproved |
| `reference/` | vendor traces captured from the Windows software — measured ground truth for §9b |

### Not in the repo *(local to the lab machine)*

| path | purpose |
|---|---|
| `scope/` | Rigol DHO4804 access — `scope.py` (strictly read-only), `analyse_trace.py`, and `monitor.py`, a standalone live read-back window to run alongside the GUI. Every calibration here was measured through it, but it is apparatus, not product, and its IP is hardcoded |
| `experiments/` | the 26 numbered experiments behind §9, the superseded deliverables, and **AWG mode** (`awg_gui.py`, `awg_backend.py` — see §6). `INDEX.md` records what each proved and which are confounded |
| `backup_pre_addrfix/` | pre-fix source snapshot, superseded by git history |
| `../WEB_UM5312_DT5810_Fast_Digital_Detector_Emulator_v5.pdf` | **the CAEN manual.** Every "manual §N" / "Tab 9.1" citation in this file refers to it. Not vendored (7.8 MB, CAEN copyright) |

`pulser_gui.py` does not import `scope/` at all: it controls the emulator and
nothing else. The read-back that used to be a "poll scope" checkbox inside it is
now `scope/monitor.py`, a separate window you run alongside. A checkout of this
repository therefore has no scope dependency, and the scope's IP is no longer
baked into the GUI.

Scope rule: Ryan drives the scope. `scope/scope.py` enforces it in code —
queries plus `:TRIG:EDGE:LEV` only, everything else raises `PermissionError`.
