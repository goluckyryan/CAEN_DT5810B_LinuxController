#!/usr/bin/env python3
"""t25 - Where is true zero skew, and what stays independent in mode 2?

t24 established the correlation block works and the delay scale is 0.8 ns/count
(one 1.25 GS/s DAC sample). Two things left before it can go in the GUI:

1. At delay register 0 the channels are NOT aligned -- CH1 measured +54 ns, i.e.
   CH2 leads. A fixed pipeline skew. Find the count that zeroes it so "0 ns" in
   the GUI means simultaneous.
2. Confirm what mode 2 leaves independent. The manual says amplitude/shape stay
   per-channel and only the timebase is shared, so CH2's RATE should now be
   ignored (it follows CH1). Verify both claims rather than trusting them.

Scope: triggers CH2, :WAV:SOUR CHAN1, so t=0 is CH2's edge (see t24).
"""
import sys, time

sys.path.insert(0, '/home/ryan/caen_signal_emulator/linux')
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')
from pulser import Pulser
from scope import Scope
from t24_channel_delay import (edge_time, measure, R_CORR_MODE, R_CORR_DELAY,
                               R_TB_MUX_CH2, MODE_TIMEBASE, MODE_DISABLED)


def main():
    sc = Scope(timeout=8)
    p = Pulser().open()
    common = dict(rate_hz=1000.0, decay_us=2.0, rise_us=0.1, baseline_v=0.0)
    p.set_pulse(amplitude_v=1.0, ch=0, **common)
    p.set_pulse(amplitude_v=0.6, ch=1, **common)
    p.d.wr(R_CORR_MODE, MODE_TIMEBASE)
    p.d.wr(R_TB_MUX_CH2, 1)
    time.sleep(2.5)

    print("--- 1. fine scan for true zero skew ---")
    print("  (CH1 edge > 0 means CH1 is LATE; raising the delay retards CH2)")
    rows = []
    for counts in (0, 25, 50, 75, 100, 125, 150):
        p.d.wr(R_CORR_DELAY, counts)
        time.sleep(1.6)
        e, a, n = measure(sc, 7)
        rows.append((counts, e))
        print(f"  counts {counts:>4} ({counts*0.8:>5.0f} ns)  CH1 edge "
              f"{('%+7.1f ns' % (e*1e9)) if e else 'not found':>12}  ({n}/7)")

    good = [(c, e) for c, e in rows if e is not None]
    if len(good) >= 3:
        n = len(good)
        sx = sum(c for c, _ in good); sy = sum(e for _, e in good)
        sxx = sum(c * c for c, _ in good); sxy = sum(c * e for c, e in good)
        slope = (n * sxy - sx * sy) / (n * sxx - sx * sx)
        icept = (sy - slope * sx) / n
        zero = -icept / slope
        print(f"\n  fit: edge = {icept*1e9:+.1f} ns {slope*1e9:+.4f} ns/count")
        print(f"  => zero skew at {zero:.1f} counts = {zero*0.8:.0f} ns of delay")
        print(f"  => at register 0 the channels are {icept*1e9:+.0f} ns apart")

    print("\n--- 2. is CH2's amplitude still independent? ---")
    for amp2 in (0.6, 0.9, 0.4):
        p.set_pulse(amplitude_v=amp2, ch=1, **common)
        # set_pulse rewrites the mux, so restore the correlation routing
        p.d.wr(R_TB_MUX_CH2, 1)
        time.sleep(2.0)
        v2 = sc.qf(':MEAS:ITEM? VMAX,CHAN2')
        v1 = sc.qf(':MEAS:ITEM? VMAX,CHAN1')
        print(f"  CH2 set {amp2:.2f} V -> CH2 reads {v2:.3f} V   "
              f"(CH1 unchanged at {v1:.3f} V)")

    print("\n--- 3. does CH2's rate now follow CH1? ---")
    p.set_pulse(amplitude_v=0.6, ch=1, **common)
    p.d.wr(R_TB_MUX_CH2, 1)
    for r1 in (1000.0, 3000.0):
        p.set_pulse(amplitude_v=1.0, ch=0, **dict(common, rate_hz=r1))
        time.sleep(2.2)
        f1 = sc.qf(':MEAS:ITEM? FREQ,CHAN1')
        f2 = sc.qf(':MEAS:ITEM? FREQ,CHAN2')
        print(f"  CH1 rate set {r1:>6.0f} Hz -> CH1 {f1 if f1 else 0:>8.1f} Hz, "
              f"CH2 {f2 if f2 else 0:>8.1f} Hz  (CH2 still programmed 1000 Hz)")

    p.d.wr(R_CORR_DELAY, 0)
    p.d.wr(R_CORR_MODE, MODE_DISABLED)
    p.d.wr(R_TB_MUX_CH2, 0)
    p.close(); sc.close()


if __name__ == '__main__':
    main()
