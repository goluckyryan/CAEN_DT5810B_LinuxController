#!/usr/bin/env python3
"""t10 - Calibrate requested vs achieved rise/decay, without a wider scope window.

Problem: at 2 us/div a 100 us decay only shows 0.18 tau, so the fitted tau is
meaningless. Solution: characterise the transfer at SHORT time constants that do
fit the 20 us window (1..8 us decay = 1.25..10 tau visible), establish the
requested->achieved scale factor, and extrapolate to the 100 us target.

If the scale factor is constant across the sweep, it is a clock/format error in
the coefficient math and can simply be divided out.
"""
import math, sys, time
sys.path.insert(0, '/home/ryan/caen_signal_emulator/linux')
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')   # archived: modules live in the parent
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')
from dt5810_drc import DT5810DRC
from scope import Scope


def analyse(t, v):
    """Return (amplitude, rise_10_90_s, tau_s, tau_span_in_taus)."""
    n = len(v)
    dt = t[1] - t[0]
    base, pk = min(v), max(v)
    amp = pk - base
    if amp < 0.05:
        return amp, None, None, None
    ipk = max(range(n), key=lambda i: v[i])

    lo, hi = base + 0.1 * amp, base + 0.9 * amp
    i10 = i90 = None
    for i in range(ipk, 0, -1):
        if v[i] >= hi and i90 is None:
            i90 = i
        if v[i] <= lo:
            i10 = i
            break
    rise = (i90 - i10) * dt if (i10 is not None and i90 is not None and i90 > i10) else None

    tail = [(i, v[i]) for i in range(ipk + 3, n) if v[i] - base > 0.05 * amp]
    tau = span = None
    if len(tail) > 25:
        xs = [(i - ipk) * dt for i, _ in tail]
        ys = [math.log(val - base) for _, val in tail]
        k = len(xs)
        mx, my = sum(xs) / k, sum(ys) / k
        num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        den = sum((x - mx) ** 2 for x in xs)
        if den > 0 and num < 0:
            tau = -den / num
            span = (xs[-1] - xs[0]) / tau
    return amp, rise, tau, span


sc = Scope()
st = sc.settings()
print(f"scope: {st['tb_scale']} s/div, {st['ch1_scale']} V/div, {st['ch1_imp']} "
      f"[not changed]\n")

d = DT5810DRC()
d.open()
d.bringup()

RATE = 20000.0        # 50 us period: pulses well separated, several per window

print("=== DECAY sweep (rise fixed 1 us) ===")
print(f"{'req tau us':>11} {'meas tau us':>12} {'span (tau)':>11} {'ratio':>8} {'amp V':>8}")
dec_rows = []
for req in (1.0, 2.0, 4.0, 8.0):
    d.set_drc_pulse(rate_hz=RATE, rise_us=1.0, decay_us=req, energy=4000, gain=1107)
    time.sleep(1.5)
    m = sc.meas(1)
    if m['VMIN'] is not None and m['VMAX'] is not None:
        sc.set_trigger_level((m['VMIN'] + m['VMAX']) / 2.0)
        time.sleep(1.0)
    try:
        t, v = sc.waveform(1)
        amp, rise, tau, span = analyse(t, v)
    except Exception as e:
        print(f"{req:>11.1f}  capture failed: {e}")
        continue
    if tau:
        ratio = tau * 1e6 / req
        dec_rows.append((req, tau * 1e6, ratio, span))
        print(f"{req:>11.1f} {tau*1e6:>12.2f} {span:>11.1f} {ratio:>8.3f} {amp:>8.3f}")
    else:
        print(f"{req:>11.1f} {'-':>12} {'-':>11} {'-':>8} {amp:>8.3f}")

print("\n=== RISE sweep (decay fixed 4 us) ===")
print(f"{'req rise us':>12} {'meas 10-90 us':>14} {'ratio':>8} {'amp V':>8}")
rise_rows = []
for req in (0.5, 1.0, 2.0, 4.0):
    d.set_drc_pulse(rate_hz=RATE, rise_us=req, decay_us=4.0, energy=4000, gain=1107)
    time.sleep(1.5)
    m = sc.meas(1)
    if m['VMIN'] is not None and m['VMAX'] is not None:
        sc.set_trigger_level((m['VMIN'] + m['VMAX']) / 2.0)
        time.sleep(1.0)
    try:
        t, v = sc.waveform(1)
        amp, rise, tau, span = analyse(t, v)
    except Exception as e:
        print(f"{req:>12.1f}  capture failed: {e}")
        continue
    if rise:
        ratio = rise * 1e6 / req
        rise_rows.append((req, rise * 1e6, ratio))
        print(f"{req:>12.1f} {rise*1e6:>14.3f} {ratio:>8.3f} {amp:>8.3f}")
    else:
        print(f"{req:>12.1f} {'-':>14} {'-':>8} {amp:>8.3f}")

d.close()
sc.close()

print("\n=== CALIBRATION ===")
if dec_rows:
    good = [r for r in dec_rows if r[3] and r[3] > 1.0]     # >=1 tau captured
    use = good or dec_rows
    rs = [r[2] for r in use]
    md = sum(rs) / len(rs)
    print(f"  decay: achieved/requested = {md:.3f}  "
          f"(spread {min(rs):.3f}-{max(rs):.3f}, n={len(use)})")
    print(f"         -> for a real 100 us decay, request {100/md:.1f} us")
    if abs(md - 0.5) < 0.08:
        print("         ~0.5 => coefficient math assumes 625 MS/s but the DRC "
              "block is running at 1.25 GS/s")
if rise_rows:
    rs = [r[2] for r in rise_rows]
    mr = sum(rs) / len(rs)
    print(f"  rise : achieved/requested = {mr:.3f}  "
          f"(spread {min(rs):.3f}-{max(rs):.3f}, n={len(rise_rows)})")
    print(f"         -> for a real 1 us rise, request {1/mr:.2f} us")
