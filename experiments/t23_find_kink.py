#!/usr/bin/env python3
"""t23 - Locate the first-derivative discontinuity and find what it tracks.

Ryan reports a kink at ~7 us for 2 V / 1 us rise / 50 us decay. The geometry
predicts the rise->tail interpolation transition at 2.84 us, so the prediction is
wrong by 2.46x and none of the simple explanations (dwell+1, sample doubling,
corner halving) land on 7 us.

Rather than guess again, vary one parameter at a time and see what the kink
follows:
    scales with rise   -> it is inside the rise region
    scales with decay  -> it is in the tail interpolation
    fixed              -> a boundary that does not depend on either
"""
import math, statistics, sys, time

sys.path.insert(0, '/home/ryan/caen_signal_emulator/linux')
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')
import tworegion as T
from pulser import Pulser
from scope import Scope


def find_kink(t, v, skip_us=1.0):
    """Locate the largest jump in the smoothed first derivative after the peak.

    Returns (kink_time_relative_to_peak, strength) or (None, None).
    """
    n = len(v)
    dt = t[1] - t[0]
    base = min(v)
    amp = max(v) - base
    if amp < 0.2:
        return None, None
    ipk = max(range(n), key=lambda i: v[i])

    # smooth hard: the scope's display decimation alternates sample to sample
    w = max(3, int(300e-9 / dt))
    sm = []
    for i in range(n):
        a, b = max(0, i - w), min(n, i + w + 1)
        sm.append(sum(v[a:b]) / (b - a))
    d = [(sm[i + 1] - sm[i]) / dt for i in range(n - 1)]

    # second difference of the smoothed derivative = curvature change
    start = ipk + int(skip_us * 1e-6 / dt)
    best, bi = 0.0, None
    span = max(2, int(400e-9 / dt))
    for i in range(start + span, n - span - 1):
        before = statistics.mean(d[i - span:i])
        after = statistics.mean(d[i:i + span])
        jump = abs(after - before)
        if jump > best:
            best, bi = jump, i
    if bi is None:
        return None, None
    return (bi - ipk) * dt, best


def main():
    sc = Scope()
    tb = float(sc.q(':TIM:MAIN:SCAL?'))
    print(f"scope {tb*10*1e6:.0f} us window, {tb*10/1000*1e9:.0f} ns/sample\n")
    p = Pulser().open()

    print(f"{'rise':>7} {'decay':>7} {'predicted':>10} {'kink found':>11} "
          f"{'ratio':>7} {'corner':>7} {'rf':>4} {'tf':>4}")
    print("-" * 66)
    for rise_us, dec_us in ((1.0, 50.0), (2.0, 50.0), (0.5, 50.0),
                            (1.0, 25.0), (1.0, 100.0)):
        s, c, rf, tf, info = T.build(rise_us * 1e-6, dec_us * 1e-6)
        pred = c * rf * T.DAC_DT
        p.set_pulse(rate_hz=1000.0, amplitude_v=2.0, decay_us=dec_us,
                    rise_us=rise_us, baseline_v=0.0)
        time.sleep(2.2)
        m = sc.meas(1)
        if m['VMIN'] is not None and m['VMAX'] is not None:
            sc.set_trigger_level((m['VMIN'] + m['VMAX']) / 2.0)
            time.sleep(1.6)
        t, v = sc.waveform(1)
        k, strength = find_kink(t, v)
        print(f"{rise_us:>5.1f}us {dec_us:>5.0f}us {pred*1e6:>9.2f}us "
              f"{(('%.2f us' % (k*1e6)) if k else '-'):>11} "
              f"{(('%.2f' % (k/pred)) if k else '-'):>7} {c:>7} {rf:>4} {tf:>4}")

    p.close()
    sc.close()


if __name__ == '__main__':
    main()
