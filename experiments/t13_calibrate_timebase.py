#!/usr/bin/env python3
"""t13 - Calibrate the shape-RAM time axis and the timebase period register.

Two unknowns to pin down with measurements rather than assumptions:

 1. ns per RAM sample as a function of the interpolation factor I.
    Expected (I+1) x DAC period. Measured by loading a pure exponential of
    known tau in SAMPLES and reading back the tau in SECONDS.

 2. the timebase clock, from the period register vs the measured pulse rate.

Both are measured with short time constants and high rates so everything fits
the scope's existing 20 us window. No scope scale changes.
"""
import math, sys, time
sys.path.insert(0, '/home/ryan/caen_signal_emulator/linux')
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')   # archived: modules live in the parent
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')
from dt5810 import DT5810, _reg
from scope import Scope
from shaperam import shaped_exponential, program_shape

CH = 0


def analyse(t, v):
    n = len(v)
    dt = t[1] - t[0]
    base, pk = min(v), max(v)
    amp = pk - base
    if amp < 0.05:
        return amp, None, None
    ipk = max(range(n), key=lambda i: v[i])
    tail = [(i, v[i]) for i in range(ipk + 3, n) if v[i] - base > 0.08 * amp]
    tau = None
    if len(tail) > 20:
        xs = [(i - ipk) * dt for i, _ in tail]
        ys = [math.log(x - base) for _, x in tail]
        k = len(xs)
        mx, my = sum(xs) / k, sum(ys) / k
        num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
        den = sum((a - mx) ** 2 for a in xs)
        if den > 0 and num < 0:
            tau = -den / num
    return amp, tau, (xs[-1] - xs[0]) / tau if tau else None


def setup(d, tau_samp, interp, period, gain=1107, offset=-55465, energy=8000):
    samples = shaped_exponential(tau_rise_samp=0.0, tau_decay_samp=tau_samp)
    program_shape(d, samples, interp, ch=CH)
    d.wr(_reg(CH, 0x020f0004), 0)
    d.wr(_reg(CH, 0x020f0005), energy)
    d.wr(_reg(CH, 0x0f000000), offset & 0xFFFFFFFF)
    d.wr(_reg(CH, 0x0f000001), gain & 0xFFFFFFFF)
    d.wr(_reg(CH, 0x0f000004), 1)
    d.wr(_reg(CH, 0x0f000002), 1)
    d.wr(0xFA00100A, 0)
    d.wr(_reg(CH, 0x0100000a), 0)
    d.wr(_reg(CH, 0x01000009), period)
    d.wr(_reg(CH, 0x01c00006), 0)
    d.wr(_reg(CH, 0x01c00004), 1)
    d.wr(_reg(CH, 0x01c00003), 1)
    d.wr(_reg(CH, 0x01c00006), 1)


def measure(sc):
    time.sleep(2.0)
    m = sc.meas(1)
    if m['VMIN'] is not None and m['VMAX'] is not None:
        sc.set_trigger_level((m['VMIN'] + m['VMAX']) / 2.0)
        time.sleep(1.5)
        m = sc.meas(1)
    try:
        t, v = sc.waveform(1)
        return m, analyse(t, v)
    except Exception as e:
        return m, (None, None, None)


sc = Scope()
print("scope:", sc.settings(), "\n")
d = DT5810()
d.open()
d.bringup()

TAU_SAMP = 100.0
print(f"=== 1. ns per RAM sample  (pure exp, tau = {TAU_SAMP:g} samples) ===")
print(f"{'interp I':>9} {'amp V':>8} {'tau meas us':>12} {'span':>6} "
      f"{'ns/sample':>10} {'ns/(I+1)':>9}")
rows = []
for I in (1, 2, 4, 8):
    setup(d, TAU_SAMP, I, period=20000)
    m, (amp, tau, span) = measure(sc)
    if tau:
        nsps = tau * 1e9 / TAU_SAMP
        print(f"{I:>9} {amp:>8.3f} {tau*1e6:>12.3f} {span:>6.1f} "
              f"{nsps:>10.2f} {nsps/(I+1):>9.2f}")
        rows.append((I, nsps))
    else:
        print(f"{I:>9} {amp if amp else 0:>8.3f} {'-':>12} {'-':>6} {'-':>10} {'-':>9}")

print("\n=== 2. timebase clock (period register vs measured rate) ===")
print(f"{'period':>9} {'FREQ meas':>12} {'derived clock':>15}")
clocks = []
I_USE = rows[0][0] if rows else 2
for P in (5000, 10000, 20000):
    setup(d, 20.0, I_USE, period=P)
    m, _ = measure(sc)
    fr = m['FREQ']
    if fr:
        c = fr * (P + 1)
        clocks.append(c)
        print(f"{P:>9} {fr/1e3:>11.2f}k {c/1e6:>14.2f} MHz")
    else:
        print(f"{P:>9} {'none':>12} {'-':>15}")

d.close()
sc.close()

print("\n=== CALIBRATION RESULT ===")
if rows:
    per_step = [n / (I + 1) for I, n in rows]
    avg = sum(per_step) / len(per_step)
    print(f"  ns per RAM sample = (I+1) x {avg:.2f} ns "
          f"(spread {min(per_step):.2f}-{max(per_step):.2f})")
    print(f"  -> for a 100 us decay with tau=200 samples, need "
          f"{100000/200:.0f} ns/sample -> I = {round(500/avg)-1}")
else:
    print("  could not calibrate the sample period")
if clocks:
    c = sum(clocks) / len(clocks)
    print(f"  timebase clock = {c/1e6:.2f} MHz -> period for 1 kHz = {round(c/1000)-1}")
else:
    print("  timebase clock not measured (no FREQ reading in this window)")
