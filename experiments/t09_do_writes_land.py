#!/usr/bin/env python3
"""t09 - CONTROL EXPERIMENT: do our register writes affect the output at all?

t08 showed Vpp/Vmin/Vmax identical to 3 decimals across four different values of
the timebase period register. Either the rate register does nothing, or none of
our writes are landing and we are staring at a stale signal.

This is the experiment that should have come first. Poke registers whose effect
is unambiguous and see whether the analog output moves:

  run gate   0x01c00006  1 -> 0 -> 1     output should stop and restart
  ch enable  0x0f000002  1 -> 0 -> 1     output should mute and return
  gain       0x0f000001  G -> 2G -> G    amplitude should roughly double
  offset     0x0f000000  0 -> big        baseline should shift

If none of these move the trace, the FPGA is not receiving configuration and
every other result today is meaningless.
"""
import sys, time
sys.path.insert(0, '/home/ryan/caen_signal_emulator/linux')
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')   # archived: modules live in the parent
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')
from dt5810 import DT5810, _reg
from scope import Scope

CH = 0
sc = Scope()
print("scope:", sc.settings(), "\n")

d = DT5810()
d.open()
d.bringup()
time.sleep(1.0)


def snap(tag):
    time.sleep(1.5)
    m = sc.meas(1)
    f = lambda k: ('%.4f' % m[k]) if m[k] is not None else '  none'
    print(f"  {tag:<34} Vpp={f('VPP'):>8}  Vmin={f('VMIN'):>8}  "
          f"Vmax={f('VMAX'):>8}  Vavg={f('VAVG'):>8}")
    return m


print("=== 1. run gate 0x01c00006 ===")
d.wr(_reg(CH, 0x01c00006), 1); a = snap("run gate ON")
d.wr(_reg(CH, 0x01c00006), 0); b = snap("run gate OFF")
d.wr(_reg(CH, 0x01c00006), 1); c = snap("run gate ON again")

print("\n=== 2. channel enable 0x0f000002 ===")
d.wr(_reg(CH, 0x0f000002), 1); snap("ch enable = 1")
d.wr(_reg(CH, 0x0f000002), 0); e = snap("ch enable = 0")
d.wr(_reg(CH, 0x0f000002), 1); snap("ch enable = 1 again")

print("\n=== 3. digital gain 0x0f000001 ===")
for g in (1107, 2214, 500, 1107):
    d.wr(_reg(CH, 0x0f000001), g)
    snap(f"gain = {g}")

print("\n=== 4. digital offset 0x0f000000 ===")
for off in (0, 20000, -20000, 0):
    d.wr(_reg(CH, 0x0f000000), off & 0xFFFFFFFF)
    snap(f"offset = {off}")

print("\n=== 5. AWG/pulser mode 0xFA00100A ===")
for mode in (0, 1, 0):
    d.wr(0xFA00100A, mode)
    snap(f"ChannelMode = {mode} ({'Pulser' if mode == 0 else 'AWG'})")

d.close()
sc.close()

print("\n=== VERDICT ===")
moved = []
if a['VPP'] is not None and b['VPP'] is not None:
    if abs(a['VPP'] - b['VPP']) > 0.05:
        moved.append("run gate")
if e['VPP'] is not None and a['VPP'] is not None:
    if abs(a['VPP'] - e['VPP']) > 0.05:
        moved.append("channel enable")
print("  Controls that visibly moved the output:", moved or "NONE")
if not moved:
    print("  => writes are NOT reaching the FPGA. Everything else today is moot")
    print("     until the config path is fixed.")
