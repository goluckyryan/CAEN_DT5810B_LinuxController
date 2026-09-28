#!/usr/bin/env python3
"""t12 - Digital RC on a COLD board (shape RAM never programmed).

Yesterday's DRC results were contaminated: set_detector_pulse had already loaded
the 16 shape memories, so the ~1 V output we attributed to DRC may have been the
memory path. Blanking the shape generators killed the output, which pointed that
way -- but the board then lost power, so it was never settled cleanly.

The board has now cold-booted. Shape RAM is virgin. If Digital RC alone produces
a pulse whose tau tracks the request, DRC works and yesterday was stale state.
If it produces nothing, DRC genuinely does not drive the DAC.

Rate is chosen so the period fits the scope's existing 20 us window.
"""
import math, sys, time
sys.path.insert(0, '/home/ryan/caen_signal_emulator/linux')
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')   # archived: modules live in the parent
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')
from dt5810_drc import DT5810DRC
from scope import Scope


def analyse(t, v):
    n = len(v)
    dt = t[1] - t[0]
    base, pk = min(v), max(v)
    amp = pk - base
    if amp < 0.04:
        return amp, None, None
    ipk = max(range(n), key=lambda i: v[i])
    tail = [(i, v[i]) for i in range(ipk + 3, n) if v[i] - base > 0.05 * amp]
    tau = None
    if len(tail) > 25:
        xs = [(i - ipk) * dt for i, _ in tail]
        ys = [math.log(x - base) for _, x in tail]
        k = len(xs)
        mx, my = sum(xs) / k, sum(ys) / k
        num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
        den = sum((a - mx) ** 2 for a in xs)
        if den > 0 and num < 0:
            tau = -den / num
    return amp, tau, ipk


sc = Scope()
st = sc.settings()
print("scope:", st, "\n")

d = DT5810DRC()
d.open()
d.bringup()
print("cold bringup done. Shape RAM has never been written in this power cycle.\n")

print("=== Digital RC only, tau sweep (period 10000 -> ~100 kHz) ===")
print(f"{'req tau us':>11} {'amp V':>8} {'meas tau us':>12} {'tracks?':>9}")
rows = []
for req in (1.0, 2.0, 4.0):
    d.set_drc_pulse(rate_hz=d.rate_for_period(10000), rise_us=0.1, decay_us=req,
                    energy=4000, gain=1107)
    time.sleep(2.0)
    m = sc.meas(1)
    if m['VMIN'] is not None and m['VMAX'] is not None:
        sc.set_trigger_level((m['VMIN'] + m['VMAX']) / 2.0)
        time.sleep(1.5)
        m = sc.meas(1)
    try:
        t, v = sc.waveform(1)
        amp, tau, ipk = analyse(t, v)
    except Exception as e:
        print(f"{req:>11.1f}  capture failed: {e}")
        continue
    tt = f"{tau*1e6:.2f}" if tau else "-"
    print(f"{req:>11.1f} {amp:>8.3f} {tt:>12} "
          f"{('yes' if tau and 0.4 < tau*1e6/req < 2.5 else 'no'):>9}")
    rows.append((req, amp, tau))

print("\n=== control: is anything on the output at all? ===")
d.run_enable(False)
time.sleep(1.5)
m_off = sc.meas(1)
d.run_enable(True)
time.sleep(1.5)
m_on = sc.meas(1)
print(f"  run OFF: Vpp={m_off['VPP']}  Vmax={m_off['VMAX']}")
print(f"  run ON : Vpp={m_on['VPP']}   Vmax={m_on['VMAX']}")

d.close()
sc.close()

print("\n=== VERDICT ===")
amps = [a for _, a, _ in rows]
taus = [t for _, _, t in rows if t]
if amps and max(amps) > 0.2:
    print(f"  DRC alone DOES drive the DAC (max amp {max(amps):.3f} V on a cold board).")
    if len(taus) >= 2:
        ratios = [t * 1e6 / r for r, _, t in rows if t]
        if max(ratios) / min(ratios) < 1.6:
            print(f"  tau tracks the request, ratio ~{sum(ratios)/len(ratios):.2f}"
                  f" -> a fixed scale factor, correctable.")
        else:
            print(f"  tau does NOT track (ratios {['%.1f' % r for r in ratios]}).")
else:
    print(f"  DRC alone produces essentially nothing (max amp "
          f"{max(amps) if amps else 0:.3f} V) even on a cold board.")
    print("  => the memory/shape path is the only one that drives the output.")
