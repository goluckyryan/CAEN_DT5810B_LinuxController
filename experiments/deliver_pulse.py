#!/usr/bin/env python3
"""Deliver the requested pulse: 1 V, 1 us rise, 100 us decay.

Uses the SHAPE-RAM (memory) datapath -- the only one that drives the DAC on this
board (Digital RC produces nothing, see ../docs/FINDINGS.md C3).

Knobs, all established by measurement on 2026-09-23:
  width_us   shape window; 600 us makes one RAM sample ~= 700 ns
  decay_us   tracks the achieved tau almost 1:1 at width=600 (100 -> 100.1 us)
  corner     0x50f005, the interpolator corner point. The vendor code leaves it
             at 0, which collapses the fine rise region and fixes the rise at
             ~1.5 us. Raising it shortens the rise but steals samples from the
             tail, so decay_us must be compensated.
  gain       0x0f000001, linear on amplitude

RATE IS NOT CONTROLLABLE. See ../docs/FINDINGS.md: the period register 0x01000009, the
alpha register 0x01000006, TimeMode, the LFSR strobe and the deadtime registers
were all swept with no effect; the rate stays at its fixed ~318 Hz. The 1 kHz
part of the request is NOT met.
"""
import math, sys, time
sys.path.insert(0, '/home/ryan/caen_signal_emulator/linux')
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')   # archived: modules live in the parent
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')
from dt5810 import DT5810, _reg
from scope import Scope

WIDTH_US = 600.0
TARGET_V = 1.0
TARGET_TAU_US = 100.0

# Digital offset 0x0f000000 that puts the baseline at 0 V.
# The register is linear at 3.234e-5 V/count, but the manual (sec 12) notes the
# offset is applied BEFORE the gain stage, so this value is only valid at
# GAIN=1107. Change the gain and this must be recalibrated (see t16_zero_offset.py).
OFFSET_ZERO_BASELINE = -55934

# Calibrated 2026-09-23 against the Rigol at 50 us/div, 1 V/div, 1 MOhm.
DECAY_ARG = 102.0     # -> tau ~= 100 us at width_us=600, corner=4
CORNER    = 4         # 0x50f005; 0/3 give a ~1.5 us rise, >=4 gives <=0.5 us
GAIN      = 1107      # -> 0.99 V amplitude


def apply(d, decay_us, corner, gain, width_us=WIDTH_US, ch=0,
          offset=OFFSET_ZERO_BASELINE):
    d.set_detector_pulse(width_us=width_us, decay_us=decay_us,
                         gain=gain, offset=offset, invert=1, ch=ch)
    if corner:
        for sid in range(16):
            b = ((ch << 8) | sid) << 20
            d.wr(b + 0x50f000, 1)
            d.wr(b + 0x50f005, corner)
            d.wr(b + 0x50f000, 1)
    d.wr(_reg(ch, 0x01c00003), 1)
    d.wr(_reg(ch, 0x01c00006), 1)


def characterise(sc):
    time.sleep(2.2)
    m = sc.meas(1)
    sc.set_trigger_level((m['VMIN'] + m['VMAX']) / 2.0)
    time.sleep(1.8)
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
    for j in range(ipk + 10, len(v)):
        if v[j] - base < v1 * 0.2:
            tau = (j - ipk) * dt / math.log(v1 / (v[j] - base))
            break
    return amp, rise, tau, dt


def main():
    sc = Scope()
    print("scope window:", float(sc.q(':TIM:MAIN:SCAL?')) * 10 * 1e6, "us,",
          sc.q(':CHAN1:SCAL?'), "V/div")
    d = DT5810()
    d.open()
    d.bringup()

    # Calibrated constants, not re-derived per run. The tau measurement has a
    # few-percent run-to-run spread, and an auto-compensation loop just chases
    # that noise (one run landed on 95 us by "correcting" a low reading).
    decay_arg, corner, gain = DECAY_ARG, CORNER, GAIN
    print(f"\napplying calibrated settings: decay_us={decay_arg} "
          f"corner={corner} gain={gain} offset={OFFSET_ZERO_BASELINE}")

    apply(d, decay_arg, corner, gain)
    amp, rise, tau, dt = characterise(sc)
    print(f"  amp={amp:.4f} V  rise="
          f"{(('%.2f us' % (rise*1e6)) if rise else '-')}  "
          f"tau={(('%.1f us' % (tau*1e6)) if tau else '-')}")

    apply(d, decay_arg, corner, gain)
    amp, rise, tau, dt = characterise(sc)
    base = sc.meas(1)['VMIN'] or 0.0
    print("\n================ FINAL ================")
    print(f"  amplitude   {amp:.4f} V        (target {TARGET_V} V)")
    print(f"  rise 10-90% {rise*1e6 if rise else float('nan'):.2f} us"
          f"        (target 1 us; scope resolution {dt*1e9:.0f} ns at this timebase)")
    print(f"  decay tau   {tau*1e6 if tau else float('nan'):.1f} us"
          f"       (target {TARGET_TAU_US} us)")
    print(f"  baseline    {base:.4f} V        (target 0 V)")
    print(f"  rate        NOT CONTROLLABLE - fixed ~318 Hz (target 1 kHz)")
    print(f"\n  settings: width_us={WIDTH_US} decay_us={decay_arg:.0f} "
          f"corner={corner} gain={gain} offset={OFFSET_ZERO_BASELINE} invert=1")
    d.close()
    sc.close()


if __name__ == '__main__':
    main()
