#!/usr/bin/env python3
"""t26 - Energy spectrum mode: does the board draw amplitudes from a histogram?

Three questions, in order:
  1. Does a DELTA spectrum (one populated bin) give a fixed amplitude, and does
     it land where bin -> volts says it should? That pins the bin numbering.
  2. Do TWO equal deltas give two amplitudes in equal proportion?
  3. Does a Gaussian peak give a spread of the right width?

MEASUREMENT SETUP -- this matters, and it is why the delay work came first.
Sampling an amplitude distribution off a scope is easy to get wrong: if the
spectrum channel is also the trigger source, the trigger level censors every
pulse below it and the sample is biased. So:

    CH2  fixed amplitude, the trigger source (a reliable, unbiased trigger)
    CH1  the spectrum, synced to CH2 by the correlation block so that every
         CH1 pulse lands in the window regardless of its height

Then :MEAS:ITEM? VMAX,CHAN1 samples CH1's amplitude with no selection effect.
"""
import sys, time, collections

sys.path.insert(0, '/home/ryan/caen_signal_emulator/linux')
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')
import spectrum as S
from pulser import Pulser, CORR_TIMEBASE, CORR_DISABLED, volts_for_energy
from scope import Scope


def sample(sc, n=60, ch=1, settle=0.06):
    """n amplitude samples from the scope, one per acquisition."""
    out = []
    for _ in range(n):
        v = sc.qf(f':MEAS:ITEM? VMAX,CHAN{ch}')
        b = sc.qf(f':MEAS:ITEM? VMIN,CHAN{ch}')
        if v is not None and b is not None:
            out.append(v - b)
        time.sleep(settle)
    return out


def describe(vals, label):
    if not vals:
        print(f"  {label}: no samples"); return
    vals = sorted(vals)
    n = len(vals)
    mean = sum(vals) / n
    sd = (sum((v - mean) ** 2 for v in vals) / n) ** 0.5
    print(f"  {label}: n={n}  mean {mean:.3f} V  sd {sd:.3f}  "
          f"min {vals[0]:.3f}  median {vals[n//2]:.3f}  max {vals[-1]:.3f}")
    return mean, sd


def histogram(vals, lo, hi, nb=14):
    """Crude ASCII histogram so the shape is visible, not just the moments."""
    if not vals:
        return
    counts = collections.Counter()
    for v in vals:
        k = int((v - lo) / (hi - lo) * nb)
        counts[max(0, min(nb - 1, k))] += 1
    peak = max(counts.values()) or 1
    for k in range(nb):
        c = counts.get(k, 0)
        edge = lo + (hi - lo) * k / nb
        print(f"    {edge:5.2f} V |{'#' * int(30 * c / peak):<30} {c}")


def main():
    sc = Scope(timeout=8)
    p = Pulser().open()

    common = dict(rate_hz=2000.0, decay_us=2.0, rise_us=0.1, baseline_v=0.0)
    p.set_pulse(amplitude_v=1.0, ch=0, **common)      # CH1: overwritten below
    p.set_pulse(amplitude_v=0.6, ch=1, **common)      # CH2: fixed trigger
    p.set_correlation(CORR_TIMEBASE, delay_ns=0.0)    # CH1 always in the window
    time.sleep(2.5)
    print(f"trigger {sc.q(':TRIG:EDGE:SOUR?').strip()} @ "
          f"{sc.q(':TRIG:EDGE:LEV?').strip()} V, "
          f"{float(sc.q(':TIM:MAIN:SCAL?'))*1e6:g} us/div\n")

    print("--- 0. baseline: CH1 in FIXED mode, for comparison ---")
    describe(sample(sc, 25), "fixed 1.0 V")

    print("\n--- 1. delta spectra: one populated bin ---")
    for volts in (0.5, 1.0, 1.5):
        b = S.bin_for_volts(volts)
        S.program(p.d, S.delta(b), ch=0)
        time.sleep(2.0)
        got = describe(sample(sc, 30), f"delta @ bin {b} (predict {volts:.2f} V)")
        if got:
            print(f"      -> predicted {S.volts_for_bin(b):.3f} V, "
                  f"measured {got[0]:.3f} V, error {got[0]-S.volts_for_bin(b):+.3f} V")

    print("\n--- 2. two equal deltas: expect a 50/50 mix ---")
    b1, b2 = S.bin_for_volts(0.5), S.bin_for_volts(1.2)
    h = S.empty(); h[b1] = 1.0; h[b2] = 1.0
    S.program(p.d, h, ch=0)
    time.sleep(2.0)
    vals = sample(sc, 80)
    describe(vals, f"bins {b1}+{b2}")
    if vals:
        mid = (S.volts_for_bin(b1) + S.volts_for_bin(b2)) / 2
        lowf = sum(1 for v in vals if v < mid) / len(vals)
        print(f"      -> {lowf*100:.0f}% low / {(1-lowf)*100:.0f}% high "
              f"(expect 50/50)")
        histogram(vals, 0.2, 1.5)

    print("\n--- 3. a Gaussian peak: expect a spread, not a line ---")
    bc = S.bin_for_volts(1.0)
    sig = S.bin_for_volts(1.1) - bc          # sigma = 0.1 V worth of bins
    S.program(p.d, S.peaks((bc, sig, 1.0)), ch=0)
    time.sleep(2.0)
    vals = sample(sc, 80)
    got = describe(vals, f"gaussian centre {bc}, sigma {sig} bins (=0.1 V)")
    if got:
        print(f"      -> measured sd {got[1]:.3f} V (predict ~0.10 V)")
        histogram(vals, 0.5, 1.5)

    S.set_fixed(p.d, 27431, ch=0)
    p.set_correlation(CORR_DISABLED)
    p.close(); sc.close()


if __name__ == '__main__':
    main()
