#!/usr/bin/env python3
"""t24 - The correlation block: sync the two channels and calibrate the delay.

From the manual (sec 9.3 "Delay generation") and DDE3.dll's exported
DelayAndCorrelationControl (thunk at 0x1000bac0 -> body at 0x10007fe0):

    0x2E000000 = (correlation_mode & 7) | (enableCchannel << 3)
    0x2E000001 = delay, in counts

    correlation_mode: 0 disabled, 1 "CH2 follows exactly CH1",
                      2 "shared time base generator", (3 = Ch3 -> mode 0 + bit 3)

The DLL computes  counts = delay_us * 1000 * F / 250e6  where F is a device
field. If F is the board's quarter clock (312.5 MHz) that is one DAC sample per
count = 0.8 ns at 1.25 GS/s; the manual quotes 1 ns for the 1 GS/s model. This
script measures the scale rather than assuming it.

MEASUREMENT METHOD (no scope writes - Ryan drives the scope):
The scope triggers on CH2 and :WAV:SOUR is CHAN1, so t=0 in the CH1 record is
the instant CH2 crossed its trigger level. The time of CH1's own edge in that
record is therefore the CH1-CH2 skew. Only the CHANGE in that time is used to
calibrate, which cancels the threshold offset between the two channels.
"""
import sys, time

sys.path.insert(0, '/home/ryan/caen_signal_emulator/linux')
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')
from pulser import Pulser
from scope import Scope

R_CORR_MODE = 0x2E000000
R_CORR_DELAY = 0x2E000001
# TimebaseMux, export @0x1000bbd0: writes (ch << 28) | 0x0f000005
R_TB_MUX_CH2 = (1 << 28) | 0x0f000005

MODE_DISABLED = 0
MODE_SAME = 1        # CH2 is an exact replica of CH1
MODE_TIMEBASE = 2    # shared timebase; energy/shape stay independent


def edge_time(t, v, frac=0.5):
    """Time of the first rising crossing of `frac` of the pulse height."""
    lo, hi = min(v), max(v)
    if hi - lo < 0.15:
        return None
    thr = lo + frac * (hi - lo)
    for i in range(1, len(v)):
        if v[i - 1] < thr <= v[i]:
            # linear interpolation between the bracketing samples
            f = (thr - v[i - 1]) / (v[i] - v[i - 1])
            return t[i - 1] + f * (t[i] - t[i - 1])
    return None


def measure(sc, n=5):
    """Median CH1 edge time over n captures, and the pulse height."""
    ts, amps = [], []
    for _ in range(n):
        t, v = sc.waveform(1)
        e = edge_time(t, v)
        if e is not None:
            ts.append(e); amps.append(max(v) - min(v))
        time.sleep(0.25)
    if not ts:
        return None, None, 0
    ts.sort()
    return ts[len(ts) // 2], sum(amps) / len(amps), len(ts)


def main():
    sc = Scope(timeout=8)
    if not sc.q(':TRIG:EDGE:SOUR?').strip().upper().endswith('2'):
        print("!! scope must trigger on CH2 for this measurement"); return
    if not sc.q(':WAV:SOUR?').strip().upper().endswith('1'):
        print("!! scope :WAV:SOUR must be CHAN1 for this measurement"); return
    tb = float(sc.q(':TIM:MAIN:SCAL?'))
    print(f"scope: {tb*1e6:g} us/div -> {tb*10*1e6:g} us window, "
          f"trigger CH2 @ {sc.q(':TRIG:EDGE:LEV?').strip()} V\n")

    p = Pulser().open()
    # crisp edges: fast rise, short decay, one pulse per window
    common = dict(rate_hz=1000.0, decay_us=2.0, rise_us=0.1, baseline_v=0.0)
    p.set_pulse(amplitude_v=1.0, ch=0, **common)
    p.set_pulse(amplitude_v=0.6, ch=1, **common)
    time.sleep(2.5)

    print("--- correlation DISABLED: the two timebases free-run ---")
    p.d.wr(R_CORR_MODE, MODE_DISABLED)
    p.d.wr(R_CORR_DELAY, 0)
    p.d.wr(R_TB_MUX_CH2, 0)
    time.sleep(2.0)
    for k in range(3):
        e, a, n = measure(sc, 4)
        print(f"  CH1 edge {('%+.3f us' % (e*1e6)) if e else 'not found':>12}"
              f"   amp {a if a else 0:.3f} V   ({n}/4 captures)")

    print("\n--- correlation mode 2 (shared timebase), delay sweep ---")
    # The piece the first attempt missed: Update_Muxes() in DDE-Control sets
    # mode4 = 1 for CorrelationMode.Timebase and calls DT_TimebaseMux(1, ch=1).
    # CH2 must take its trigger from the correlation block, not its own
    # generator. Without this the mode register alone does nothing.
    p.d.wr(R_CORR_MODE, MODE_TIMEBASE)
    p.d.wr(R_TB_MUX_CH2, 1)
    time.sleep(1.5)
    rows = []
    for counts in (0, 125, 250, 500, 1000, 2000, 4000):
        p.d.wr(R_CORR_DELAY, counts)
        time.sleep(1.8)
        e, a, n = measure(sc, 5)
        rows.append((counts, e))
        print(f"  counts {counts:>5}  CH1 edge "
              f"{('%+.3f us' % (e*1e6)) if e else 'not found':>12}"
              f"   amp {a if a else 0:.3f} V   ({n}/5)")

    good = [(c, e) for c, e in rows if e is not None]
    if len(good) >= 3:
        c0, e0 = good[0]
        print("\n  counts   d(edge) vs counts=0    ns per count")
        for c, e in good[1:]:
            d = (e - e0)
            print(f"  {c:>6}   {d*1e9:>12.1f} ns     "
                  f"{d*1e9/(c-c0) if c != c0 else float('nan'):>8.3f}")
        n = len(good)
        sx = sum(c for c, _ in good); sy = sum(e for _, e in good)
        sxx = sum(c*c for c, _ in good); sxy = sum(c*e for c, e in good)
        slope = (n*sxy - sx*sy) / (n*sxx - sx*sx)
        print(f"\n  least-squares: {slope*1e9:.4f} ns per count "
              f"({1/slope/1e9:.4f} counts per ns)")
        print(f"  => implied delay clock {1/abs(slope)/1e9:.4f} GHz")

    p.d.wr(R_CORR_DELAY, 0)
    p.d.wr(R_TB_MUX_CH2, 0)
    p.close(); sc.close()


if __name__ == '__main__':
    main()
