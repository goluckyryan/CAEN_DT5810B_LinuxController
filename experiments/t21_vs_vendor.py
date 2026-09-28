#!/usr/bin/env python3
"""t21 - Drive OUR implementation at the vendor's settings and overlay the edges.

The vendor reference set (../reference/) was captured with the Windows software
driving this board: LSB 1234, 100 Hz, 50 us decay, rise swept 0 -> 1 us. This
reproduces those settings through pulser.py and compares the EDGE SHAPE, not just
the 10-90 number -- because at 50 ns and above we match on the number while almost
certainly getting there differently, and the shape is what reveals the geometry.

Run after the board is powered and enumerated. Wants the scope at a timebase where
the edge is ~10% of the window (see ../reference/README.md).

Amplitude note: the vendor made 0.215 V from LSB 1234 where our calibration
predicts 0.105 V. Shapes are normalised here so that discrepancy does not obscure
the comparison, but it is a real open question.
"""
import glob, json, math, os, re, sys, time

sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')
from pulser import Pulser
from scope import Scope

REF = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   '..', 'reference')


def load_refs():
    out = {}
    for fn in glob.glob(os.path.join(REF, '*.json')):
        d = json.load(open(fn))
        m = d['meta']
        q = float(re.search(r'([\d.]+) us',
                            m['settings'].split('rise')[0].split(',')[-1]).group(1)) * 1000
        out[round(q)] = d
    return out


def edge_profile(t, v, npts=20):
    """Normalised 0..1 edge, resampled onto npts points between 10% and 90%."""
    lo10 = sorted(v)[:max(1, len(v) // 10)]
    hi10 = sorted(v)[-max(1, len(v) // 10):]
    b = sum(lo10) / len(lo10)
    pk = sum(hi10) / len(hi10)
    amp = pk - b
    if amp <= 0:
        return None, None, None
    i10 = next((i for i, x in enumerate(v) if x >= b + 0.1 * amp), None)
    i90 = next((i for i, x in enumerate(v) if x >= b + 0.9 * amp), None)
    if i10 is None or i90 is None or i90 <= i10:
        return None, None, amp
    dt = t[1] - t[0]
    span = (i90 - i10) * dt
    prof = []
    for k in range(npts):
        idx = i10 + (i90 - i10) * k // max(1, npts - 1)
        prof.append((v[idx] - b) / amp)
    return prof, span, amp


def main():
    refs = load_refs()
    print(f"loaded {len(refs)} vendor references: "
          f"{sorted(refs)} ns\n")

    sc = Scope()
    tb = float(sc.q(':TIM:MAIN:SCAL?'))
    print(f"scope {tb:g} s/div -> {tb*10*1e6:.2f} us window, "
          f"{tb*10/1000*1e9:.1f} ns/sample")

    p = Pulser().open()
    print()
    print(f"{'rise':>7} {'vendor':>9} {'ours':>9} {'ratio':>7} {'edge/win':>9}")
    print("-" * 48)

    results = []
    for q in sorted(refs):
        if q == 0:
            continue                      # we cannot request 0
        p.set_pulse(rate_hz=100.0, amplitude_v=0.215, decay_us=50.0,
                    rise_us=q / 1000.0)
        time.sleep(2.0)
        sc.set_trigger_level(0.4 * 0.215 / 1.0)   # ~40% of the vendor amplitude
        time.sleep(1.5)
        rt = sc.qf(':MEAS:ITEM? RTIM,CHAN1')
        try:
            t, v = sc.waveform(1)
            prof, span, amp = edge_profile(t, v)
        except Exception:
            prof = span = amp = None
        vend = float(refs[q]['meta']['meas']['RTIM']) * 1e9
        frac = (rt / (tb * 10) * 100) if rt else None
        print(f"{q:>5.0f}ns {vend:>7.1f}ns "
              f"{(('%.1f ns' % (rt*1e9)) if rt else '-'):>9} "
              f"{(('%.2f' % (rt*1e9/vend)) if rt else '-'):>7} "
              f"{(('%.0f%%' % frac) if frac else '-'):>9}")
        results.append((q, vend, rt * 1e9 if rt else None, prof))

    # shape comparison on whichever point both sides resolved
    print("\nnormalised edge shape, vendor vs ours (10% -> 90%):")
    for q, vend, ours, prof in results:
        ref = refs[q]
        rprof, _, _ = edge_profile(ref['t'], ref['v'])
        if not prof or not rprof:
            continue
        rms = math.sqrt(sum((a - b) ** 2 for a, b in zip(prof, rprof)) / len(prof))
        bar = ''.join('#' if abs(a - b) < 0.05 else '.'
                      for a, b in zip(prof, rprof))
        print(f"  {q:>5.0f} ns  rms diff {rms:.3f}  [{bar}]")
    print("  ('#' = within 5% at that point along the edge, '.' = diverges)")

    p.close()
    sc.close()


if __name__ == '__main__':
    main()
