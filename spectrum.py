#!/usr/bin/env python3
"""Energy-spectrum mode for the DT5810B: emit amplitudes drawn from a histogram.

Instead of one fixed energy per pulse (EnergyMode 0), the board can draw each
pulse's amplitude from a user-supplied distribution (EnergyMode 1). This is what
makes it a source emulator rather than a pulser.

HOW THE HARDWARE DOES IT (manual sec 10 "From custom distributions to a set of
values"): a LUT-SR pseudo-random generator produces a uniform 32-bit number, and
the board finds which bin of the stored CUMULATIVE spectrum brackets it. Bin
widths are therefore probabilities: a bin twice as tall is drawn twice as often.
Only one memory cell per bin is needed because the cumulative form is stored.

WHAT THE HOST HAS TO SEND, from DDE3.dll `ProgramSpectrum` @0x1000b1a0 (body
@0x10004db0). The DLL does the cumulative-and-normalise itself, so its caller
passes a RAW histogram and so do we:

    cum[0] = 0
    cum[i] = sum(hist[1..i])                 # NOTE hist[0] is skipped
    cdf[i] = round(cum[i] / cum[16383] * (2**32 - 1))

then, with the channel in the top nibble:

    0x20f003 = 1                 open the spectrum RAM
    0x200000 <- 16384 words      the cdf
    0x20f003 = 0                 close it
    0x20f004 = 1                 EnergyMode = spectrum
    0x20f002 = 1, then 0         reprogram the energy LFSR

`ConfigureLFSR(seed, LFSR_ENERGY, LFSR_REPROGRAM)` turns out to be exactly that
last strobe of `0x20f002` and nothing else -- the seed argument is not written
anywhere in that path, so there is no seed control here to reproduce.

BIN NUMBERING: 16384 bins, and the energy register is documented as LSB x 2, so
bin i corresponds to energy register 2i. `bin_for_volts` / `volts_for_bin` wrap
that up. Bin 0 is unusable (the DLL's cumulative loop starts at 1).
"""
import math

N_BINS = 16384
CDF_FULL = (1 << 32) - 1

R_SPEC_GATE = 0x20f003      # spectrum RAM gate, 1 open / 0 close
R_SPEC_RAM = 0x200000       # 16384-word block
R_ENERGY_STROBE = 0x20f002
R_ENERGY_MODE = 0x20f004

MODE_FIXED = 0
MODE_SPECTRUM = 1
MODE_SEQUENCE = 2

# The whole 16384-word CDF goes in ONE bulk transfer, exactly as the DLL does
# it. Splitting it into 1024-word block writes at advancing addresses does NOT
# work: a delta spectrum then came back as a mixture (median 0.63 V instead of
# a clean 1.00 V), so the RAM evidently latches on one contiguous burst rather
# than honouring the address on each successive block. One transfer gives
# sd 0.007 V on a delta; chunked gave sd 0.19.


def to_cdf(hist):
    """Raw histogram -> the 16384-word cumulative array the hardware wants.

    Reproduces the DLL's arithmetic exactly, including that `hist[0]` never
    contributes and that the top entry saturates the full 32-bit range.
    """
    h = list(hist[:N_BINS]) + [0.0] * max(0, N_BINS - len(hist))
    cum = [0.0] * N_BINS
    run = 0.0
    for i in range(1, N_BINS):
        run += max(0.0, float(h[i]))
        cum[i] = run
    total = cum[N_BINS - 1]
    if total <= 0:
        raise ValueError("spectrum is empty: every bin is zero "
                         "(remember bin 0 is ignored by the hardware)")
    return [int(round(c / total * CDF_FULL)) & 0xFFFFFFFF for c in cum]


def program(dev, hist, ch=0, set_mode=True):
    """Upload a spectrum and switch the channel into spectrum mode."""
    cdf = to_cdf(hist)
    base = ch << 28
    dev.wr(base + R_SPEC_GATE, 1)
    dev.wr_block(base + R_SPEC_RAM, cdf)
    dev.wr(base + R_SPEC_GATE, 0)
    if set_mode:
        dev.wr(base + R_ENERGY_MODE, MODE_SPECTRUM)
        dev.wr(base + R_ENERGY_STROBE, 1)
        dev.wr(base + R_ENERGY_STROBE, 0)
    return cdf


def set_fixed(dev, energy_reg, ch=0):
    """Back to one fixed amplitude per pulse."""
    base = ch << 28
    dev.wr(base + R_ENERGY_MODE, MODE_FIXED)
    dev.wr(base + 0x20f005, int(energy_reg) & 0xFFFFFFFF)
    dev.wr(base + R_ENERGY_STROBE, 1)
    dev.wr(base + R_ENERGY_STROBE, 0)


# ---- bin <-> volts, via the fixed-energy calibration in pulser.py ----

def bin_for_energy_reg(reg):
    return int(round(reg / 2.0))


def energy_reg_for_bin(b):
    return int(b) * 2


def volts_for_bin(b, v_per_energy=3.414e-5, v_intercept=0.0635):
    return v_per_energy * energy_reg_for_bin(b) + v_intercept


def bin_for_volts(v, v_per_energy=3.414e-5, v_intercept=0.0635):
    return max(1, min(N_BINS - 1,
                      bin_for_energy_reg((v - v_intercept) / v_per_energy)))


# ---- builders ----

def empty():
    return [0.0] * N_BINS


def add_gaussian(hist, centre_bin, sigma_bins, counts=1.0):
    """Add a Gaussian peak. sigma in bins; counts is the integrated area."""
    lo = max(1, int(centre_bin - 6 * sigma_bins))
    hi = min(N_BINS, int(centre_bin + 6 * sigma_bins) + 1)
    norm = counts / (sigma_bins * math.sqrt(2 * math.pi))
    for i in range(lo, hi):
        hist[i] += norm * math.exp(-0.5 * ((i - centre_bin) / sigma_bins) ** 2)
    return hist


def add_flat(hist, lo_bin, hi_bin, counts=1.0):
    """Add a flat continuum between two bins (e.g. a Compton plateau)."""
    lo, hi = max(1, int(lo_bin)), min(N_BINS, int(hi_bin))
    if hi <= lo:
        return hist
    per = counts / (hi - lo)
    for i in range(lo, hi):
        hist[i] += per
    return hist


def delta(bin_index, counts=1.0):
    """A single populated bin -- the test case that pins the bin->volts map."""
    h = empty()
    h[int(bin_index)] = counts
    return h


def peaks(*specs):
    """peaks((bin, sigma, area), ...) -> histogram."""
    h = empty()
    for centre, sigma, area in specs:
        add_gaussian(h, centre, sigma, area)
    return h


def from_csv(path, bins_column=0, counts_column=1, n_bins=N_BINS):
    """Read a two-column CSV (bin, counts) and rebin onto the 16384-bin grid.

    The vendor's "Import an Energy Spectrum from File" (manual p.68) takes the
    same shape of file.
    """
    xs, ys = [], []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line[0] in '#;':
                continue
            parts = [p for p in line.replace(';', ',').split(',') if p != '']
            try:
                xs.append(float(parts[bins_column]))
                ys.append(float(parts[counts_column]))
            except (ValueError, IndexError):
                continue          # header or junk line
    if not xs:
        raise ValueError(f"no numeric rows found in {path}")
    h = empty()
    xmax = max(xs) or 1.0
    for x, y in zip(xs, ys):
        b = int(round(x / xmax * (n_bins - 1)))
        if 1 <= b < n_bins:
            h[b] += y
    return h
