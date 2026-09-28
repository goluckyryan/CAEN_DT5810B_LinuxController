#!/usr/bin/env python3
"""t05 - Is the FPGA alive? Decisive test: does the board actually OUTPUT anything?

Register readback is inconclusive (config space is write-only / returns filler).
So: drive the board with the existing set_detector_pulse() -- the one path the
project notes say definitely produced a real ~1.1 V exponential pulse -- and ask
the scope, READ-ONLY, what it sees.

Scope: Rigol DHO4804 @ 192.168.2.200:5555. We only QUERY. No settings are changed.
"""
import socket, sys, time
sys.path.insert(0, '/home/ryan/caen_signal_emulator/linux')
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')   # archived: modules live in the parent

SCOPE = ('192.168.2.200', 5555)


def q(cmd, timeout=6):
    s = socket.socket()
    s.settimeout(timeout)
    s.connect(SCOPE)
    s.sendall((cmd + '\n').encode())
    try:
        r = s.recv(65536).decode(errors='replace').strip()
    except socket.timeout:
        r = '<timeout>'
    s.close()
    return r


def scope_snapshot(tag):
    print(f"\n--- scope ({tag}) ---")
    for ch in (1, 2):
        vpp = q(f':MEAS:ITEM? VPP,CHAN{ch}')
        vmax = q(f':MEAS:ITEM? VMAX,CHAN{ch}')
        vmin = q(f':MEAS:ITEM? VMIN,CHAN{ch}')
        frq = q(f':MEAS:ITEM? FREQ,CHAN{ch}')
        disp = q(f':CHAN{ch}:DISP?')
        print(f"  CH{ch}: disp={disp:>4}  Vpp={vpp:>14}  Vmax={vmax:>14}  "
              f"Vmin={vmin:>14}  Freq={frq:>14}")


print("scope IDN:", q('*IDN?'))
print("timebase :", q(':TIM:MAIN:SCAL?'), "s/div")
scope_snapshot("BEFORE - board idle")

from dt5810 import DT5810
d = DT5810()
d.open()
d.bringup()
print("\nbringup done. board_id():", d.board_id())

print(">>> set_detector_pulse(width_us=300, decay_us=50, gain=1107, offset=-55465, invert=1)")
r = d.set_detector_pulse(width_us=300, decay_us=50, gain=1107, offset=-55465, invert=1)
print("    returned:", r)
time.sleep(2.0)
scope_snapshot("AFTER set_detector_pulse")

print("\n>>> run gate OFF (0x01c00006 = 0)")
from dt5810 import _reg
d.wr(_reg(0, 0x01c00006), 0)
time.sleep(1.5)
scope_snapshot("run gate OFF")

print("\n>>> run gate ON again")
d.wr(_reg(0, 0x01c00006), 1)
time.sleep(1.5)
scope_snapshot("run gate ON")

d.close()
print("\nclosed.")
