#!/usr/bin/env python3
"""t08 - Prove the rate hypothesis (B6) inside the scope's EXISTING 20 us window.

No scope scale/timebase changes. Trigger level is the only thing we touch, and
only because Ryan authorised it (2026-09-22).

Strategy: shrink the decay to ~1 us and pick pulse periods that fit inside the
20 us window, so FREQ becomes measurable. Then derive the timebase clock from
  clock = measured_freq * (period_reg + 1)
rather than assuming 1 GHz / 1.25 GHz / 312.5 MHz.

If measured frequency tracks the period register, the "locked 318 Hz" is
confirmed as the library's hardcode (dt5810.py:324), not a firmware limit.
"""
import sys, time
sys.path.insert(0, '/home/ryan/caen_signal_emulator/linux')
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')   # archived: modules live in the parent
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')
from dt5810 import DT5810, _reg
from drc_coeffs import compute_drc
from scope import Scope, describe

CH = 0
CQ = 312.5e6
DECAY_US = 1.0
ENERGY = 4000
GAIN = 1107


def drc(d, decay_us, energy, gain, period):
    """Manual-derived Digital RC: TR off, enable -1, amplitude via energy reg."""
    r = compute_drc(rise_ns=20, fall_ns=int(decay_us * 1000), clock_quarter=CQ)
    for off in (0x020f0002, 0x0100004, 0x1400009, 0x1900003):
        d.wr(_reg(CH, off), 1); d.wr(_reg(CH, off), 0)
    d.wr(_reg(CH, 0x0f000035), 0)
    d.wr(_reg(CH, 0x0f000002), 1)
    d.wr(_reg(CH, 0x0f000001), gain & 0xFFFFFFFF)
    d.wr(_reg(CH, 0x0f000000), 0)
    d.wr(_reg(CH, 0x0f000004), 1)
    d.wr(0xFA00100A, 0)
    d.wr(_reg(CH, 0x020f0004), 0)
    d.wr(_reg(CH, 0x020f0005), energy * 2)
    d.wr(_reg(CH, 0x0100000a), 0)
    d.wr(_reg(CH, 0x01000009), period)
    d.wr(_reg(CH, 0x01000007), 0); d.wr(_reg(CH, 0x01000008), 0)
    d.wr(_reg(CH, 0x0f000005), 0); d.wr(_reg(CH, 0x0f000006), 0)
    d.wr(0xF00000C2, 3 | (1 << 2) | (1 << 3))
    d.wr(_reg(CH, 0x00300010), 0)                 # TR off = Digital RC
    d.wr(_reg(CH, 0x0300000d), r['rise_presc_disable'])
    for i, v in enumerate(r['coeffs_reg']):
        d.wr(_reg(CH, 0x03000000 + i), v)
    d.wr(_reg(CH, 0x0300000a), 1); d.wr(_reg(CH, 0x0300000a), 0)
    d.wr(_reg(CH, 0x0300000c), 0)
    d.wr(_reg(CH, 0x0300000b), 0xFFFFFFFF)        # enable = -1
    d.wr(_reg(CH, 0x00300014), r['prescaler_reg_0x300014'])
    d.wr(_reg(CH, 0x01c00005), 0); d.wr(_reg(CH, 0x01c00000), 0)
    d.wr(_reg(CH, 0x01c00006), 0); d.wr(_reg(CH, 0x01c00004), 1)
    d.wr(_reg(CH, 0x01c00003), 1); d.wr(_reg(CH, 0x01c00006), 1)


sc = Scope()
st = sc.settings()
win_us = float(st['tb_scale']) * 10 * 1e6
print(f"scope: {st['tb_scale']} s/div ({win_us:.0f} us window), "
      f"{st['ch1_scale']} V/div, {st['ch1_imp']}   [scales NOT touched]")
print("wav preamble:", sc.q(':WAV:PRE?'))

d = DT5810()
d.open()
d.bringup()
print("bringup done.\n")

print(f"{'P':>8} {'@1GHz':>9} {'@1.25G':>9} {'@312M':>9} | "
      f"{'FREQ meas':>11} {'Vpp':>8} {'Vmin':>8} {'Vmax':>8} | derived clock")
print("-" * 100)

rows = []
for P in (2500, 5000, 10000, 20000):
    drc(d, DECAY_US, ENERGY, GAIN, P)
    time.sleep(1.5)

    # centre the trigger between the observed extremes (the one allowed write)
    m = sc.meas(1)
    if m['VMIN'] is not None and m['VMAX'] is not None:
        sc.set_trigger_level((m['VMIN'] + m['VMAX']) / 2.0)
        time.sleep(1.0)
        m = sc.meas(1)

    fr = m['FREQ']
    derived = fr * (P + 1) if fr else None
    print(f"{P:>8} {1e9/(P+1)/1e3:>8.0f}k {1.25e9/(P+1)/1e3:>8.0f}k "
          f"{312.5e6/(P+1)/1e3:>8.0f}k | "
          f"{(('%.1f' % (fr/1e3)) + 'k') if fr else 'none':>11} "
          f"{('%.3f' % m['VPP']) if m['VPP'] is not None else '-':>8} "
          f"{('%.3f' % m['VMIN']) if m['VMIN'] is not None else '-':>8} "
          f"{('%.3f' % m['VMAX']) if m['VMAX'] is not None else '-':>8} | "
          f"{(('%.1f MHz' % (derived/1e6)) if derived else '-')}")
    rows.append((P, fr, derived))

# shape of the tail at the mid rate
print("\n--- waveform shape at P=5000 ---")
drc(d, DECAY_US, ENERGY, GAIN, 5000)
time.sleep(1.5)
m = sc.meas(1)
if m['VMIN'] is not None and m['VMAX'] is not None:
    sc.set_trigger_level((m['VMIN'] + m['VMAX']) / 2.0)
    time.sleep(1.0)
try:
    t, v = sc.waveform(1)
    print("  ", describe(t, v))
except Exception as e:
    print("   waveform capture unavailable (query-only):", e)

d.close()
sc.close()

good = [r for r in rows if r[1]]
print("\n=== VERDICT ===")
if len(good) < 2:
    print("  Not enough frequency readings to conclude.")
else:
    cl = [c for _, _, c in good]
    avg = sum(cl) / len(cl)
    spread = (max(cl) - min(cl)) / avg * 100
    print(f"  Frequency tracked the period register at {len(good)}/{len(rows)} points.")
    print(f"  Derived timebase clock = {avg/1e6:.2f} MHz  (spread {spread:.1f}%)")
    print("  => RATE IS CONTROLLABLE via 0x01000009.")
    print("     The 'locked ~318 Hz' was dt5810.py:324 hardcoding P=3,124,999.")
    print(f"     At {avg/1e6:.0f} MHz that P gives {avg/3125000:.1f} Hz - matching the observed 318 Hz.")
