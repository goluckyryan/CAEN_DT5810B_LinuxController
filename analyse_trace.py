#!/usr/bin/env python3
"""Capture CH1 and characterise it: amplitude, rise, decay tau, pulse spacing.

Read-only. Does not touch any scope setting (Ryan has set the window and trigger).
"""
import math, sys
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')
from scope import Scope


def find_peaks(v, thresh_frac=0.5):
    base = min(v)
    amp = max(v) - base
    thr = base + thresh_frac * amp
    peaks = []
    i = 0
    n = len(v)
    while i < n:
        if v[i] > thr:
            j = i
            while j < n and v[j] > thr:
                j += 1
            seg = range(i, j)
            peaks.append(max(seg, key=lambda k: v[k]))
            i = j
        else:
            i += 1
    return peaks, base, amp


def fit_tau(t, v, ipk, base, amp):
    n = len(v)
    dt = t[1] - t[0]
    tail = [(i, v[i]) for i in range(ipk + 2, n) if v[i] - base > 0.08 * amp]
    # stop at the next rise
    cut = []
    for i, val in tail:
        if cut and val > cut[-1][1] + 0.02 * amp:
            break
        cut.append((i, val))
    if len(cut) < 12:
        return None, None
    xs = [(i - ipk) * dt for i, _ in cut]
    ys = [math.log(val - base) for _, val in cut]
    k = len(xs)
    mx, my = sum(xs) / k, sum(ys) / k
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    den = sum((a - mx) ** 2 for a in xs)
    if den <= 0 or num >= 0:
        return None, None
    tau = -den / num
    return tau, (xs[-1] - xs[0]) / tau


def main():
    sc = Scope()
    tb = float(sc.q(':TIM:MAIN:SCAL?'))
    print(f"window: {tb*10*1e6:.0f} us across screen, "
          f"{sc.q(':CHAN1:SCAL?')} V/div, trig={sc.q(':TRIG:STAT?')}")
    t, v = sc.waveform(1)
    sc.close()

    n = len(v)
    dt = t[1] - t[0]
    peaks, base, amp = find_peaks(v)
    print(f"trace : {n} pts @ {dt*1e9:.0f} ns/sample = {n*dt*1e6:.0f} us span")
    print(f"level : baseline {base:.4f} V, peak {max(v):.4f} V, "
          f"amplitude {amp:.4f} V")
    print(f"peaks : {len(peaks)} found at "
          f"{[round(t[p]*1e6,1) for p in peaks[:8]]} us")

    if len(peaks) >= 2:
        gaps = [(peaks[i+1] - peaks[i]) * dt for i in range(len(peaks) - 1)]
        g = sum(gaps) / len(gaps)
        print(f"spacing: {g*1e6:.2f} us  -> rate {1/g:.1f} Hz "
              f"(gaps {[round(x*1e6,1) for x in gaps]})")
    else:
        print("spacing: need >=2 peaks in the window to measure rate")

    if peaks:
        ipk = peaks[0] if len(peaks) == 1 else peaks[len(peaks)//2]
        lo, hi = base + 0.1 * amp, base + 0.9 * amp
        i10 = i90 = None
        for i in range(ipk, 0, -1):
            if v[i] >= hi and i90 is None:
                i90 = i
            if v[i] <= lo:
                i10 = i
                break
        if i10 is not None and i90 is not None and i90 > i10:
            print(f"rise  : 10-90% = {(i90-i10)*dt*1e6:.2f} us "
                  f"({i90-i10} samples @ {dt*1e9:.0f} ns - "
                  f"{'resolved' if i90-i10 >= 4 else 'AT RESOLUTION LIMIT'})")
        else:
            print("rise  : not resolvable")

        tau, span = fit_tau(t, v, ipk, base, amp)
        if tau:
            print(f"decay : tau = {tau*1e6:.2f} us  (fitted over {span:.2f} tau - "
                  f"{'good' if span > 1.0 else 'short, indicative'})")
        else:
            print("decay : could not fit")


if __name__ == '__main__':
    main()
