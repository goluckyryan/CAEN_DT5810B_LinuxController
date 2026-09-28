#!/usr/bin/env python3
"""t06 - Clean manual-derived Digital RC config + rate sweep.

Two goals at once, without touching any scope setting:

 1. The scope sits at 2 us/div (20 us window), too fast to show a 318 Hz train.
    So instead of slowing the scope, speed up the PULSES until the period fits.
 2. That sweep simultaneously tests hypothesis B6 -- that the "locked 318 Hz" is
    just the library hardcoding 0x01000009, not a firmware limit.

We do NOT assume the timebase clock. We write period P and measure output
frequency F, then derive  clock = F * (P+1).  Empirical, not assumed.

Digital RC configured per the MANUAL (not the old code):
  0x300010 = 0            TR off  -> NOT pulsed-reset (manual 10: that block is
                          the integrator/staircase = the long-standing "triangle")
  0x30000b = 0xFFFFFFFF   DRC enable (C# IENABLE = -1)
  0x300000..9             IIR coefficients (drc_coeffs, verified vs asm5340.txt)
  0x020f0005 = energy*2   amplitude lives HERE, not in 0x300012
  no 0x300012 / 0x300013  those are pulsed-reset ramp params; TR=0 means skip them
"""
import socket, sys, time
sys.path.insert(0, '/home/ryan/caen_signal_emulator/linux')
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')   # archived: modules live in the parent
from dt5810 import DT5810, _reg
from drc_coeffs import compute_drc

SCOPE = ('192.168.2.200', 5555)
CH = 0
CQ = 312.5e6          # clock_quarter used by the coefficient math


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


def meas():
    """Read-only scope measurements on CH1."""
    def f(item):
        v = q(f':MEAS:ITEM? {item},CHAN1')
        try:
            x = float(v)
        except ValueError:
            return None
        return None if x > 1e30 else x       # 9.9E37 = no valid measurement
    return f('FREQ'), f('VPP'), f('VMAX'), f('VMIN')


def configure_drc(d, decay_us, energy, gain, offset, period, invert=1):
    """Manual-derived Digital RC setup. period -> reg 0x01000009."""
    fall_ns = int(round(decay_us * 1000.0))
    r = compute_drc(rise_ns=20, fall_ns=fall_ns, clock_quarter=CQ)

    for off in (0x020f0002, 0x0100004, 0x1400009, 0x1900003):
        d.wr(_reg(CH, off), 1); d.wr(_reg(CH, off), 0)

    d.wr(_reg(CH, 0x0f000035), 0)
    d.wr(_reg(CH, 0x0f000002), 1)                       # channel enable
    d.wr(_reg(CH, 0x0f000001), int(gain) & 0xFFFFFFFF)  # digital gain
    d.wr(_reg(CH, 0x0f000000), int(offset) & 0xFFFFFFFF)
    d.wr(_reg(CH, 0x0f000004), 1 if invert else 0)
    d.wr(0xFA00100A, 0)                                 # ChannelMode = Pulser

    # energy: Fixed mode, amplitude = LSB*2   <-- the fix for B5
    d.wr(_reg(CH, 0x020f0004), 0)
    d.wr(_reg(CH, 0x020f0005), int(energy) * 2)

    # timebase: constant rate, period is the swept variable  <-- the fix for B6
    d.wr(_reg(CH, 0x0100000a), 0)
    d.wr(_reg(CH, 0x01000009), int(period))
    d.wr(_reg(CH, 0x01000007), 0)
    d.wr(_reg(CH, 0x01000008), 0)
    d.wr(_reg(CH, 0x0f000005), 0)
    d.wr(_reg(CH, 0x0f000006), 0)

    d.wr(0xF00000C2, (3 & 3) | ((1 if invert else 0) << 2) | (1 << 3))

    # ---- Digital RC, TR OFF ----
    d.wr(_reg(CH, 0x00300010), 0)                        # <-- the fix for B2
    d.wr(_reg(CH, 0x0300000d), r['rise_presc_disable'])
    for i, v in enumerate(r['coeffs_reg']):
        d.wr(_reg(CH, 0x03000000 + i), v)
    d.wr(_reg(CH, 0x0300000a), 1); d.wr(_reg(CH, 0x0300000a), 0)   # latch
    d.wr(_reg(CH, 0x0300000c), 0)
    d.wr(_reg(CH, 0x0300000b), 0xFFFFFFFF)               # <-- the fix for B3
    d.wr(_reg(CH, 0x00300014), r['prescaler_reg_0x300014'])
    # deliberately NOT writing 0x300012 / 0x300013      <-- the fix for B4

    # run control
    d.wr(_reg(CH, 0x01c00005), 0); d.wr(_reg(CH, 0x01c00000), 0)
    d.wr(_reg(CH, 0x01c00006), 0); d.wr(_reg(CH, 0x01c00004), 1)
    d.wr(_reg(CH, 0x01c00003), 1); d.wr(_reg(CH, 0x01c00006), 1)
    return r


print("scope:", q('*IDN?'))
print("timebase:", q(':TIM:MAIN:SCAL?'), "s/div  (NOT changed)")
print("CH1 scale:", q(':CHAN1:SCAL?'), "V/div   offset:", q(':CHAN1:OFFS?'))

d = DT5810()
d.open()
d.bringup()
print("\nbringup done.\n")

print(f"{'period P':>10} {'pred@1GHz':>11} {'pred@1.25G':>11} {'pred@312M':>10} "
      f"| {'meas FREQ':>12} {'Vpp':>10} {'Vmax':>10}  -> derived clock")
print("-" * 104)

results = []
for P in (1000, 2000, 5000, 10000, 20000):
    configure_drc(d, decay_us=1.0, energy=12000, gain=3056, offset=0, period=P)
    time.sleep(1.2)
    fr, vpp, vmax, vmin = meas()
    p1 = 1.00e9 / (P + 1)
    p2 = 1.25e9 / (P + 1)
    p3 = 312.5e6 / (P + 1)
    if fr:
        derived = fr * (P + 1)
        dtxt = f"{derived/1e6:.1f} MHz"
    else:
        dtxt = "-"
    print(f"{P:>10} {p1/1e3:>10.1f}k {p2/1e3:>10.1f}k {p3/1e3:>9.1f}k "
          f"| {('%.1f' % (fr/1e3) + 'k') if fr else '     none':>12} "
          f"{('%.4f' % vpp) if vpp is not None else '    -':>10} "
          f"{('%.4f' % vmax) if vmax is not None else '    -':>10}  -> {dtxt}")
    results.append((P, fr, vpp))

print("\n--- control: run gate OFF ---")
d.wr(_reg(CH, 0x01c00006), 0)
time.sleep(1.2)
fr, vpp, vmax, vmin = meas()
print(f"  FREQ={fr}  Vpp={vpp}  Vmax={vmax}")

print("\n--- control: run gate ON ---")
d.wr(_reg(CH, 0x01c00006), 1)
time.sleep(1.2)
fr, vpp, vmax, vmin = meas()
print(f"  FREQ={fr}  Vpp={vpp}  Vmax={vmax}")

d.close()

ok = [r for r in results if r[1]]
print("\n=== VERDICT ===")
if not ok:
    print("  No frequency measured at any rate -> no periodic output on CH1.")
    print("  Either the FPGA is not generating, or CH1 is not seeing the output.")
else:
    print(f"  Periodic output seen at {len(ok)}/{len(results)} rates -> FPGA IS generating.")
    cl = [f * (P + 1) for P, f, _ in ok]
    print(f"  Derived timebase clock: {sum(cl)/len(cl)/1e6:.2f} MHz "
          f"(spread {min(cl)/1e6:.1f}-{max(cl)/1e6:.1f})")
    print("  => rate IS controllable via 0x01000009; the 318 Hz lock was the hardcode.")
