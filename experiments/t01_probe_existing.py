#!/usr/bin/env python3
"""t01 - Probe what the EXISTING linux/dt5810.py actually writes to the FPGA.

Runs the existing bringup() + set_drc_pulse() unchanged, then reads back the
registers that decide which shape datapath is active.

Manual ground truth (UM5312 rev5):
  Sec 10 "Shape Datapath": three datapaths -- custom(memory), digital RC, pulsed reset.
  Sec 12 "Transistor reset": pre-amp mode {continuous reset | pulsed reset};
         pulsed reset = integrator, staircase output, resets at max -> A RAMP.
  => reg 0x300010 (ConfigureTR enable) selects pulsed-reset. For Digital RC it MUST be 0.
"""
import sys, time
sys.path.insert(0, '/home/ryan/caen_signal_emulator/linux')
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')   # archived: modules live in the parent
from dt5810 import DT5810, _reg

CH = 0
WATCH = [
    (0x00300010, "TR enable  (1 = PULSED RESET integrator, 0 = Digital RC)"),
    (0x0030000b, "DRC enable (C# IENABLE = -1 = 0xFFFFFFFF)"),
    (0x0030000d, "rise prescaler disable"),
    (0x00300014, "prescaler"),
    (0x00300012, "pulsed-reset RAMP AMPLITUDE (only valid when TR=1)"),
    (0x00300013, "pulsed-reset AMPLITUDE SCALING (only valid when TR=1)"),
    (0x00300000, "IIR coeff a^1"),
    (0x00300005, "IIR coeff b^1"),
    (0x020f0004, "EnergyMode (0=Fixed)"),
    (0x020f0005, "ENERGY value (= LSB x 2)"),
    (0x0100000a, "TimeMode (0=Constant)"),
    (0x01000009, "timebase PERIOD"),
    (0x0f000001, "digital gain"),
    (0x0f000000, "digital offset"),
    (0x0f000002, "channel enable"),
    (0x01c00006, "RUN gate"),
]


def dump(d, tag):
    print(f"\n--- register readback: {tag} ---")
    for off, name in WATCH:
        try:
            raw = d.rd(_reg(CH, off), 1)
            import struct
            val = None
            for o in range(0, max(0, len(raw) - 3)):
                w = struct.unpack_from('<I', raw, o)[0]
                if w not in (0xFFFFABBA, 0xFFABBAFF, 0xABBAFFFF, 0x0):
                    val = w
                    break
            if val is None and len(raw) >= 4:
                val = struct.unpack_from('<I', raw, 0)[0]
            print(f"  0x{off:08x} = 0x{val:08x} ({val:>11d})  {name}" if val is not None
                  else f"  0x{off:08x} = <no data>   {name}")
        except Exception as e:
            print(f"  0x{off:08x} = <read error {e}>  {name}")


d = DT5810()
d.open()
print("opened:", d.board_id())
d.bringup()
print("bringup done:", d.board_id())
dump(d, "after bringup()")

print("\n>>> calling EXISTING set_drc_pulse(rise_ns=10, decay_us=50, energy=4000, rate_hz=1000)")
res = d.set_drc_pulse(rise_ns=10, decay_us=50.0, energy=4000, rate_hz=1000.0)
print("    returned:", res)
time.sleep(0.5)
dump(d, "after set_drc_pulse()")

print("\n--- live stats (0x3f002=cps, 0x3f004=events) ---")
import struct
for off, nm in ((0x3f002, "measured rate"), (0x3f004, "event count")):
    raw = d.rd(_reg(CH, off), 1)
    print(f"  0x{off:05x} {nm}: {raw.hex()}")

d.close()
print("\nclosed.")
