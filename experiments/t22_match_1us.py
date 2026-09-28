#!/usr/bin/env python3
"""t22 - Reproduce the vendor's 1 us / 0.6 us / 0.5 us rise SHAPE.

The vendor edge is a clean single-pole exponential approach (verified off the
saved traces), which is what our IIR low-pass already produces. So the form
should match and only the time scaling should differ. This measures both the
10-90 and the normalised shape against the saved reference, so a mismatch in
form is distinguishable from a mismatch in scale.

Vendor settings: LSB 1234, 100 Hz, 50 us decay.
"""
import glob, json, math, os, re, sys, time

sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')
from pulser import Pulser
from scope import Scope

REF = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'reference')
TARGETS = [1000, 600, 500]          # ns, in the order Ryan asked for


def load_ref(q_ns):
    for fn in glob.glob(os.path.join(REF, '*.json')):
        d = json.load(open(fn))
        s = d['meta']['settings']
        q = float(re.search(r'([\d.]+) us', s.split('rise')[0].split(',')[-1]).group(1)) * 1000
        if round(q) == q_ns:
            return d
    return None


def analyse(t, v):
    """Return (baseline, amplitude, t10_90, normalised edge profile)."""
    n = len(v)
    dt = t[1] - t[0]
    lo = sorted(v)[:max(1, n // 10)]
    hi = sorted(v)[-max(1, n // 10):]
    b = sum(lo) / len(lo)
    pk = sum(hi) / len(hi)
    amp = pk - b
    if amp <= 0.02:
        return b, amp, None, None
    w = max(1, int(20e-9 / dt))
    der = [v[min(i + w, n - 1)] - v[i] for i in range(n - w)]
    ie = max(range(len(der)), key=lambda i: der[i])
    i0 = ie
    while i0 > 0 and v[i0] > b + 0.02 * amp:
        i0 -= 1
    i10 = next((i for i in range(i0, n) if v[i] >= b + 0.1 * amp), None)
    i90 = next((i for i in range(i0, n) if v[i] >= b + 0.9 * amp), None)
    span = (i90 - i10) * dt if (i10 and i90 and i90 > i10) else None
    prof = None
    if span:
        prof = []
        for k in range(21):
            idx = i10 + (i90 - i10) * k // 20
            prof.append(min(1.5, max(-0.5, (v[idx] - b) / amp)))
    return b, amp, span, prof


def main():
    sc = Scope()
    tb = float(sc.q(':TIM:MAIN:SCAL?'))
    print(f"scope {tb:g} s/div -> {tb*10*1e6:.2f} us window, "
          f"{tb*10/1000*1e9:.1f} ns/sample, {sc.q(':CHAN1:SCAL?')} V/div\n")

    p = Pulser().open()
    for q in TARGETS:
        ref = load_ref(q)
        if ref is None:
            print(f"  no reference for {q} ns, skipping")
            continue
        rb, ramp, rspan, rprof = analyse(ref['t'], ref['v'])
        vend_rtim = float(ref['meta']['meas']['RTIM']) * 1e9

        p.set_pulse(rate_hz=100.0, amplitude_v=0.215, decay_us=50.0,
                    rise_us=q / 1000.0)
        time.sleep(2.2)
        sc.set_trigger_level(0.09)          # ~40% of a 0.215 V pulse
        time.sleep(1.6)
        ours_rtim = sc.qf(':MEAS:ITEM? RTIM,CHAN1')
        t, v = sc.waveform(1)
        ob, oamp, ospan, oprof = analyse(t, v)

        print(f"=== requested {q} ns ===")
        print(f"   vendor  RTIM {vend_rtim:>7.1f} ns   10-90 "
              f"{(('%.0f ns' % (rspan*1e9)) if rspan else '-'):>9}   amp {ramp:.4f} V")
        print(f"   ours    RTIM "
              f"{(('%.1f ns' % (ours_rtim*1e9)) if ours_rtim else '-'):>9}   10-90 "
              f"{(('%.0f ns' % (ospan*1e9)) if ospan else '-'):>9}   amp "
              f"{(('%.4f V' % oamp) if oamp else '-')}")
        if rprof and oprof:
            rms = math.sqrt(sum((a - b) ** 2 for a, b in zip(oprof, rprof)) / len(oprof))
            print(f"   normalised edge shape, 10% -> 90%:")
            print(f"     vendor " + " ".join(f"{x:4.2f}" for x in rprof[::4]))
            print(f"     ours   " + " ".join(f"{x:4.2f}" for x in oprof[::4]))
            print(f"     rms difference {rms:.3f}  "
                  f"({'SAME FORM' if rms < 0.06 else 'DIFFERENT FORM'})")
        print()
    p.close()
    sc.close()


if __name__ == '__main__':
    main()
