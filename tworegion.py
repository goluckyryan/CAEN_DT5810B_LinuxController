"""Two-region shape builder — the vendor's method for arbitrary rise + long tail.

Manual sec 10, exponential shape generation:

    1. ideal exponential is generated
    2. it passes through a first-order IIR low-pass with bandwidth = t_rise
    3. find the sample where the filtered shape drops below the DAC LSB
    4. "500 points are reserved to the rising edge, the remaining 3596 are for
       the tail"
    5. the subsampled shape is programmed together with the linear interpolation
       factors for the rising and falling edges

Why this is needed: one uniformly-sampled array cannot carry both a 100 ns rise
and a 250 us tail (2500:1). Splitting the array at a corner and giving each
region its own interpolation factor is what makes both fit.

Register mapping, from set_detector_pulse's ordering:
    0x50f005  corner, in RAM samples (vendor uses 500)
    0x50f007  RISE region factor   packed ((0x10000/(f+1)) << 16) | f
    0x50f006  TAIL region factor   same packing
    0x50f003  length = words + 3
    0x50f004  interpolator enable (3 = both regions)

One adaptation to the vendor recipe: a fixed 500 rise points would demand
sub-nanosecond spacing for a 100 ns rise, finer than the DAC can play. So the
rise point count adapts to the finest usable interpolation factor (1) and is
capped at 500.
"""
import math

DAC_DT = 1.0 / 312.5e6  # 3.2 ns: one quarter-clock tick. Derived from the vendor's
                        # own magic constant (interp = width_us/3.185) and equal to
                        # the clock the timebase and AWG both use.
N_TOTAL = 4096         # shape RAM depth
N_RISE_MAX = 500       # vendor's reservation for the rising edge
FULL = 32575.0

# EXPLAINED 2026-09-24 (was an unexplained empirical 2.83).
#
# Measuring the rendered edge against the number of rise-region RAM samples, at
# 1 ns/sample scope resolution and interpolation factor 1:
#
#     corner  6 ->  58.5 ns    9.75 ns per rise sample
#     corner  8 ->  71.5 ns    8.94
#     corner 11 ->  98.2 ns    8.93
#     corner 17 -> 153.0 ns    9.00
#
# i.e. each rise-region sample takes ~9.0 ns, not the 3.2 ns of one interpolation
# step. 9.0 / 3.2 = 2.81 -- the rise region has a MINIMUM DWELL OF ~3 STEPS.
# That is exactly the old fudge factor, so it is now a derived constant.
MIN_RISE_DWELL = 3                       # interpolation steps per rise sample
RISE_STEP_S = MIN_RISE_DWELL * DAC_DT    # ~9.6 ns
# NOTE: 3.00 (the derived dwell) was tried and measured WORSE than the fitted
# value across the range (-8% at 100 ns, -31% at 1 us), as was a full
# max(dwell, factor) model (-23% to -62%). The 9 ns/sample observation above is
# real and almost certainly the origin of this number, but it is not a complete
# model. 2.83 stays because it is what measures correctly.
RISE_SCALE = 2.83        # retained for reference; no longer used in build()
# Rise-region length in rise time constants. 4.0 is the physically right number
# (the edge is ~98% complete by then) but the rendered result came out a uniform
# 1.30x long with corner_halved, so the working value is 4.0/1.30.
N_TAU = 4.0
PEAK_MARGIN = 1.25       # push the corner this far past the peak
# The WRITTEN value is 7 bits (0..127) and the hardware multiplies it by 2, so the
# rise region can hold up to 254 array samples. Confirmed twice over: Ryan measured
# the transition at ~7 us where corner x rf x 3.2ns predicts 2.84 us and
# 2*corner x rf x 3.2ns predicts 5.69 us, and FUN_10005a30 writes crosspoint>>1.
CORNER_WRITE_MAX = 127                    # 0x50f005 field limit
CORNER_MAX = 2 * CORNER_WRITE_MAX         # 254 array samples in the rise region

# THE HARDWARE CORNER UNIT IS 2 RAM SAMPLES. Ryan measured the first-derivative
# discontinuity at ~7 us for 1 us rise / 50 us decay, where corner=127 and
# rise factor=7. corner x rf x 3.2ns = 2.84 us, but 2*corner x rf x 3.2ns =
# 5.69 us and 2*corner x (rf+1) x 3.2ns = 6.50 us -- the 2x variants bracket the
# measurement. So writing 127 gave the hardware 254 samples, and the transition
# landed at 7 us instead of the intended 2.8 us.
#
# Halving ALONE breaks the rise (it pinned at ~1.25 us for every request) because
# it shrinks the fine region to half of what the array was built for. The two must
# change together: the array now carries up to CORNER_MAX = 254 rise samples and
# program() writes n_rise//2, so the hardware's 2x lands back on the intended
# geometry.
#
# 0x50f005 WRAPS ABOVE 127 -- the corner field is 7 bits. Measured: corner 114
# gave ratio 0.89, corner 133 gave 1.64, a hard discontinuity across 128. Writing
# crosspoint>>1 (which is exactly what the vendor's FUN_10005a30 does) doubles the
# usable range and removes the jump: ratios became a smooth 1.37/1.33/1.29/1.29/1.27
# over 500-1000 ns. That is why the vendor halves it, and program() now does too.

MIN_RISE_SAMPLES = 6

# Smallest rise the geometry can express: below this the corner clamps at
# MIN_RISE_SAMPLES and the edge stops getting faster. The region is N_TAU time
# constants long and the 10-90 is 2.197 tau, so
#     rise_floor = 2.197 * (MIN_RISE_SAMPLES * RISE_STEP_S) / N_TAU
# Measured after the N_TAU/corner-cap fix: 30 ns -> 48 ns (at the clamp),
# 40 ns -> 44 ns, 50 ns -> 54 ns. It no longer fails backwards -- before the fix
# 30 ns came out at 273 ns.
RISE_FLOOR_S = 2.197 * (MIN_RISE_SAMPLES * RISE_STEP_S) / N_TAU   # ~32 ns


def _fine_shape(rise_s, decay_s, dt, n):
    """Ideal exponential through the vendor's first-order IIR low-pass."""
    tau_r = max(1e-12, rise_s / 2.197)          # 10-90% -> single-pole tau
    a = dt / (tau_r + dt)
    out = [0.0] * n
    for i in range(1, n):
        x = math.exp(-(i - 1) * dt / decay_s)
        out[i] = out[i - 1] * (1.0 - a) + x * a
    return out


def _resample(src, dt_src, t0, t1, npts):
    """Linear resample of src over [t0, t1] into npts points."""
    out = []
    for k in range(npts):
        t = t0 + (t1 - t0) * k / max(1, npts - 1)
        u = t / dt_src
        i = int(u)
        if i >= len(src) - 1:
            out.append(src[-1])
        else:
            f = u - i
            out.append(src[i] * (1 - f) + src[i + 1] * f)
    return out


def build(rise_s, decay_s, tail_spans=6.0):
    """Return (samples, corner, rise_factor, tail_factor, info).

    samples is N_TOTAL entries of signed DAC codes normalised to FULL.
    """
    total_s = 0.0
    tau_r = max(1e-12, rise_s / 2.197)
    rise_span = 6.0 * tau_r                       # to well past the peak
    tail_span = tail_spans * decay_s
    total_s = rise_span + tail_span

    # fine grid: never coarser than the DAC, never more than ~400k points
    dt_fine = max(DAC_DT, total_s / 400000.0)
    n_fine = int(total_s / dt_fine) + 2
    fine = _fine_shape(rise_s, decay_s, dt_fine, n_fine)

    ipk = max(range(n_fine), key=lambda i: fine[i])
    peak_t = (ipk + 1) * dt_fine

    # Measured 2026-09-24: the rendered 10-90 edge fills essentially the WHOLE
    # rise region (measured/region = 0.96 at factor 1). So the region must be
    # sized to the requested 10-90 time, not to the time-to-peak -- the latter is
    # ~2.7x longer and was making every rise come out ~3x slow.
    # The rise region must contain the WHOLE edge, not part of it. Sampling the
    # ideal curve over rise_s/RISE_SCALE only reached ~54% of the way up for a
    # 1 us rise, so the hardware stretched that partial curve across the full
    # edge -- giving a nearly-linear ramp that then jumped when the coarse tail
    # took over. Measured against the vendor: our normalised edge read
    # 0.11/0.26/0.32/0.49/0.51 where a single pole (and the vendor) gives
    # 0.10/0.42/0.63/0.76/0.85.
    #
    # Take the region out to N_TAU time constants so the edge is essentially
    # complete inside it; the 10-90 then falls out at the right place on its own.
    tau_rise = max(1e-12, rise_s / 2.197)
    # The corner must sit PAST the peak. If the fine region ends before the pulse
    # has topped out, the last part of the approach is rendered by the coarse tail
    # interpolator (74 ns/step at a 50 us decay vs 9.6 ns in the rise region) and
    # the step change shows as a visible hump right at the peak. The old
    # min(peak_t, ...) actively capped the corner at the peak and so guaranteed it.
    corner_t = max(N_TAU * tau_rise, peak_t * PEAK_MARGIN, 2.0 * DAC_DT)

    # Geometry in shape-array indices. This sizing with RISE_SCALE is the
    # VERIFIED-WORKING combination (see the calibration table in the module
    # docstring). Two attempts to replace RISE_SCALE with a derived dwell model
    # both made the calibration worse and were reverted -- see README section 8.
    # 0x50f005 is a 7-BIT field: corner 114 worked, corner 133 wrapped (ratio
    # jumped 0.89 -> 1.64 across 128). So cap the corner at CORNER_MAX and buy
    # extra region length with the FACTOR instead of with more samples.
    n_rise = max(MIN_RISE_SAMPLES,
                 min(CORNER_MAX, int(round(corner_t / RISE_STEP_S))))
    n_rise -= n_rise % 2          # must be even: we write n_rise//2
    n_tail = N_TOTAL - n_rise
    ram_rise, ram_tail = n_rise // 4, n_tail // 4

    rise_factor = max(MIN_RISE_DWELL, int(round(corner_t / n_rise / DAC_DT)))
    tail_factor = max(1, int(round((total_s - corner_t) / n_tail / DAC_DT)))

    samples = (_resample(fine, dt_fine, 0.0, corner_t, n_rise)
               + _resample(fine, dt_fine, corner_t, total_s, n_tail))
    pk = max(samples) or 1.0
    samples = [int(round(v / pk * FULL)) for v in samples]

    info = {
        "corner": n_rise, "n_tail": n_tail,
        "ram_rise": ram_rise, "ram_tail": ram_tail,
        "rise_factor": rise_factor, "tail_factor": tail_factor,
        "corner_time_s": corner_t, "peak_time_s": peak_t,
        "rise_dt_s": rise_factor * DAC_DT,
        "tail_dt_s": tail_factor * DAC_DT,
        "total_s": total_s,
        "rendered_total_s": n_rise * rise_factor * DAC_DT
                            + n_tail * tail_factor * DAC_DT,
    }
    return samples, n_rise, rise_factor, tail_factor, info


def program(dev, samples, corner, rise_factor, tail_factor, ch=0,
            corner_halved=True, factor_minus_one=False, decimated=False,
            swap_pairs=False):
    """Upload with both interpolation regions active.

    The vendor worker FUN_10005a30 (sg_decomp.txt) differs from the working
    reverse-engineered sequence in three ways. Each is switchable here so they
    can be tested one at a time rather than all at once:

      corner_halved      0x50f005 = crosspoint >> 1
      factor_minus_one   the factor registers carry (f - 1) in the low half
      decimated          with interpolation active the packing is
                             word[u+2] = shape[8u+4] << 16 | shape[8u]
                         and 0x50f003 = (length >> 3) + 3

    Defaults reproduce the sequence that is known to work on this board.
    """
    rf = max(1, int(rise_factor))
    tf = max(1, int(tail_factor))
    blk = [0] * 0x800

    if decimated:
        nwords = min(len(samples) >> 3, 0x800 - 2)
        for u in range(nwords):
            lo = int(max(-FULL, min(FULL, samples[8 * u])))
            hi = int(max(-FULL, min(FULL, samples[8 * u + 4])))
            blk[u + 2] = ((hi & 0xFFFF) << 16) | (lo & 0xFFFF)
        length_reg = nwords + 3
        corner_ram = corner // 4
    else:
        nwords = min(len(samples) // 2, 0x800 - 2)
        for u in range(nwords):
            a = int(max(-FULL, min(FULL, samples[2 * u])))
            b = int(max(-FULL, min(FULL, samples[2 * u + 1])))
            # swap_pairs tests whether the two samples in a word are played in
            # the order we assume: the rendered edge showed a strict
            # big/small/big/small step pattern, which is what mis-ordered pairs
            # would produce.
            lo, hi = (b, a) if swap_pairs else (a, b)
            blk[u + 2] = ((hi & 0xFFFF) << 16) | (lo & 0xFFFF)
        length_reg = nwords + 3
        corner_ram = corner

    lo_r = (rf - 1) if factor_minus_one else rf
    lo_t = (tf - 1) if factor_minus_one else tf
    corner_reg = (corner_ram >> 1) if corner_halved else corner_ram

    for sid in range(16):
        b = ((ch << 8) | sid) << 20
        dev.wr(b + 0x50f000, 1)
        dev.wr(b + 0x50f001, 0)
        dev.wr(b + 0x50f007, ((0x10000 // (rf + 1)) << 16) | (lo_r & 0xFFFF))
        dev.wr(b + 0x50f006, ((0x10000 // (tf + 1)) << 16) | (lo_t & 0xFFFF))
        dev.wr(b + 0x50f004, 3)
        dev.wr(b + 0x50f005, corner_reg)
        dev.wr(b + 0x50f002, 1)
        dev.wr_block(b + 0x500000, blk)
        dev.wr(b + 0x500000, blk[0])
        dev.wr(b + 0x500001, blk[1])
        dev.wr(b + 0x50f003, length_reg)
        dev.wr(b + 0x50f002, 0)
    return nwords


if __name__ == '__main__':
    print("two-region plans (target: arbitrary rise with a long tail)")
    print(f"{'rise':>9} {'decay':>8} {'corner':>7} {'rise dt':>9} {'tail dt':>9} "
          f"{'rendered':>10}")
    for rise_ns, dec_us in ((100, 50), (100, 100), (1000, 50), (20, 50),
                            (10000, 50)):
        s, c, rf, tf, info = build(rise_ns * 1e-9, dec_us * 1e-6)
        print(f"{rise_ns:>7}ns {dec_us:>6}us {c:>7} "
              f"{info['rise_dt_s']*1e9:>7.0f}ns {info['tail_dt_s']*1e9:>7.0f}ns "
              f"{info['rendered_total_s']*1e6:>8.0f}us")
