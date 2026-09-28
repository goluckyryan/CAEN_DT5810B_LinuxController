#!/usr/bin/env python3
"""t17 - Survey every generating mode against the target spec.

Target: 1 kHz, 1 V, 100 ns rise, 50 us decay, 0 V baseline.

For each mode record: does it output, amplitude, baseline, decay tau, and --
crucially -- whether the RATE RESPONDS TO A COMMAND. Rate responsiveness is
tested by commanding two different rates and seeing if the pulse spacing moves.
A single pulse per window cannot distinguish 318 Hz from 1 kHz, so each mode is
driven at rates fast enough to put several pulses in the window.
"""
import math, sys, time
sys.path.insert(0, '/home/ryan/caen_signal_emulator/linux')
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')   # archived: modules live in the parent
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')
from dt5810 import DT5810, _reg
from drc_coeffs import compute_drc
from scope import Scope

CH = 0
FREQ_CLK = 312.5e6
results = []


def measure(sc, settle=2.3):
    time.sleep(settle)
    m = sc.meas(1)
    if m['VMIN'] is not None and m['VMAX'] is not None:
        sc.set_trigger_level((m['VMIN'] + m['VMAX']) / 2.0)
        time.sleep(1.6)
        m = sc.meas(1)
    t, v = sc.waveform(1)
    dt = t[1] - t[0]
    base, top = min(v), max(v)
    amp = top - base
    if amp < 0.15:
        return dict(amp=amp, base=base, tau=None, npk=0, gap=None, dt=dt,
                    live=False)
    thr = base + 0.5 * amp
    peaks, i, n = [], 0, len(v)
    while i < n:
        if v[i] > thr:
            j = i
            while j < n and v[j] > thr:
                j += 1
            peaks.append(max(range(i, j), key=lambda k: v[k]))
            i = j
        else:
            i += 1
    gap = None
    if len(peaks) >= 2:
        gaps = [(peaks[k + 1] - peaks[k]) * dt for k in range(len(peaks) - 1)]
        gap = sum(gaps) / len(gaps)
    tau = None
    if peaks:
        ipk = peaks[0]
        v1 = v[ipk] - base
        if v1 > 0:
            for j in range(ipk + 3, n):
                dv = v[j] - base
                if dv <= 0:
                    continue
                if dv < v1 * 0.2:
                    tau = (j - ipk) * dt / math.log(v1 / dv)
                    break
    return dict(amp=amp, base=base, tau=tau, npk=len(peaks), gap=gap, dt=dt,
                live=True)


def record(name, r, rate_note):
    results.append((name, r, rate_note))
    print(f"  {name:<40} out={'Y' if r['live'] else 'n':<2} "
          f"amp={r['amp']:.3f} base={r['base']:+.3f} "
          f"tau={(('%.0fus' % (r['tau']*1e6)) if r['tau'] else '-'):>7} "
          f"peaks={r['npk']:<4} {rate_note}")


def shape_ram(d, width_us=300.0, decay_us=50.0, corner=4, gain=1107,
              offset=-55934):
    d.set_detector_pulse(width_us=width_us, decay_us=decay_us, gain=gain,
                         offset=offset, invert=1, ch=CH)
    if corner:
        for sid in range(16):
            b = ((CH << 8) | sid) << 20
            d.wr(b + 0x50f000, 1)
            d.wr(b + 0x50f005, corner)
            d.wr(b + 0x50f000, 1)


def run(d):
    d.wr(_reg(CH, 0x01c00003), 1)
    d.wr(_reg(CH, 0x01c00006), 1)


sc = Scope()
win = float(sc.q(':TIM:MAIN:SCAL?')) * 10
print(f"scope window {win*1e6:.0f} us, {sc.q(':CHAN1:SCAL?')} V/div, "
      f"sample {win/1000*1e9:.0f} ns\n")

d = DT5810()
d.open()
d.bringup()

# ---------- 1. Pulser / shape-RAM / constant rate ----------
print("1. Pulser + shape-RAM + CONSTANT timebase")
gaps = []
for P in (10000, 50000):
    shape_ram(d)
    d.wr(_reg(CH, 0x0f000005), 0)
    d.wr(_reg(CH, 0x0100000a), 0)
    d.wr(_reg(CH, 0x01000009), P)
    run(d)
    r = measure(sc)
    gaps.append(r['gap'])
    record(f"   period={P}", r, f"gap={r['gap']*1e6:.1f}us" if r['gap'] else "1 pulse/window")
note = "RATE FIXED" if (gaps[0] is None and gaps[1] is None) else "responds"
print(f"   -> {note}\n")

# ---------- 2. Pulser / shape-RAM / Poisson ----------
print("2. Pulser + shape-RAM + POISSON timebase")
for alpha in (2**22, 2**26):
    shape_ram(d)
    d.wr(_reg(CH, 0x0f000005), 0)
    d.wr(_reg(CH, 0x0100000a), 1)
    d.wr(_reg(CH, 0x01000006), alpha)
    d.wr(_reg(CH, 0x01000004), 1); d.wr(_reg(CH, 0x01000004), 0)
    run(d)
    r = measure(sc)
    record(f"   alpha=2^{alpha.bit_length()-1}", r,
           f"gap={r['gap']*1e6:.1f}us" if r['gap'] else "1 pulse/window")
print()

# ---------- 3. Pulser / shape-RAM / SEQUENCE timebase ----------
print("3. Pulser + shape-RAM + SEQUENCE timebase (mux 6/7, no data uploaded)")
for mux in (6, 7):
    shape_ram(d)
    d.wr(_reg(CH, 0x0100000a), 2)
    d.wr(_reg(CH, 0x0f000005), mux)
    run(d)
    r = measure(sc)
    record(f"   TimebaseMux={mux}", r,
           f"gap={r['gap']*1e6:.1f}us" if r['gap'] else "1 pulse/window")
print()

# ---------- 4. Pulser / shape-RAM / EXTERNAL trigger ----------
print("4. Pulser + shape-RAM + EXTERNAL trigger (mux 4, no cable connected)")
shape_ram(d)
d.wr(_reg(CH, 0x0100000a), 0)
d.wr(_reg(CH, 0x0f000005), 4)
run(d)
r = measure(sc)
record("   TimebaseMux=4", r, "needs a physical trigger source")
d.wr(_reg(CH, 0x0f000005), 0)
print()

# ---------- 5. Pulser / Digital RC ----------
print("5. Pulser + DIGITAL RC (TR=0, enable=-1)")
rr = compute_drc(rise_ns=100, fall_ns=50000, clock_quarter=FREQ_CLK)
d.wr(_reg(CH, 0x0f000002), 1)
d.wr(_reg(CH, 0x0f000001), 1107)
d.wr(_reg(CH, 0x0f000000), (-55934) & 0xFFFFFFFF)
d.wr(_reg(CH, 0x0f000004), 1)
d.wr(0xFA00100A, 0)
d.wr(_reg(CH, 0x020f0004), 0)
d.wr(_reg(CH, 0x020f0005), 8000)
d.wr(_reg(CH, 0x0100000a), 0)
d.wr(_reg(CH, 0x01000009), 10000)
d.wr(_reg(CH, 0x00300010), 0)
d.wr(_reg(CH, 0x0300000d), rr['rise_presc_disable'])
for i, val in enumerate(rr['coeffs_reg']):
    d.wr(_reg(CH, 0x03000000 + i), val)
d.wr(_reg(CH, 0x0300000a), 1); d.wr(_reg(CH, 0x0300000a), 0)
d.wr(_reg(CH, 0x0300000c), 0)
d.wr(_reg(CH, 0x0300000b), 0xFFFFFFFF)
d.wr(_reg(CH, 0x00300014), rr['prescaler_reg_0x300014'])
run(d)
r = measure(sc)
record("   DRC rise=100ns fall=50us", r, "")
print()

# ---------- 6. Pulser / PULSED RESET ----------
print("6. Pulser + PULSED RESET (TR=1)")
d.wr(_reg(CH, 0x00300010), 1)
d.wr(_reg(CH, 0x00300012), 8000)
d.wr(_reg(CH, 0x00300013), 0)
run(d)
r = measure(sc)
record("   TR=1", r, "expect staircase/ramp per manual sec 10")
d.wr(_reg(CH, 0x00300010), 0)
print()

# ---------- 7. AWG ----------
print("7. AWG mode (CPS=3 -> 9.6 ns/sample, capable of a 100 ns rise)")


def awg(cps, dlen, rise_us, dec_us, peak, dc=0):
    dt_s = cps / FREQ_CLK
    tr = max(1e-12, rise_us * 1e-6) / 2.197
    td = dec_us * 1e-6
    raw = [(1 - math.exp(-(i * dt_s) / tr)) * math.exp(-(i * dt_s) / td)
           for i in range(dlen)]
    mx = max(raw) or 1.0
    pts = [int(round(dc - peak * (x / mx))) for x in raw]
    d._program_ddr(pts)
    d._awg_enable(cps, CH)


for cps, dlen, lbl in ((3, 10416, "10 kHz"), (3, 104160, "1 kHz")):
    awg(cps, dlen, 0.1, 50.0, 7065)
    r = measure(sc, settle=3.0)
    rate = FREQ_CLK / (dlen * cps)
    record(f"   CPS=3 DataLen={dlen} ({lbl})", r,
           f"cmd {rate:.0f} Hz, gap="
           f"{('%.0fus' % (r['gap']*1e6)) if r['gap'] else '-'}")

d.close()
sc.close()

print("\n" + "=" * 78)
print("summary written by the caller; see ../docs/REPORT.md")
