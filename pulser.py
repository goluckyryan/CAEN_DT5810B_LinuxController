"""DT5810B Pulser-mode control with CORRECTED register addresses.

The timebase and energy register families in linux/dt5810.py (and in all the
project docs) carry one extra hex zero, so those writes have been landing 16x
away from the real registers. See docs/REGISTER_ADDRESS_BUG.md. That single error is
why the rate looked "locked at 318 Hz" and why the energy argument never
affected amplitude.

Correct addresses, read out of DDE3.dll (tb2_asm.txt, ghidra_tb.txt,
w_decomp.txt, ghidra_decomp2.txt), all OR-ed with channel << 28:

    0x10000a  TimeMode      0 = constant, 1 = Poisson, 2 = sequence
    0x100009  period        round(312.5e6 / rate) - 1
    0x100006  Poisson alpha 2^32 * 0.25 * rate / clock
    0x100007  paralyzable
    0x100008  deadtime
    0x20f002  energy LFSR strobe
    0x20f004  EnergyMode    0 = fixed, 1 = spectrum, 2 = sequence
    0x20f005  energy value  = energy_LSB * 2, must stay <= 32767

Measured on hardware 2026-09-23 (CH1, 1 MOhm):
    rate      = 312.5e6 / (period + 1)      verified -0.0% from 4 kHz to 31 kHz
    amplitude = 3.414e-5 * energy_reg + 0.0205  V   at gain 1184
    baseline  = linear in offset at 3.234e-5 V/count

Shape still comes from the shape-RAM (memory) datapath. Digital RC remains inert
even with correct energy addressing - verified by loading the shape RAM with a
5 us decay and the DRC coefficients with 50 us: the output followed the shape RAM.
"""
import math, sys, time

sys.path.insert(0, '/home/ryan/caen_signal_emulator/linux')
from dt5810 import DT5810, _reg                      # noqa: E402

CLOCK_HZ = 312.5e6          # timebase clock, derived from measurement

# corrected register offsets
R_TIMEMODE = 0x10000a
R_PERIOD = 0x100009
R_ALPHA = 0x100006
R_PARAL = 0x100007
R_DEADTIME = 0x100008
R_ENERGY_STROBE = 0x20f002
R_ENERGY_MODE = 0x20f004
R_ENERGY_VAL = 0x20f005
R_OFFSET = 0x0f000000
R_GAIN = 0x0f000001
R_TB_MUX = 0x0f000005
R_EN_MUX = 0x0f000006

# --- correlation block: sync the two channels (manual sec 9.3 "Delay
# generation"; DDE3.dll export DelayAndCorrelationControl @0x1000bac0, body
# @0x10007fe0). Absolute addresses, NOT channel-scoped.
R_CORR_MODE = 0x2E000000    # (mode & 7) | (enable_ch3 << 3)
R_CORR_DELAY = 0x2E000001   # delay, in DAC samples

CORR_DISABLED = 0     # two free-running timebases
CORR_SAME = 1         # CH2 is an exact replica of CH1, including noise
CORR_TIMEBASE = 2     # shared timebase: CH2 fires with CH1, but keeps its own
                      # amplitude, shape and polarity. This is the useful one.

# Measured 2026-09-25 (t24): 3.200 us of shift over 4000 counts = 0.800 ns per
# count, i.e. one 1.25 GS/s DAC sample. The manual quotes 1 ns for the 1 GS/s
# model, and the DLL's counts = delay_us * 1000 * F / 250e6 gives exactly this
# for F = 312.5 MHz, the board's quarter clock.
DELAY_NS_PER_COUNT = 0.800
# At register 0 the outputs are NOT aligned: CH2 leads by a fixed ~77 ns of
# pipeline skew, so this is the register value that actually lines them up.
# First estimated as 72 from a 7-point scan (t25), then trimmed by averaging
# the residual over 0-2400 ns, 14 captures each: it was flat at +18.9 ns
# (sd 6.2) with no slope, which corrects the intercept and confirms
# DELAY_NS_PER_COUNT is exact. residual = (DELAY_ZERO_COUNTS - true) * 0.8, so
# a POSITIVE residual means this constant is too HIGH: 72 - 24 = 48.
# Good to roughly +-10 ns; the limit is the scope's 20 ns sampling at 2 us/div.
DELAY_ZERO_COUNTS = 48
DELAY_MAX_COUNTS = 5000     # manual: delay programmable to 4 us

# calibration at gain=1184, 1 MOhm
V_PER_ENERGY = 3.414e-5
V_INTERCEPT = 0.0635      # 0.0205 + 0.043 closed-loop trim (2026-09-24):
                          # auto-gain over-delivered by a uniform +0.043 V
V_PER_OFFSET = 3.234e-5
DEFAULT_GAIN = 1184
DEFAULT_OFFSET = -55512     # puts the baseline on 0 V at the above gain
DECAY_SCALE = 1.048         # measured tau / requested decay_us at width_us=300


def period_for_rate(rate_hz):
    if not (0.01 <= rate_hz <= 5e6):
        raise ValueError("rate outside the manual's 1e-2 .. 5e6 cps range")
    return max(1, int(round(CLOCK_HZ / rate_hz)) - 1)


def rate_for_period(p):
    return CLOCK_HZ / (p + 1)


def energy_for_volts(v):
    reg = int(round((v - V_INTERCEPT) / V_PER_ENERGY))
    return max(1, min(32767, reg))


def volts_for_energy(reg):
    return V_PER_ENERGY * reg + V_INTERCEPT


# --- automatic gain selection -------------------------------------------------
# Measured 2026-09-24 (4-point gain sweep, offset held at DEFAULT_OFFSET):
#     gain  900 -> baseline +0.359 V,  amp/gain 0.910e-3
#     gain 1184 -> baseline -0.102 V,  amp/gain 0.880e-3
#     gain 1500 -> baseline -0.615 V,  amp/gain 0.876e-3
#     gain 2000 -> baseline -1.434 V,  amp/gain 0.871e-3
# Amplitude is linear in gain; the baseline drifts linearly too because the
# offset is applied BEFORE the gain stage (manual sec 12), so gain multiplies it.
BASELINE_SLOPE_PER_GAIN = -1.63e-3      # V per gain count, at DEFAULT_OFFSET
BASELINE_AT_DEFAULT = -0.099            # V at DEFAULT_GAIN / DEFAULT_OFFSET.
                                        # Two measurements disagreed (-0.0137 trimmed,
                                        # -0.102 from the gain sweep); a closed-loop
                                        # check settled it at -0.099, so the gain sweep
                                        # was right and the trim was the bad reading.
ENERGY_HEADROOM = 30000                 # keep the energy register below this
GAIN_MIN, GAIN_MAX = 900, 3000          # measured-linear region


def auto_gain(amplitude_v):
    """Pick the digital gain for a requested amplitude.

    Amplitude normally comes from the energy register at the calibrated gain --
    that is the physically meaningful knob (pulse height proportional to energy).
    Gain is only raised when energy alone cannot reach the requested amplitude.
    Returns (gain, energy_reg, extrapolating).
    """
    reg = int(round((amplitude_v - V_INTERCEPT) / V_PER_ENERGY))
    if reg <= ENERGY_HEADROOM:
        return DEFAULT_GAIN, max(1, reg), False
    # energy alone is not enough: scale the gain so energy lands at the headroom
    want = V_PER_ENERGY * ENERGY_HEADROOM + V_INTERCEPT       # ~1.05 V at unit gain
    gain = int(round(DEFAULT_GAIN * amplitude_v / want))
    gain = max(GAIN_MIN, min(GAIN_MAX, gain))
    reg = int(round((amplitude_v * DEFAULT_GAIN / gain - V_INTERCEPT) / V_PER_ENERGY))
    return gain, max(1, min(32767, reg)), gain >= GAIN_MAX


# Per-channel offset laws. Measured 2026-09-25 with a 4-point sweep on each.
# CH2's slope is NEGATIVE -- its offset moves the baseline the opposite way to
# CH1 -- which is why applying CH1's law to CH2 drove the output to -2 V.
# Amplitude needs no per-channel correction: CH2 gave 1.00 V for a 1.0 V request
# with CH1's energy/gain calibration unchanged.
# CH2's ANALOG OUTPUT STAGE IS INVERTED relative to CH1. One fact, three symptoms:
#   * polarity flipped   - invert=1 gives positive on CH1, negative on CH2
#   * offset slope sign  - -3.44e-5 V/count on CH2 vs +3.234e-5 on CH1
#   * CH1's offset law drove CH2 to -2 V
# So CH2 needs the opposite invert bit, and its baseline solved against the
# mirrored law. Measured 2026-09-25 with a 4-point offset sweep per channel.
CH_OFFSET = {
    0: dict(v_per_count=3.234e-5, zero_offset=-55512, base_at_default=-0.099,
            invert=1),
    # CH2 with invert=0 (its positive-going setting): the slope is positive
    # again, confirming the earlier -3.44e-5 was the inversion showing through.
    # 3-point fit over the linear region, saturated points excluded.
    1: dict(v_per_count=3.401e-5, zero_offset=58371, base_at_default=0.0,
            invert=0),
}


def invert_for_ch(ch, positive_going=True):
    """Invert bit giving the requested polarity on this channel."""
    native = CH_OFFSET.get(ch, CH_OFFSET[0])['invert']
    return native if positive_going else (1 - native)


def offset_for_ch(baseline_v, ch=0, gain=DEFAULT_GAIN):
    """Offset register placing channel `ch`'s baseline at `baseline_v`."""
    c = CH_OFFSET.get(ch, CH_OFFSET[0])
    if ch == 0:
        return offset_for(baseline_v, gain)
    vpc = c['v_per_count'] * gain / DEFAULT_GAIN
    return int(round(c['zero_offset'] + (baseline_v - c['base_at_default']) / vpc))


def offset_for(baseline_v, gain=DEFAULT_GAIN):
    """Offset register placing the baseline at `baseline_v`, at any gain.

    At DEFAULT_OFFSET the baseline sits at BASELINE_AT_DEFAULT and drifts with
    gain at BASELINE_SLOPE_PER_GAIN. Volts per offset count scales with gain,
    because the offset is applied before the gain stage.
    """
    base_now = BASELINE_AT_DEFAULT + BASELINE_SLOPE_PER_GAIN * (gain - DEFAULT_GAIN)
    v_per_count = V_PER_OFFSET * gain / DEFAULT_GAIN
    return int(round(DEFAULT_OFFSET + (baseline_v - base_now) / v_per_count))


def offset_for_baseline(baseline_v):
    """Offset register value that places the baseline at `baseline_v`.
    DEFAULT_OFFSET gives 0 V; the register is linear at V_PER_OFFSET V/count.
    Only valid at DEFAULT_GAIN - the manual notes offset is applied BEFORE gain."""
    return int(round(DEFAULT_OFFSET + baseline_v / V_PER_OFFSET))


def baseline_for_offset(reg):
    return (reg - DEFAULT_OFFSET) * V_PER_OFFSET


def build_shape(width_us, rise_us, decay_us, n=1000, compensate_decay=True):
    """Shape-RAM array with a REAL rise time, using the vendor's method.

    DDE-Control applies a first-order IIR low-pass to the ideal exponential, with
    the filter time constant set by the rise time (manual sec 10 step 2). Our
    previous builder wrote a bare exponential with an instantaneous rise, which is
    why rise time was never controllable.

    Built in the shape RAM's own time base: n samples spanning width_us, so
    dt = width_us/n. A rise faster than a few dt cannot be represented here --
    that needs the vendor's two-region interpolator (corner at 500 with separate
    rise/tail factors), which is not implemented yet.
    """
    dt = (width_us * 1e-6) / n
    req_decay = (decay_us / DECAY_SCALE if compensate_decay else decay_us) * 1e-6
    tau_r = max(1e-12, (rise_us * 1e-6) / 2.197)     # 10-90% -> single-pole tau
    a = dt / (tau_r + dt)
    arr = [0.0] * n
    lead = 4
    peak = 0.0
    for i in range(lead, n):
        x = math.exp(-(i - lead) * dt / req_decay) * 32575.0
        arr[i] = arr[i - 1] * (1.0 - a) + x * a
        peak = max(peak, arr[i])
    if peak <= 0:
        return arr, dt
    return [v / peak * 32575.0 for v in arr], dt


def shape_preview(width_us, decay_us, amplitude_v, baseline_v=0.0, n=600,
                  compensate_decay=True):
    """(times_s, volts) of the pulse that will be loaded into the shape RAM.

    The shape RAM holds 1000 samples spanning width_us, so one sample is
    width_us/1000. The vendor builder puts four zeros at the start before the
    exponential begins.
    """
    req = decay_us / DECAY_SCALE if compensate_decay else decay_us
    dt = (width_us * 1e-6) / 1000.0
    lead = 4
    ts, vs = [], []
    for i in range(n):
        k = i * 1000.0 / n
        y = 0.0 if k < lead else math.exp(-(k - lead) * (width_us / 1000.0) / req)
        ts.append(k * dt)
        vs.append(baseline_v + amplitude_v * y)
    return ts, vs


def _program_shape(dev, samples, interp, corner, ch=0):
    """Load a sample array into all 16 shape generators.

    Mirrors set_detector_pulse's register sequence exactly, except the array is
    ours (rise-filtered). NOTE: 0x50f000 is deliberately left at 1 - driving it
    to 0 across all sids gates the generators off and kills the output.
    """
    from shaperam import pack
    words = pack(samples)
    blk = [0] * 0x800
    for i, w in enumerate(words):
        blk[i + 2] = w
    step = 0x10000 // (interp + 1)
    for sid in range(16):
        b = ((ch << 8) | sid) << 20
        dev.wr(b + 0x50f000, 1)
        dev.wr(b + 0x50f001, 0)
        dev.wr(b + 0x50f007, ((0x10000 // 3) << 16) | 2)
        dev.wr(b + 0x50f006, (step << 16) | interp)
        dev.wr(b + 0x50f004, 3)
        dev.wr(b + 0x50f005, corner)
        dev.wr(b + 0x50f002, 1)
        dev.wr_block(b + 0x500000, blk)
        dev.wr(b + 0x500000, blk[0])
        dev.wr(b + 0x500001, blk[1])
        dev.wr(b + 0x50f003, len(words) + 3)
        dev.wr(b + 0x50f002, 0)


class Pulser:
    """Pulser-mode detector pulse: shape RAM + corrected timebase/energy."""

    def __init__(self, ch=0):
        self.d = DT5810()
        self.ch = ch
        # The first shape programming after a bringup does not take effect; an
        # identical second pass does. Observed repeatedly (Vpp 0.019 then 1.012
        # on unchanged parameters). Cause unknown -- config space is write-only
        # so there is nothing to poll. set_pulse() therefore programs twice on
        # its first call after open(), once thereafter.
        self._primed = False
        # correlation state, so set_pulse() can preserve the timebase routing
        self._corr_mode = CORR_DISABLED
        self._corr_delay_ns = 0.0
        # channels currently drawing energy from a spectrum rather than a
        # fixed value, so set_pulse() does not silently drop them back
        self._spectrum_ch = set()

    def open(self):
        self.d.open()
        self.d.bringup()
        self._primed = False
        self._primed_ch = set()
        self._corr_mode = CORR_DISABLED
        self._corr_delay_ns = 0.0
        self._spectrum_ch = set()      # the RAM is volatile; a reopen clears it
        return self

    def _tb_mux_for(self, ch):
        """Which timebase drives this channel.

        0 = the channel's own generator. 1 = the correlation block, which is
        what CH2 needs in shared-timebase mode. DDE-Control does this in
        Update_Muxes(): mode4 = 1 for CorrelationMode.Timebase, then
        DT_TimebaseMux(mode4, handle, 1). Setting R_CORR_MODE alone does
        nothing without it -- that cost an experiment to find.
        """
        return 1 if (ch == 1 and self._corr_mode == CORR_TIMEBASE) else 0

    def set_correlation(self, mode=CORR_TIMEBASE, delay_ns=0.0):
        """Lock CH2's timing to CH1, optionally offset by delay_ns.

        mode CORR_TIMEBASE shares only the timebase: CH2 still has its own
        amplitude, shape and polarity (verified t25 -- 0.6/0.9/0.4 V on CH2 with
        CH1 untouched), but its own rate setting is ignored and it follows CH1
        (verified: CH2 tracked CH1 to 150.9 and 250.8 kHz while programmed for
        100 kHz).

        delay_ns is the time CH2 lags CH1, measured at the outputs -- the fixed
        pipeline skew is taken out via DELAY_ZERO_COUNTS, so 0 means aligned.
        It may go slightly negative (CH2 early) until the register floors at 0.

        Returns the register values actually written.
        """
        counts = int(round(DELAY_ZERO_COUNTS + delay_ns / DELAY_NS_PER_COUNT))
        counts = max(0, min(DELAY_MAX_COUNTS, counts))
        self._corr_mode = mode
        self._corr_delay_ns = delay_ns
        self.d.wr(R_CORR_DELAY, counts)
        self.d.wr(R_CORR_MODE, mode & 0x7)
        # route CH2's timebase; CH1 always runs its own
        self.d.wr(_reg(1, R_TB_MUX), self._tb_mux_for(1))
        self.d.wr(_reg(0, R_TB_MUX), self._tb_mux_for(0))
        return {"mode": mode, "delay_ns": delay_ns, "delay_counts": counts,
                "achieved_ns": (counts - DELAY_ZERO_COUNTS) * DELAY_NS_PER_COUNT,
                "tb_mux_ch2": self._tb_mux_for(1)}

    # ---- energy spectrum (EnergyMode 1) ----

    def volts_for_bin(self, b, gain=DEFAULT_GAIN):
        """Amplitude a spectrum bin will produce, at the gain in use.

        Bin i drives energy register 2i, then the usual fixed-energy law scaled
        by gain/DEFAULT_GAIN (the calibration was taken at DEFAULT_GAIN).
        """
        return ((V_PER_ENERGY * 2 * int(b) + V_INTERCEPT)
                * gain / float(DEFAULT_GAIN))

    def bin_for_volts(self, v, gain=DEFAULT_GAIN):
        """Inverse of volts_for_bin, clamped to the usable bin range."""
        reg = (v * DEFAULT_GAIN / float(gain) - V_INTERCEPT) / V_PER_ENERGY
        return max(1, min(16383, int(round(reg / 2.0))))

    def max_spectrum_volts(self, gain=DEFAULT_GAIN):
        """Top of the spectrum range: bin 16383."""
        return self.volts_for_bin(16383, gain)

    def set_energy_spectrum(self, hist, ch=None):
        """Draw each pulse's amplitude from `hist` instead of a fixed value.

        `hist` is a raw 16384-bin histogram (bin i -> energy register 2i); the
        cumulative-and-normalise the hardware needs is done in spectrum.py,
        matching what DDE3.dll computes for the Windows software.

        Verified on the scope: a single populated bin gives a fixed amplitude
        (sd 0.007 V), two equal bins split 51/49, and a Gaussian of sigma 0.1 V
        measured sd 0.105 V.
        """
        import spectrum as _S
        c = self.ch if ch is None else ch
        _S.program(self.d, hist, ch=c)
        self._spectrum_ch.add(c)
        return {"ch": c, "bins": _S.N_BINS,
                "nonzero": sum(1 for x in hist if x > 0)}

    def set_energy_fixed(self, energy_reg, ch=None):
        """Back to one amplitude per pulse."""
        import spectrum as _S
        c = self.ch if ch is None else ch
        _S.set_fixed(self.d, energy_reg, ch=c)
        self._spectrum_ch.discard(c)
        return {"ch": c, "energy_reg": int(energy_reg)}

    def delay_range_ns(self):
        """(min, max) delay the register can express, in ns at the output."""
        return (-DELAY_ZERO_COUNTS * DELAY_NS_PER_COUNT,
                (DELAY_MAX_COUNTS - DELAY_ZERO_COUNTS) * DELAY_NS_PER_COUNT)

    def close(self):
        self.d.close()

    def wr(self, off, val):
        self.d.wr(_reg(self.ch, off), val & 0xFFFFFFFF)

    def set_pulse(self, rate_hz=1000.0, amplitude_v=1.0, decay_us=50.0,
                  rise_us=None, baseline_v=0.0,
                  width_us=300.0, corner=4, gain=None, offset=None,
                  poisson=False, deadtime=0, paralyzable=False,
                  compensate_decay=True, force_reprogram=False, invert=None,
                  positive_going=True, ch=None):
        """The five parameters that matter: rate, amplitude, rise, decay, baseline.

        gain and offset are chosen automatically from amplitude_v and baseline_v.
        Pass them explicitly only to override.
        """
        # one open device can drive either channel; registers are channel-scoped
        # as (ch << 28) | offset, and the shape RAM as ((ch<<8)|sid) << 20.
        prev_ch = self.ch
        if ch is not None:
            self.ch = ch
        if invert is None:
            invert = invert_for_ch(self.ch, positive_going)
        extrapolating = False
        if gain is None:
            gain, _auto_reg, extrapolating = auto_gain(amplitude_v)
        if offset is None:
            offset = offset_for_ch(baseline_v, ch if ch is not None else self.ch,
                                   gain)
        """Configure and start a detector pulse.

        decay_us is the TARGET tau; with compensate_decay the requested value is
        divided by the measured 1.048 scale factor so the achieved tau matches.
        """
        req_decay = decay_us / DECAY_SCALE if compensate_decay else decay_us
        # energy is referred to the calibrated gain, then scaled for the gain in use
        energy_reg = max(1, min(32767, int(round(
            (amplitude_v * DEFAULT_GAIN / gain - V_INTERCEPT) / V_PER_ENERGY))))
        period = period_for_rate(rate_hz)

        # see _primed in __init__
        passes = 2 if (force_reprogram or
                       self.ch not in getattr(self, '_primed_ch', set())) else 1
        for _pass in range(passes):
            self._apply(req_decay, energy_reg, period, rate_hz, decay_us,
                        width_us, corner, gain, offset, poisson, deadtime,
                        paralyzable, compensate_decay, rise_us, invert)
        self._primed = True
        getattr(self, '_primed_ch', set()).add(self.ch)
        result_ch = self.ch
        self.ch = prev_ch

        return {
            "rate_hz": rate_hz, "period": period,
            "rate_actual": rate_for_period(period),
            "amplitude_v": amplitude_v, "energy_reg": energy_reg,
            "amplitude_predicted": volts_for_energy(energy_reg),
            "decay_us_target": decay_us, "decay_us_requested": req_decay,
            "width_us": width_us, "corner": corner,
            "gain": gain, "offset": offset, "rise_us": rise_us,
            "baseline_v": baseline_v, "gain_auto": True, "invert": invert,
            "ch": result_ch, "positive_going": positive_going,
            "polarity": "positive" if invert else "negative",
            "amplitude_extrapolating": extrapolating,
            "programming_passes": passes,
            "shape": getattr(self, "_shape_info", None),
            "mode": "Poisson" if poisson else "constant",
        }

    def _apply(self, req_decay, energy_reg, period, rate_hz, decay_us,
               width_us, corner, gain, offset, poisson, deadtime,
               paralyzable, compensate_decay, rise_us, invert=1):
        """One full configuration pass. Called twice on the first set_pulse."""
        # shape RAM
        if rise_us is None:
            # legacy path: bare exponential, instantaneous rise
            self.d.set_detector_pulse(width_us=width_us, decay_us=req_decay,
                                      gain=gain, offset=offset, invert=invert,
                                      ch=self.ch)
            if corner:
                for sid in range(16):
                    b = ((self.ch << 8) | sid) << 20
                    self.d.wr(b + 0x50f000, 1)
                    self.d.wr(b + 0x50f005, corner)
                    self.d.wr(b + 0x50f000, 1)
        else:
            # vendor method: IIR low-pass for the rise, then the two-region
            # interpolator so a fast rise and a long tail fit one array
            import tworegion
            samples, corn, rf, tf, shinfo = tworegion.build(
                rise_us * 1e-6, decay_us * 1e-6)
            tworegion.program(self.d, samples, corn, rf, tf, ch=self.ch)
            self._shape_info = shinfo
            self.wr(R_GAIN, gain)
            self.wr(R_OFFSET, offset)
            # 0x0f000004 = output polarity (manual sec 12 "Invert").
            # invert=1 gives a POSITIVE-going pulse on this board.
            self.wr(0x0f000004, 1 if invert else 0)
            self.wr(0x0f000002, 1)
            self.d.wr(0xFA00100A, 0)

        # Preserve the correlation routing: a plain 0 here would silently drop
        # CH2 back onto its own timebase every time its pulse is reprogrammed.
        self.wr(R_TB_MUX, self._tb_mux_for(self.ch))
        self.wr(R_EN_MUX, 0)

        # energy - CORRECT addresses.
        # Preserve spectrum mode: writing mode 0 here would drop a channel that
        # is drawing from a histogram back to a fixed amplitude every time its
        # pulse shape is reprogrammed. The spectrum RAM itself is untouched by
        # set_pulse, so only the mode needs restoring.
        self.wr(R_ENERGY_STROBE, 1)
        self.wr(R_ENERGY_STROBE, 0)
        if self.ch in getattr(self, '_spectrum_ch', set()):
            self.wr(R_ENERGY_MODE, 1)
        else:
            self.wr(R_ENERGY_MODE, 0)
            self.wr(R_ENERGY_VAL, energy_reg)

        # timebase - CORRECT addresses
        self.wr(R_TIMEMODE, 1 if poisson else 0)
        self.wr(R_PERIOD, period)
        if poisson:
            self.wr(R_ALPHA, int((2 ** 32) * 0.25 * rate_hz / CLOCK_HZ))
        self.wr(R_DEADTIME, int(deadtime))
        self.wr(R_PARAL, 1 if paralyzable else 0)

        self.run(True)

    def assert_alive(self, scope, settle=2.0, scope_ch=1):
        """Raise if the board is wedged.

        The FPGA has wedged three times in one session: USB still enumerates and
        writes are accepted without error, but nothing reaches the hardware and
        the analog output freezes on the last good configuration. A frozen trace
        looks like a perfectly good measurement, and conclusions have twice been
        drawn from one before it was noticed.

        Toggling the run gate is the cheapest unambiguous test: on a live board
        the output collapses, on a wedged one it does not move.
        """
        import time as _t
        q = f':MEAS:ITEM? VPP,CHAN{scope_ch}'
        self.run(True); _t.sleep(settle)
        on = scope.qf(q)
        self.run(False); _t.sleep(settle)
        off = scope.qf(q)
        self.run(True); _t.sleep(settle)
        if on is None:
            raise RuntimeError(
                f"assert_alive: no reading on scope CH{scope_ch} with the output "
                f"RUNNING — wrong scope channel, or nothing is being generated.")
        if off is None:
            # if the scope triggers on this signal, stopping it kills the trigger
            # and the measurement goes invalid. That IS the output stopping.
            return on, 0.0
        if off > 0.5 * on:
            raise RuntimeError(
                f"BOARD WEDGED: run gate does not change the output "
                f"(on {on:.3f} V, off {off:.3f} V). Power-cycle load 3 and "
                f"reload the FX3 firmware. Any measurement taken now is stale.")
        return on, off

    def run(self, on=True):
        if on:
            self.wr(0x01c00006, 0)
            self.wr(0x01c00003, 1)
            self.wr(0x01c00006, 1)
        else:
            self.wr(0x01c00006, 0)


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description="DT5810B pulser (corrected registers)")
    ap.add_argument('--rate', type=float, default=1000.0)
    ap.add_argument('--amp', type=float, default=1.0)
    ap.add_argument('--decay', type=float, default=50.0)
    ap.add_argument('--width', type=float, default=300.0)
    ap.add_argument('--corner', type=int, default=4)
    ap.add_argument('--poisson', action='store_true')
    a = ap.parse_args()
    p = Pulser().open()
    info = p.set_pulse(rate_hz=a.rate, amplitude_v=a.amp, decay_us=a.decay,
                       width_us=a.width, corner=a.corner, poisson=a.poisson)
    for k, v in info.items():
        print(f"  {k:22} = {v}")
    p.close()
    print("\nrunning.")
