#!/usr/bin/env python3
"""1 kHz / 1 V / 1 us rise / 100 us decay, 0 V baseline -- AWG mode.

Ryan's insight: the ~318 Hz lock is a PULSER-datapath property. ChannelMode
(0xFA00100A) selects Pulser vs AWG, and in AWG mode the rate is not a timebase
register at all:

    rate = 312.5 MHz / (DataLen * ClockPerStep)

Verified on hardware: DataLen=1008, CPS=31 -> 10 kHz predicted, 10.16 kHz measured.
DataLen=10080, CPS=31 -> 1.000 kHz.

AWG MODE BYPASSES THE ANALOG-STAGE REGISTERS. Measured 2026-09-23:
  * gain   0x0f000001 - no effect
  * offset 0x0f000000 - no effect (baseline unchanged from 0 to -20000)
  * invert 0x0f000004 - no effect (identical trace for 0 and 1)
So amplitude, polarity AND baseline all have to be baked into the sample array.

Transfer measured at ClockPerStep=31, 1 MOhm:
    output_volts = -1.296e-4 * array_code  +  (-0.12 V)
i.e. the output is INVERTED with respect to the array, and a zero array sits at
about -0.12 V. Both are compensated below, so a positive array `shape` in 0..1
comes out as a positive pulse on a 0 V baseline.
"""
import math, sys, time
sys.path.insert(0, '/home/ryan/caen_signal_emulator/linux')
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')   # archived: modules live in the parent
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')
from dt5810 import DT5810, _reg
from scope import Scope

FREQ_CLK = 312.5e6
CPS = 31                 # ClockPerStep -> dt = 99.2 ns/sample
DLEN = 10080             # -> 312.5e6/(10080*31) = 1000.06 Hz
RISE_US = 1.0
DECAY_US = 100.0
TARGET_V = 1.0

V_PER_COUNT = 1.296e-4   # |slope| of output vs array code, at 1 MOhm
ZERO_ARRAY_V = -0.12     # output when the array is all zeros
CH = 0


def build(n, dt_s, rise_us, decay_us, peak_counts, dc_counts):
    """Shaped exponential, emitted NEGATIVE so the inverted output is positive."""
    tau_r = (rise_us * 1e-6) / 2.197      # 10-90% of a single pole = 2.197 tau
    tau_d = decay_us * 1e-6
    raw = [(1.0 - math.exp(-(i * dt_s) / tau_r)) * math.exp(-(i * dt_s) / tau_d)
           for i in range(n)]
    m = max(raw) or 1.0
    return [int(round(dc_counts - peak_counts * (x / m))) for x in raw]


def characterise(sc):
    time.sleep(2.5)
    m = sc.meas(1)
    if m['VMIN'] is not None and m['VMAX'] is not None:
        sc.set_trigger_level((m['VMIN'] + m['VMAX']) / 2.0)
        time.sleep(1.8)
        m = sc.meas(1)
    t, v = sc.waveform(1)
    dt = t[1] - t[0]
    base = min(v)
    amp = max(v) - base
    ipk = max(range(len(v)), key=lambda i: v[i])
    lo, hi = base + 0.1 * amp, base + 0.9 * amp
    i10 = i90 = None
    for i in range(ipk, 0, -1):
        if v[i] >= hi and i90 is None:
            i90 = i
        if v[i] <= lo:
            i10 = i
            break
    rise = (i90 - i10) * dt if (i10 is not None and i90 is not None and i90 > i10) else None
    tau = None
    v1 = v[ipk] - base
    if v1 > 0:
        for j in range(ipk + 3, len(v)):
            dv = v[j] - base
            if dv <= 0:
                continue
            if dv < v1 * 0.2:
                tau = (j - ipk) * dt / math.log(v1 / dv)
                break
    return m, base, amp, rise, tau, dt


def main():
    dt_s = CPS / FREQ_CLK
    rate = FREQ_CLK / (DLEN * CPS)
    peak_counts = int(round(TARGET_V / V_PER_COUNT))
    dc_counts = int(round(ZERO_ARRAY_V / V_PER_COUNT))   # lifts baseline to 0 V

    print(f"AWG: CPS={CPS} ({dt_s*1e9:.1f} ns/sample), DataLen={DLEN}")
    print(f"     rate {rate:.2f} Hz, period {DLEN*dt_s*1e6:.1f} us")
    print(f"     rise {RISE_US} us = {RISE_US*1e-6/dt_s:.1f} samples, "
          f"decay {DECAY_US} us = {DECAY_US*1e-6/dt_s:.0f} samples")
    print(f"     peak {peak_counts} counts, dc {dc_counts} counts")

    sc = Scope()
    print(f"\nscope: {float(sc.q(':TIM:MAIN:SCAL?'))*10*1e6:.0f} us window, "
          f"{sc.q(':CHAN1:SCAL?')} V/div")

    d = DT5810()
    d.open()
    d.bringup()

    peak = peak_counts
    dc = dc_counts
    for it in range(4):
        pts = build(DLEN, dt_s, RISE_US, DECAY_US, peak, dc)
        d._program_ddr(pts)
        d._awg_enable(CPS, CH)
        m, base, amp, rise, tau, dt = characterise(sc)
        print(f"  it{it}: peak={peak:<7} dc={dc:<7} base={base:+.4f} V "
              f"amp={amp:.4f} V")
        if abs(amp - TARGET_V) < 0.03 and abs(base) < 0.02:
            break
        if amp > 0.05:
            peak = int(round(peak * TARGET_V / amp))
        # output is INVERTED wrt the array, so to RAISE the baseline dc must go
        # MORE negative. (Getting this sign backwards makes the loop diverge.)
        dc = int(round(dc + base / V_PER_COUNT))

    m, base, amp, rise, tau, dt = characterise(sc)
    print("\n============== FINAL (AWG mode) ==============")
    print(f"  rate        {rate:.1f} Hz commanded "
          f"(formula verified: DataLen=1008 -> 10.16 kHz measured vs 10.0 predicted)")
    print(f"  amplitude   {amp:.4f} V        (target {TARGET_V} V)")
    print(f"  baseline    {base:+.4f} V       (target 0 V)")
    print(f"  rise 10-90% {(('%.2f us' % (rise*1e6)) if rise else '-')}"
          f"         (target {RISE_US} us; scope resolution {dt*1e9:.0f} ns)")
    print(f"  decay tau   {(('%.1f us' % (tau*1e6)) if tau else '-')}"
          f"        (target {DECAY_US} us)")
    print(f"\n  settings: ChannelMode=AWG ClockPerStep={CPS} DataLen={DLEN} "
          f"peak={peak} dc={dc}")
    d.close()
    sc.close()


if __name__ == '__main__':
    main()
