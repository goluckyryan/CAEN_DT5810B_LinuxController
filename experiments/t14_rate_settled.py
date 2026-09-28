#!/usr/bin/env python3
"""t14 - Settle the rate question now that the window is 500 us.

Sweep the timebase period register and count pulse spacing in the trace. At
5-20 kHz several pulses fit a 500 us window, so the rate is directly measurable
for the first time.

Also checks TimebaseMux (0x0f000005), which the C# Update_Muxes writes and which
may gate whether the constant-rate generator is actually the trigger source.
"""
import math, sys, time
sys.path.insert(0, '/home/ryan/caen_signal_emulator/linux')
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')   # archived: modules live in the parent
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')
from dt5810 import DT5810, _reg
from scope import Scope
from analyse_trace import find_peaks

CH = 0


def spacing(sc):
    t, v = sc.waveform(1)
    dt = t[1] - t[0]
    peaks, base, amp = find_peaks(v)
    if len(peaks) >= 2:
        gaps = [(peaks[i + 1] - peaks[i]) * dt for i in range(len(peaks) - 1)]
        g = sum(gaps) / len(gaps)
        return amp, len(peaks), g
    return amp, len(peaks), None


sc = Scope()
print("window:", float(sc.q(':TIM:MAIN:SCAL?')) * 10 * 1e6, "us  trig:",
      sc.q(':TRIG:STAT?'), "\n")

d = DT5810()
d.open()
d.bringup()
# short pulses so many fit the window
d.set_detector_pulse(width_us=20, decay_us=2, gain=1107, offset=0, invert=1)

print(f"{'period':>9} {'mux':>4} {'peaks':>6} {'spacing us':>11} {'rate Hz':>10} "
      f"{'derived clock':>14}")
print("-" * 62)
for mux in (0, 4):
    d.wr(_reg(CH, 0x0f000005), mux)
    for P in (10000, 25000, 50000):
        d.wr(_reg(CH, 0x0100000a), 0)
        d.wr(_reg(CH, 0x01000009), P)
        d.wr(_reg(CH, 0x01c00003), 1)
        d.wr(_reg(CH, 0x01c00006), 1)
        time.sleep(2.5)
        amp, npk, g = spacing(sc)
        if g:
            rate = 1 / g
            print(f"{P:>9} {mux:>4} {npk:>6} {g*1e6:>11.2f} {rate:>10.1f} "
                  f"{rate*(P+1)/1e6:>13.2f}M")
        else:
            print(f"{P:>9} {mux:>4} {npk:>6} {'-':>11} {'-':>10} {'-':>14}")

d.close()
sc.close()
