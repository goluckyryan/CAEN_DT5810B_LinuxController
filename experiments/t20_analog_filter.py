#!/usr/bin/env python3
"""t20 - Turn the 30 MHz analog filter OFF. READY TO RUN, NOT YET RUN.

Written 2026-09-24 while the board was unplugged. Decoded from FUN_10007650
(amux_decomp.txt), which builds 0xF00000C2 as:

    ch0 (param_1==0):  value = (cached & 0x3) + (filter + analogsel*2) * 4
    ch1 (param_1==1):  value = (cached & 0xc) +  analogsel + filter*2
    then writes analogsel to 0xF0000043 (ch0) / 0xF0000044 (ch1)

So bits 2-3 are CH0 = filter + analogsel*2, bits 0-1 are CH1 = analogsel + filter*2.
INVERT IS NOT IN THIS REGISTER (it is 0x0f000004). Both KNOWLEDGE_BASE.md's
"(range&3)+(invert<<2)+(filter<<3)" and DRC_SESSION_CHECKPOINT.md's "0xF=pos,
0xB=neg" are wrong.

    0xF -> ch0 filter ON,  analogsel 1      <- what we have been running
    0xB -> ch0 filter OFF, analogsel 1      <- same output, filter removed
    0x7 -> ch0 filter ON,  analogsel 0      <- DIFFERENT OUTPUT CONNECTOR
                                               (this is what I tried before and
                                               mistook for "the output died")

Manual Tab 9.1, High Speed output:  filter ON -> 25 ns rise, filter OFF -> 1 ns.
If that holds, 0xB should take the rise floor from ~50 ns down toward the
interpolator limit, which is what is needed for a 30 ns edge.

WHAT TO CHECK WHEN RUNNING
  * 0x0B should keep the signal on the SAME connector, just with a faster edge
    and more noise. If the signal vanishes at 0x0B, the field order is reversed
    and 0x07 is the filter-off value instead -- try that before concluding.
  * scope wants ~100 ns/div (1 ns/sample) and ~0.5 V/div.
  * expect visibly more high-frequency noise with the filter off; the manual says
    the filter cuts noise by a factor of 5.
"""
import math, sys, time
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')
from pulser import Pulser
from scope import Scope

AMUX = {
    0x0F: "filter ON,  analogsel 1  (current default)",
    0x0B: "filter OFF, analogsel 1  (target: fast edge, same connector)",
    0x07: "filter ON,  analogsel 0  (other connector - expect no signal here)",
    0x03: "filter OFF, analogsel 0  (other connector, filter off)",
}


def main():
    sc = Scope()
    tb = float(sc.q(':TIM:MAIN:SCAL?'))
    print(f"scope {tb*1e9:.0f} ns/div -> {tb*10/1000*1e12:.0f} ps/sample, "
          f"{sc.q(':CHAN1:SCAL?')} V/div")
    if tb > 5e-7:
        print("  WARNING: too coarse to resolve a fast edge. Want ~100 ns/div.")

    p = Pulser().open()
    print()
    print(f"{'0xC2':>6} {'RTIM':>10} {'Vpp':>9}  meaning")
    print("-" * 74)
    for val, meaning in AMUX.items():
        p.set_pulse(rate_hz=1000.0, amplitude_v=1.0, decay_us=50.0, rise_us=0.1)
        p.d.wr(0xF00000C2, val)
        time.sleep(2.0)
        sc.set_trigger_level(0.4)
        time.sleep(1.5)
        r = sc.qf(':MEAS:ITEM? RTIM,CHAN1')
        v = sc.qf(':MEAS:ITEM? VPP,CHAN1')
        print(f"  0x{val:02X} {(('%.1f ns' % (r*1e9)) if r else '-'):>10} "
              f"{(('%.3f V' % v) if v and v < 30 else '-'):>9}  {meaning}")

    # restore the known-good value before leaving
    p.d.wr(0xF00000C2, 0x0F)

    print()
    print("If 0x0B kept the signal and shortened RTIM, re-run the rise floor sweep")
    print("with the filter off -- the ~50 ns floor should drop.")
    p.close()
    sc.close()


if __name__ == '__main__':
    main()
