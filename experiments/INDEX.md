# Experiment archive

Every experiment run 2026-09-22..24, in order, with what it actually established.
Kept because the findings in `../README.md` rest on them. None of these are needed
to use the instrument — `../pulser.py` and `../awg_backend.py` are the product.

They import the production modules from the parent directory (a `sys.path` line was
added when they were archived), so they still run from here. All of them need the
board powered and most need the scope.

| script | what it established | still valid? |
|---|---|---|
| `t01_probe_existing.py` | ran the existing library and dumped registers; every read returned filler | ✅ led to B1 |
| `t02_read_matrix.py` | read framing matrix — **address verbatim, count = N**, not `addr-1`/`count+2` | ✅ |
| `t03_fpga_alive.py` | identity registers before/after bringup; `board_id()` never returns the model word | ✅ |
| `t04_id_scan.py` | scanned the address space for the model word — not present anywhere | ✅ |
| `t05_output_check.py` | first scope check; "no pulse train" — **WRONG**, was a 2 µs window missing a 318 Hz signal | ❌ artifact |
| `t06_rate_sweep.py` | first rate sweep; inconclusive, pile-up at the rates chosen | ❌ superseded |
| `t07_shape_compare.py` | compared shape-RAM / old DRC / corrected DRC | ⚠️ confounded by the energy address |
| `t08_rate_in_window.py` | tried to fit the rate into the scope window | ❌ superseded by t14/t18 |
| `t09_do_writes_land.py` | **control experiment** — run gate 1.011 V → 0.087 V proved writes reach the FPGA | ✅ important |
| `t10_calibrate_shape.py` | decay/rise sweep; τ stuck at ~53 µs regardless of request | ✅ led to t11 |
| `t11_which_path_drives.py` | discriminator: τ followed the shape RAM, not the DRC coefficients | ✅ |
| `t12_cold_drc.py` | DRC on a cold board — nothing | ⚠️ confounded (wrong energy address), later re-proved |
| `t13_calibrate_timebase.py` | attempted ns/sample and timebase clock calibration | ❌ superseded |
| `t14_rate_settled.py` | period register swept with no effect — **with the wrong address** | ⚠️ true but for the wrong reason |
| `t15_find_rate_control.py` | swept α, TimeMode, LFSR strobe, deadtime — all no effect, again at wrong addresses | ⚠️ same |
| `t16_zero_offset.py` | **offset is linear at 3.234e-5 V/count**; −55512 puts the baseline on 0 V | ✅ calibration |
| `t17_mode_survey.py` | every mode surveyed; the naive peak detector split one pulse into three | ⚠️ read with care |
| `t18_rate_robust.py` | rate with a de-duplicated peak detector | ✅ |
| `t19_convention_matrix.py` | **the vendor `ConfigureShapeGenerator` conventions tested one at a time** | ✅ key result |
| `t20_analog_filter.py` | analog-stage response; rise floor is not set by the output filter | ✅ |
| `t21_vs_vendor.py` | our edge against the 8 vendor reference traces; found the half-curve and 7-bit corner bugs | ✅ key result |
| `t22_match_1us.py` | shape match at 0.5/0.6/1 µs after the geometry fixes (rms 0.23 → 0.04) | ✅ |
| `t23_find_kink.py` | located the first-derivative discontinuity by varying one parameter at a time | ✅ solved the hump |
| `t24_channel_delay.py` | **the correlation block**: mode 2 + CH2 timebase mux syncs the channels; delay = 0.800 ns/count | ✅ key result |
| `t25_delay_zero.py` | zero-skew scan, and confirmed mode 2 keeps amplitude/shape independent while slaving the rate | ✅ |
| `t26_spectrum.py` | **energy spectrum mode**: delta/two-peak/Gaussian spectra verified; found that the 16384-word CDF needs ONE bulk transfer | ✅ key result |

Superseded deliverables, kept for reference:

| script | note |
|---|---|
| `deliver_pulse.py` | first shape-RAM delivery; superseded by `../pulser.py` |
| `deliver_pulse_awg.py` | first AWG delivery; superseded by `../awg_backend.py` |
| `make_pulse.py` | earliest attempt, contains the auto-calibration loop that chased noise |
| `dt5810_drc.py` | early clean-room DRC implementation; DRC never produced output |
| `fastshape.py` | first port of the vendor IIR rise filter (DDE-Control.cs 12921-12950); `../tworegion.py` reimplements it and is what runs |

## The two lessons these encode

**Check the scope before changing code.** `t05`, and several later runs, concluded
"dead output" when the real cause was the scope timebase or vertical scale. That
pattern cost more time than any genuine hardware problem.

**A confounded negative is not a negative.** `t07`/`t12`/`t14`/`t15` all reached
conclusions that happened to be partly right, by reasoning that was invalid because
the energy and timebase registers were misaddressed. Only `t19` and the post-fix
re-runs are safe to quote.
