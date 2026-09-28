"""Vendor-faithful FAST/exponential shape builder with arbitrary rise time.

Ported from DDE-Control.decompiled.cs lines 12921-12950, the branch that runs for
PreamplifierMode=ContinuosReset with EnableFAST. The C# is:

    num2 = TimePerSame*2 / (RiseTime/5 * 1E-05/2.97 + TimePerSame*2)
    array[0..3] = 0
    for n in 4..4095:
        num5     = exp(-(n-4) / (FallTime*1E-05 / (TimePerSame*2*2*PI))) * 32575
        array[n] = array[n-1]*(1-num2) + num5*num2        <-- first-order IIR low-pass
    normalise to max, scale to 32768

with TimePerSame = 1E-09, so the array is computed at 2 ns per sample.

This is exactly what manual sec 10 describes: an ideal exponential passed through
a first-order IIR low-pass whose bandwidth is the rise time. THE RISE TIME IS
BAKED INTO THE ARRAY BY FILTERING - it is not a register and not the corner point.

Our previous builder (linux/dt5810.py set_detector_pulse) wrote a bare exponential
with an instantaneous rise, which is why rise time was not controllable and why
the interpolator corner was being abused as a substitute.

UNITS: the C# RiseTime/FallTime carry the GUI's own scaling (the 1E-05 factors).
Rather than guess it, RISE_UNIT_S / FALL_UNIT_S below express the vendor constants
as plain seconds-per-unit and are calibrated against the scope.
"""
import math

TPS = 1e-9                  # TimePerSame
DT = 2.0 * TPS              # 2 ns per computed sample
FULL = 32575.0

# vendor constants, rearranged into "seconds of time constant per GUI unit"
#   rise: RiseTime/5 * 1e-5/2.97   ->  RiseTime * 6.7340e-7
#   fall: FallTime*1e-5 / (2*TPS*2*pi) is in SAMPLES; x DT -> seconds
#         FallTime * 1e-5 / (2*pi) ... -> FallTime * 1.5915e-6 s
RISE_UNIT_S = 1e-5 / (5.0 * 2.97)      # 6.7340e-7 s per rise unit
FALL_UNIT_S = 1e-5 / (2.0 * math.pi)   # 1.5915e-6 s per fall unit


def build(rise_s, fall_s, n=4096, dt=DT):
    """Vendor formula, but taking rise/fall directly in SECONDS.

    rise_s is the IIR time constant (not the 10-90% time; see rise_10_90()).
    Returns a list of n floats normalised to peak 32768.
    """
    a = dt / (max(rise_s, 1e-12) + dt)          # IIR coefficient (= num2)
    tau_samples = max(1e-9, fall_s) / dt
    arr = [0.0] * n
    peak = -1e30
    for i in range(4, n):
        x = math.exp(-(i - 4) / tau_samples) * FULL
        arr[i] = arr[i - 1] * (1.0 - a) + x * a
        if arr[i] > peak:
            peak = arr[i]
    if peak <= 0:
        return arr
    return [v / peak * 32768.0 for v in arr]


def rise_10_90(samples, dt=DT):
    """Measure the 10-90% rise of a generated array, in seconds."""
    pk = max(samples)
    if pk <= 0:
        return None
    lo, hi = 0.1 * pk, 0.9 * pk
    i10 = i90 = None
    for i, v in enumerate(samples):
        if i10 is None and v >= lo:
            i10 = i
        if v >= hi:
            i90 = i
            break
    if i10 is None or i90 is None or i90 <= i10:
        return None
    return (i90 - i10) * dt


def tau_of(samples, dt=DT):
    """Measure the decay tau of a generated array, in seconds."""
    ipk = max(range(len(samples)), key=lambda i: samples[i])
    pk = samples[ipk]
    if pk <= 0:
        return None
    for j in range(ipk + 1, len(samples)):
        if samples[j] < pk * 0.36788:
            return (j - ipk) * dt
    return None


def tau_for_10_90(t1090_s):
    """A single pole's 10-90% time is 2.197 tau."""
    return t1090_s / 2.197


if __name__ == '__main__':
    print("vendor formula, rise expressed as an IIR time constant")
    print(f"{'rise req':>10} {'-> 10-90%':>11} {'fall req':>10} {'-> tau':>10} "
          f"{'span':>9}")
    for rise_ns, fall_us in ((100, 50), (1000, 50), (20, 50), (100, 10)):
        s = build(tau_for_10_90(rise_ns * 1e-9), fall_us * 1e-6)
        r = rise_10_90(s)
        t = tau_of(s)
        print(f"{rise_ns:>8} ns {(('%.0f ns' % (r*1e9)) if r else '-'):>11} "
              f"{fall_us:>8} us {(('%.2f us' % (t*1e6)) if t else '-'):>10} "
              f"{len(s)*DT*1e6:>8.2f}us")
    print()
    print("NOTE: 4096 samples at 2 ns spans only 8.19 us, so a 50 us tail cannot")
    print("      fit at this resolution - that is precisely why the vendor")
    print("      subsamples with two interpolation regions (manual sec 10 step 4:")
    print("      500 points to the rise, 3596 to the tail).")
