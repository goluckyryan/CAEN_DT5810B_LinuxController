# Scope control and pulse fitting — session findings, 2026-09-29/30

Written for the next agent. Everything here was measured on the bench in one
session; nothing is inherited from the earlier notes. Where it contradicts
`../README.md`, this file is newer — but read §9, some of it contradicts *me*.

Naming, per `../README.md` §0: **emulator** = DT5810B, **scope** = Rigol
DHO4804. Never "board".

---

## 1. What changed in the repository

| file | status | what it is |
|---|---|---|
| `scope/scope.py` | **modified** | write whitelist widened from 1 to 3 commands |
| `scope/plan.py` | **new** | settings calculator, sends nothing |
| `scope/zoom.py` | **new** | coarse→fine ladder + clipping check from ADC codes |
| `scope/fitpulse.py` | **new** | 5-parameter shaped-exponential fit of the whole trace |
| everything else | prose only | "board" → "emulator"/"DT5810B", 42 occurrences |

`scope/` is gitignored, so the three new files are **local to the lab machine**
and will not appear in a clone. That is deliberate and consistent with the
existing policy (`../README.md` §11: the scope is apparatus, not product).

`git status` at time of writing: 10 modified tracked files (all prose renames)
plus this document untracked, and **nothing committed**. The rename and the
scope work are independent; commit them separately.

---

## 2. The scope write restriction — lifted, narrowly

It used to be query-only plus `:TRIG:EDGE:LEV`. Ryan authorised two more on
2026-09-29 **after** the settings were derived from evidence, not before.

```python
_ALLOWED_WRITES = (':TRIG:EDGE:LEV', ':CHAN1:SCAL', ':CHAN2:SCAL',
                   ':CHAN3:SCAL', ':CHAN4:SCAL', ':TIM:MAIN:SCAL')
```

Values are checked against the 1-2-5 ladder before sending: an off-ladder value
raises `ValueError` rather than letting the instrument silently round it, which
would make the read-back disagree with the request.

**Still refused, and each for a reason:**

* `:CHAN:OFFS` — the sign convention has never been established here. No
  capture in `reference/` used a non-zero offset, and determining it requires a
  write. Guessing wrong throws the trace off-screen *the other way*, i.e. it
  recreates the exact failure the module exists to prevent.
* `:WAV:*` — an earlier version of `scope.py` sent `:WAV:SOUR/:MODE/:FORM` and
  broke waveform capture. Capture stays query-only.
* `:RUN :STOP :SING :AUT :CHAN:DISP :TRIG:SWE` — these change what the
  instrument is *doing*, not how it is framed.

**Do not widen this further without asking.** The restriction is Ryan's and it
has already caught one real mistake.

---

## 3. Scope geometry — measured, not assumed

Read back from the DHO4804 and cross-checked against all 8 traces in
`reference/`:

| quantity | value | how it was established |
|---|---|---|
| codes per division | **29.3** | 8 bits over ~8.74 div |
| ADC rail | **±4.34 div**, codes 0 and 255 | `yinc`×128 vs `:CHAN:SCAL` |
| quantisation | (V/div)/29.3 | 3.413 mV at 0.1 V/div, matches every reference trace |
| record length | **1000 points** in NORM | `:WAV:POIN`, independent of timebase |
| sample spacing | (s/div)/100 | 10 div ÷ 1000 points |
| origin | 0 V at **code 128**, t=0 at **sample 500** | verified against live samples |

**The rail is ±4.34 div, not the ±4 the graticule suggests.** This matters and
it bit me twice (§9).

**The origin is centred and stays centred.** `:CHAN1:OFFS`, `:CHAN2:OFFS` and
`:TIM:MAIN:OFFS` all read 0, and they survive every scale change — verified
across five V/div × s/div combinations. This is structural, not luck: neither
offset command is on the whitelist, so they cannot move by accident.

**Consequence of a centred origin:** a unipolar pulse sits on ground and only
goes up, so it has **half the screen — 4.34 div, not 8.71**. The rule is

```
V/div  >=  amplitude / 4.34
```

For a 1 V pulse the finest legal stop is 0.5 V/div. 0.2 V/div rails.

---

## 4. Choosing the settings (Ryan's rule)

Frame the feature you are actually looking at. **You cannot see the edge and
the tail at once — take two captures.**

| looking at | s/div |
|---|---|
| rising edge | = the rise time (edge fills ~1 division) |
| decay | = decay/2.5, i.e. ~4 τ on screen |
| rate | a few periods across the window |

For 1 µs rise on a 50 µs tail: **1 µs/div** for the edge, **20 µs/div** for the
decay. Ryan later preferred **50 µs/div** for a 50 µs decay — 10 τ, peak
mid-screen, 499 tail samples — and that framing measures well (§6).

Vertical: start at amplitude/1 div (a 1 V pulse → 1 V/div, which cannot
overflow), then step finer and **check the trace after each step**. Stop at the
last setting that fits.

Trigger at half amplitude: far from the baseline (which moves with gain and
offset) and from the peak (which varies per-pulse in spectrum mode). Use
`:TRIG:SWE NORM` so a dead output shows as a stale screen rather than a
misleading auto-triggered flatline — *note this is not settable from code, the
sweep command is not whitelisted.*

**Counting samples is not a separate concern.** 1000 points over 10 divisions
means one division ≈ 100 samples, so anything framed to about a division is
oversampled by construction. An earlier draft of `plan.py` carried a separate
"≥40 samples per edge" criterion; it can never bind once the division rule is
applied, and it has been removed.

---

## 5. Detecting a bad frame from the trace — do this, do not trust arithmetic

`scope/zoom.py:check()` reads the trace and converts volts back to ADC codes
via the `:WAV:PRE` block, then counts samples sitting on **code 0 or 255**.

```
0.05 V/div, 2 us/div, trig TD
codes 200..255   span 1.87 div   rails: 0 low / 499 high
-> CLIPPED (499 samples on the top rail) -- go COARSER, 0.1 V/div
```

Code-based, not volts-based. **A volts-vs-graticule test called a hard-railed
trace "ok"** while 499 of its 1000 samples were pinned at code 255, because the
data sat between the ±4 graticule and the ±4.34 rail. Use the codes.

This is also the answer to "is the emulator dead or is the scope wrong" — see
§9, where getting that backwards produced a false alarm.

---

## 6. Fitting the trace

**Model** (Ryan's, and the same one the emulator builds — manual sec 12
"Shaped Exponential", `shaperam.shaped_exponential()`):

```
y(t) = offset                                                    t <= t0
y(t) = offset + A e^{-(t-t0)/decay} (1 - e^{-(t-t0)/rise})       t >  t0
```

Five free parameters `A, decay, rise, offset, t0`, fitted to **all 1000
samples** by nonlinear least squares (`scipy.optimize.curve_fit`, TRF with
bounds). `t0` is fitted, not assumed — the trigger point is not the pulse start.
Implemented in `scope/fitpulse.py`; `numpy` and `scipy` are both present.

Validated against synthetic data with known parameters plus 4 mV noise:
recovers decay and amplitude to ~0.1%.

**The peak is not A.** Because `(1-e^{-t/rise})` has not reached 1 when the
decay has started, the maximum is

```
y_pk / A = (d/(d+r)) * (r/(d+r))^(r/d)
```

99.3% for rise 76 ns / decay 53 µs, and much lower for slower rises. Any method
that reads A off the peak sample is biased low.

**Each parameter needs its own framing.** Same pulse, one trace per row:

| s/div | dt | decay (µs) | rise (ns) |
|---|---|---|---|
| 0.1 µ | 1 ns | 93.7 ± 135 | **76.4 ± 1.2** |
| 0.5 µ | 5 ns | 80.0 ± 11.2 | 77.6 ± 1.5 |
| 2 µ | 20 ns | 55.3 ± 1.2 | 81.5 ± 2.4 |
| 10 µ | 100 ns | **52.9 ± 0.29** | 142.5 ± 6.5 |
| 50 µ | 500 ns | 53.8 ± 0.30 | 102.3 ± 42.8 |

The quoted errors are the fit covariance and they are honest: at 0.1 µs/div the
window is 1 µs so the decay is pure extrapolation; at 50 µs/div one sample is
500 ns so the rise is unresolved. **Take rise from ~0.1 µs/div and decay from
~10 µs/div, and disbelieve each from the other's view.**

The rise trending 76 → 142 ns as the timebase coarsens is sampling and the
analog path smearing the edge, not a real change.

### `analyse_trace.fit_tau()` is broken — separate pre-existing bug

It returns `None` on ordinary traces at 50 µs/div. Its tail loop breaks the
moment a sample rises by >2% of amplitude; noise triggers that after 11
samples, one short of its `>= 12` minimum. It also takes `base = min(v)`, the
noise floor rather than the baseline. **Not caused by this session's changes.**
Point it at `fitpulse.fit()` or fix the cut.

---

## 7. Measurements of the emulator

Reference pulse: 1 kHz, 1 V, 0.1 µs rise, 50 µs decay, 0 V baseline.

| quantity | requested | measured | note |
|---|---|---|---|
| amplitude | 1.0 V | VPP **1.016 V** | +1.6%, clean frame, zero railed samples |
| A (fitted) | 1.0 V | **0.963 ± 0.011 V** | peak reads 0.995 V |
| decay | 50 µs | **52.9 ± 0.3 µs** | ~6% long |
| rise (τ) | 0.1 µs | **76 ± 1 ns** → 10-90 ≈ 168 ns | |
| rate | 200 kHz | **200 200 Hz** | +0.1%, verified separately |

`DECAY_SCALE = 1.048` divides the request to 47.7 µs before programming, so the
shape RAM is asked for 47.7 and delivers ~53 — consistent with the ~1.16×
geometry stretch `tworegion.py` already documents for the rise.

`../README.md` §6 claims 49.4 µs. **52.9 µs is the better number** — it is a
whole-trace fit with A fitted rather than read off the peak — but the two were
measured on different days and the discrepancy is not resolved.

### Amplitude dead-band below ~0.3 V — undocumented, worth chasing

At a fixed, verified-good 0.2 V/div frame:

| requested | measured VPP |
|---|---|
| 0.15 V | 0.022 V |
| 0.20 V | 0.022 V |
| 0.25 V | 0.026 V |
| **0.30 V** | **0.258 V** |
| 0.50 V | 0.461 V |

Nothing below ~0.3 V. Consistent with `../README.md` §5, which gives the
amplitude calibration as linear only over energy_reg 4000→30000: 0.1 V needs
reg 1069, far below the floor. **`auto_gain()` accepts 0.1 V and returns a
silent nothing** — no warning, no exception. That is a real usability bug.

---

## 8. Emulator power and recovery

* `pdu/pduOnOff.sh on 3` — load 3 is the emulator. Enumerates in 1-2 s at
  `21e1:000d`, then `python3 fx3_firmware_loader.py` → `000e`. Firmware is
  volatile; this is needed after every cold power-up.
* The firmware image is **not** in the repo; the loader falls back to
  `~/caen_signal_emulator/linux/firmware/dt5810usb.img`.
* A **12 s** off period is needed for a genuinely cold cycle. The script's
  `cycle` sleeps 1 s, which is too short — the FX3 keeps power and comes back at
  `000e` instead of `000d`. Do `off 3`, sleep 12, `on 3`, and confirm it returns
  at `000d`; that is the proof it actually power-cycled.
* **The emulator dropped off USB entirely once mid-session** (not `000d`, not
  `000e` — absent from `lsusb`). `on 3` brought it straight back, so it had
  lost power. Cause unknown. If a script suddenly reports "DT5810B not found",
  check `lsusb` before assuming a software fault.

---

## 9. Mistakes made this session — read this part

Four, all of the same kind: **trusting arithmetic over the samples.**

1. **False "EMULATOR WEDGED".** `assert_alive()` reported the emulator wedged,
   twice. It was fine. The scope was at 0.05 V/div — leftover from the vendor's
   0.215 V pulse — so a 1 V pulse railed and every VPP query returned ~9 mV of
   clipped baseline. I burned a full 12 s cold power-cycle on this before
   proving it was the scope. *This is `../README.md` trap #5, logged, and walked
   into anyway.*

2. **Used ±4 div when the rail is ±4.34.** Made a volts-based clip test report
   "ok" on a trace with 499 railed samples.

3. **Then used 8 div for the ladder when a centred origin gives only 4.34.**
   `zoom.py` recommended 0.2 V/div for a 1 V pulse; the hardware railed 385
   samples. Same mistake as #2, two functions later. Fixed — the ladder now uses
   `RAIL_DIVS`.

4. **Reported decay as 36.6 µs.** Wrong twice over: a single 1/e crossing is a
   one-sample estimator that scattered 48.8→53.0 µs on an unchanged signal, and
   the baseline was taken from `v[-100:]`, which at 20 µs/div is still on the
   decaying tail. Then reported ~55 µs from a tail-only log-linear fit with A
   read off the peak. The whole-trace fit gives **52.9 µs**. I flagged a number
   that contradicted the documentation without first re-measuring it.

**The pattern, and the lesson for whoever is next:** every one of these was
caught by reading actual samples, and every one was *caused* by computing
instead of reading. The emulator also breaks prediction independently — the
vendor makes 0.215 V where the calibration predicts 0.105 V (`reference/README.md`,
"a clean factor of two"). Measure, then measure the thing you used to measure.

One process note: `analyse_trace.py` already had a `fit_tau()` and I wrote my
own without looking first. Check what exists before adding.

---

## 10. Open, in the order worth doing

> **Update 2026-09-30 (next agent).** Items 1-3 are done; see `../README.md`
> §9l for the decay work. Item 2 turned out to be a code fault rather than a
> measurement disagreement: `compensate_decay` was never wired into the
> two-region path, so `DECAY_SCALE` had no effect on anything, and the true
> error is a constant **+3.86 µs additive offset**, not a 1.048 multiplier.
> Both numbers in §7 were right — 52.9 µs uncompensated, 49.4 µs once the
> compensation actually runs. Items 4-6 are still open.
>
> Also corrected: the "emulator wedged" report from the session before this one
> was the same false alarm as §9.1. The trigger level had been left *below* the
> baseline so the scope never triggered, and medians over random AUTO captures
> erased a 4 %-duty signal. §9's lesson, walked into a third time.


1. **`auto_gain()` silently returns nothing below ~0.3 V.** Should warn or
   raise. §7.
2. **Decay 52.9 µs vs `../README.md`'s 49.4 µs vs 50 µs requested.** Needs one
   careful re-measure to settle, with `DECAY_SCALE` in view.
3. **`analyse_trace.fit_tau()` returns `None` on real traces.** §6.
4. **The edge timebase keys off the *requested* rise, not the measured one.**
   It worked for 0.1 µs (137 ns actual → 1.37 div) because the rule tolerates a
   40% error, but `tworegion.py` says the geometry runs 1.16-1.79× long, so the
   input is known-wrong by design. Read RTIM once coarse, then reframe.
5. **`assert_alive()` should call `zoom.check()` first** and refuse to rule on
   wedged-vs-alive while the trace is railed. This is the actual fix for §9.1
   and it needs no scope writes.
6. **`:CHAN:OFFS` sign convention** is still unestablished. Settling it would
   give a unipolar pulse the full 8.7 divisions instead of 4.34. Needs one
   authorised write and one read-back.
