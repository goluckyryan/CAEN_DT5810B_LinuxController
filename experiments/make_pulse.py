#!/usr/bin/env python3
"""Generate the requested signal: 1 kHz, 1 V, 1 us rise, 100 us decay.

Usage:  python3 make_pulse.py [--rate 1000] [--amp 1.0] [--rise 1.0] [--decay 100.0]
                              [--no-calib] [--gain N] [--energy N]

Calibrates the amplitude against the scope (read-only apart from trigger level,
which Ryan authorised) by scaling the digital gain, then reports the achieved
rise time and decay constant measured from the captured trace.
"""
import argparse, math, sys, time
sys.path.insert(0, '/home/ryan/caen_signal_emulator/linux')
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')   # archived: modules live in the parent
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')
from dt5810_drc import DT5810DRC
from scope import Scope

ap = argparse.ArgumentParser()
ap.add_argument('--rate', type=float, default=1000.0)
ap.add_argument('--amp', type=float, default=1.0)
ap.add_argument('--rise', type=float, default=1.0)
ap.add_argument('--decay', type=float, default=100.0)
ap.add_argument('--energy', type=int, default=4000)
ap.add_argument('--gain', type=int, default=1107)
ap.add_argument('--no-calib', action='store_true')
ap.add_argument('--iters', type=int, default=4)
a = ap.parse_args()

print(f"TARGET: {a.rate:g} Hz, {a.amp:g} V, rise {a.rise:g} us, decay {a.decay:g} us\n")

sc = Scope()
st = sc.settings()
win_us = float(st['tb_scale']) * 10 * 1e6
vdiv = float(st['ch1_scale'])
print(f"scope: {st['tb_scale']} s/div -> {win_us:.0f} us window, "
      f"{vdiv} V/div, {st['ch1_imp']}")
period_us = 1e6 / a.rate
if win_us < period_us:
    print(f"  NOTE: window {win_us:.0f} us < pulse period {period_us:.0f} us -> "
          f"FREQ not measurable here; rate cannot be verified from this scope view.")
print()

d = DT5810DRC()
d.open()
d.bringup()

gain = a.gain
info = None
for it in range(a.iters if not a.no_calib else 1):
    info = d.set_drc_pulse(rate_hz=a.rate, rise_us=a.rise, decay_us=a.decay,
                           energy=a.energy, gain=gain)
    time.sleep(1.5)
    m = sc.meas(1)
    if m['VMIN'] is not None and m['VMAX'] is not None:
        sc.set_trigger_level((m['VMIN'] + m['VMAX']) / 2.0)
        time.sleep(1.2)
        m = sc.meas(1)
    vpp = m['VPP']
    print(f"  iter {it}: gain={gain:<6} -> Vpp={vpp if vpp is None else round(vpp,4)} V")
    if a.no_calib or vpp is None or vpp <= 0.01:
        break
    err = abs(vpp - a.amp) / a.amp
    if err < 0.03:
        print(f"           within 3% of {a.amp} V - done")
        break
    new_gain = int(round(gain * a.amp / vpp))
    new_gain = max(1, min(65535, new_gain))
    if new_gain == gain:
        break
    gain = new_gain

print("\nconfig applied:")
for k, v in info.items():
    print(f"   {k:22} = {v}")

# ---- measure the achieved shape ----
print("\nmeasured on CH1:")
m = sc.meas(1)
for k in ('VPP', 'VMAX', 'VMIN', 'VAVG', 'FREQ', 'PER'):
    print(f"   {k:5} = {m[k]}")

try:
    t, v = sc.waveform(1)
    n = len(v)
    dt = t[1] - t[0]
    base = min(v)
    pk = max(v)
    amp = pk - base
    ipk = max(range(n), key=lambda i: v[i])
    print(f"\n   trace: {n} pts @ {dt*1e9:.0f} ns/sample ({n*dt*1e6:.0f} us span)")
    print(f"   baseline={base:.4f} V  peak={pk:.4f} V  amplitude={amp:.4f} V")

    # rise: 10% -> 90% before the peak
    lo, hi = base + 0.1 * amp, base + 0.9 * amp
    i10 = i90 = None
    for i in range(ipk, 0, -1):
        if v[i] >= hi and i90 is None:
            i90 = i
        if v[i] <= lo:
            i10 = i
            break
    if i10 is not None and i90 is not None and i90 > i10:
        print(f"   RISE 10-90% = {(i90-i10)*dt*1e6:.3f} us   (target {a.rise:g} us, "
              f"model {info['rise_10_90_model_us']:.3f} us)")
    else:
        print("   RISE: edge not fully captured in this window")

    # decay: least-squares fit of ln(v-base) on the tail
    tail = [(i, v[i]) for i in range(ipk + 3, n) if v[i] - base > 0.05 * amp]
    if len(tail) > 30:
        xs = [(i - ipk) * dt for i, _ in tail]
        ys = [math.log(val - base) for _, val in tail]
        k = len(xs)
        mx, my = sum(xs) / k, sum(ys) / k
        num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        den = sum((x - mx) ** 2 for x in xs)
        if den > 0 and num < 0:
            tau = -1.0 / (num / den)
            frac = (xs[-1] - xs[0]) / tau
            print(f"   DECAY tau  = {tau*1e6:.1f} us   (target {a.decay:g} us, "
                  f"model {info['tau_fall_model_us']:.1f} us)")
            print(f"        fitted over {(xs[-1]-xs[0])*1e6:.1f} us = {frac:.2f} tau "
                  f"({'adequate' if frac > 0.5 else 'SHORT - extrapolated, treat as indicative'})")
        else:
            print("   DECAY: tail does not decay within the captured window")
    else:
        print("   DECAY: not enough tail samples above 5% in this window")
except Exception as e:
    print("   waveform capture unavailable:", e)

d.close()
sc.close()
print("\nOutput left RUNNING on CH0.")
