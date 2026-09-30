"""Shape-RAM (memory-based) pulse generation for the DT5810B.

This is the path that actually drives the DAC (see docs/FINDINGS.md C3: Digital RC
produces nothing even on a cold DT5810B). Manual calls it "Exponential - Fast" /
custom shape, sec 10 "Custom Shape - Memory Based data-path".

RAM layout, reverse-engineered from the working set_detector_pulse():
  * the shape is 1000 samples, packed two per 32-bit word (500 words)
  * word k = (sample[2k+1] << 16) | sample[2k]
  * written to base+0x500000 with the RAM gate (base+0x50f002) open
  * base = ((ch << 8) | sid) << 20, and all 16 sids must be programmed
    identically (they are the 16 pile-up generators, manual sec 10)
  * base+0x50f003 = word_count + 3
  * base+0x50f006 packs the interpolation factor: (0x10000/(I+1) << 16) | I
    -> I extra points inserted between consecutive RAM samples, so the
    effective sample period is (I+1) x the DAC period.

Time base: one RAM sample spans (I+1) DAC periods. The DAC period is nominally
1 ns (manual sec 12: "system clock is equal to 1GHz, i.e. 1 ns of sampling
period"), but this is calibrated empirically rather than assumed -- see
calibrate_ns_per_sample().
"""
import math

NSAMP = 1000               # shape samples actually stored
NWORD = NSAMP // 2         # 500 words
FULL_SCALE = 32575         # DAC clamp used by the vendor code


def shaped_exponential(tau_rise_samp, tau_decay_samp, n=NSAMP, peak=FULL_SCALE):
    """Manual sec 12 'Shaped Exponential': A(1-e^{-t/t1})e^{-t/t2}, normalised
    so the maximum is exactly `peak`."""
    raw = []
    for i in range(n):
        if tau_rise_samp <= 0:
            r = 1.0
        else:
            r = 1.0 - math.exp(-i / tau_rise_samp)
        raw.append(r * math.exp(-i / tau_decay_samp))
    m = max(raw) or 1.0
    return [int(round(peak * x / m)) for x in raw]


def pack(samples):
    """1000 samples -> 500 words, two 16-bit samples per word."""
    s = list(samples[:NSAMP]) + [0] * max(0, NSAMP - len(samples))
    out = []
    for k in range(NWORD):
        lo = max(-FULL_SCALE, min(FULL_SCALE, int(s[2 * k]))) & 0xFFFF
        hi = max(-FULL_SCALE, min(FULL_SCALE, int(s[2 * k + 1]))) & 0xFFFF
        out.append((hi << 16) | lo)
    return out


def program_shape(dev, samples, interp, ch=0):
    """Load `samples` into all 16 shape generators with interpolation factor
    `interp`. dev needs .wr(addr, value) and .wr_block(addr, words)."""
    words = pack(samples)
    blk = [0] * 0x800
    for i, w in enumerate(words):
        blk[i + 2] = w
    step = 0x10000 // (interp + 1)
    for sid in range(16):
        b = ((ch << 8) | sid) << 20
        dev.wr(b + 0x50f000, 1)                       # reconfigure gate open
        dev.wr(b + 0x50f001, 0)                       # multishape id
        dev.wr(b + 0x50f007, ((0x10000 // 3) << 16) | 2)
        dev.wr(b + 0x50f006, (step << 16) | interp)   # interpolation factor
        dev.wr(b + 0x50f004, 3)                       # interpolator enable
        dev.wr(b + 0x50f005, 0)                       # corner point
        dev.wr(b + 0x50f002, 1)                       # RAM open
        dev.wr_block(b + 0x500000, blk)
        dev.wr(b + 0x500000, blk[0])
        dev.wr(b + 0x500001, blk[1])
        dev.wr(b + 0x50f003, NWORD + 3)               # length
        dev.wr(b + 0x50f002, 0)                       # RAM closed
        # NOTE: do NOT write 0x50f000 = 0 here. The working set_detector_pulse()
        # leaves it at 1, and t11 showed that driving it to 0 across all 16 sids
        # kills the output entirely -- it gates the generator, not just the
        # reconfiguration window.


def interp_for_sample_period(ns_per_sample, ns_per_sample_at_interp0):
    """Choose the interpolation factor giving the requested RAM sample period."""
    i = int(round(ns_per_sample / ns_per_sample_at_interp0)) - 1
    return max(1, i)
