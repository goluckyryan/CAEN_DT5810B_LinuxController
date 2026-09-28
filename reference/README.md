# Vendor reference captures — Windows DDE-Control driving the board

Captured 2026-09-24 with the emulator driven by the **Windows** software, so these
are ground truth for what the instrument can actually do. Our Linux implementation
is measured against them.

Common settings: **LSB 1234, 0.1 kHz, 50 µs decay**, CH1 at 1 MΩ.
Only the rise time was varied. Each file holds the full trace (`t`, `v`) plus the
scope settings and its own measurements.

## Rise-time series

| requested | scope RTIM | my 10-90 | amplitude | file |
|---|---|---|---|---|
| 0 µs | 10.2 ns | 11.0 ns | 0.2146 V | `..._0rise_...json` |
| 0.01 µs | 13.2 ns | 15.5 ns | 0.2141 V | `..._10nsrise_...json` |
| 0.03 µs | 31.0 ns | 34.0 ns | 0.2139 V | `..._30nsrise_...json` |
| 0.05 µs | 48.2 ns | 54.5 ns | 0.2139 V | `..._50nsrise_...json` |
| 0.1 µs | 90.5 ns | 104.5 ns | 0.2113 V | `..._100nsrise_...json` |
| 0.5 µs | 441.5 ns | 490.0 ns | 0.2135 V | `..._500nsrise_...json` |
| 0.6 µs | 510.0 ns | 560.0 ns | 0.2115 V | `..._600nsrise_...json` |
| 1 µs | 1000.0 ns | 920.0 ns | 0.2063 V | `..._1000nsrise_...json` |

## What these support — and what they do not

**Measurement precision is ±10-15%.** The scope's own RTIM and a 10-90 fit off the
same raw samples disagree by 7-17% on every single trace. Worse, the 600 ns point
read 652 ns at 2 µs/div and 510 ns at 1 µs/div — a 22% swing from the timebase
alone. So:

* ✅ **Hardware rise floor ≈ 10 ns** (a 5× effect, well outside the error)
* ✅ **Roughly linear, slope ≈ 1**, from 30 ns to 1 µs
* ✅ **Amplitude independent of rise** to ~4%
* ❌ Any finer structure in the ratio column is **noise, not behaviour**

Three different models were fitted to this curve during the session (quadrature
with a floor; a monotonic droop; unity-slope linear) before the precision was
established. All three were over-reading scatter. Don't repeat that — establish
the error bar first.

Rule of thumb that emerged: **keep the feature under ~10% of the window.** The two
bad captures in the series were at 38% and 18%.

## The comparison that matters

| requested | vendor | ours, before | ours, after the geometry fixes |
|---|---|---|---|
| 100 ns | 90.5 ns | 102 ns | 179 ns |
| 200 ns | — | — | 350 ns |
| 500 ns | 441.5 ns | 482 ns | 836 ns |
| 1000 ns | 1000.0 ns | 978 ns | 1414 ns |
| 2000 ns | — | — | 2329 ns |

The "after" column is worse in magnitude but the SHAPE is now correct and the
peak kink is gone (confirmed on the scope 2026-09-25). Rise runs 1.16-1.79x long
and needs a scale trim — a one-parameter fit, not a structural problem.

**These captures did their job.** Comparing our edge against them found two real
bugs (see `../README.md` §9b): we were rendering only half the rise curve, and
`0x50f005` is a 7-bit field that wrapped above corner 127. The 9× failure at 30 ns
is gone, and the normalised edge shape now matches the vendor to rms 0.04 (was 0.23).

Remaining gap: ~20% long at 100-200 ns where the vendor is ~10% short, and we floor
at ~32 ns where the vendor reaches 31 ns.

Also unexplained: the vendor makes **0.215 V from LSB 1234**. Our calibration
(`V = 3.414e-5 × reg + 0.0205` at gain 1184, with reg = LSB × 2 = 2468) predicts
**0.105 V** — a clean factor of two. Either the Windows software runs a different
digital gain, or our energy→volts law double-counts the ×2.

## Still needed from the Windows side

Neither of these is in the captures above, and both would close the 30 ns gap
faster than any more edge measurements:

1. **Wide captures at the same settings** — ~20 µs/div for the decay shape,
   ~2 ms/div for the 100 Hz rate. If their tail interpolation step is finer than
   our 74 ns, that alone explains why their edge never spills out of the fine
   region.
2. **The shape-interpolator panel** — Corner, Rise Edge factor, Decay Time factor.
   Those are `0x50f005/6/7` read straight off the screen, and would hand over the
   geometry instead of us inferring it.
