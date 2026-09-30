#!/usr/bin/env python3
"""DT5810B Pulser-mode control GUI (PyQt6) — both channels.

    python3 pulser_gui.py

Pulser is the detector-emulator datapath: pulses are fired by a timebase (constant
rate or Poisson), each scaled by the energy value, with the shape drawn from the
shape RAM. Unlike AWG it is triggered rather than looping, so it can do Poisson
statistics and pile-up.

The two channels get a panel each and run independently — one open USB device
drives both, registers being channel-scoped as (ch << 28) | offset.

You set five things per channel: rate, amplitude, rise, decay, baseline. Gain,
offset, polarity bit and the whole shape geometry are derived from those; see
README.md for the calibrations. CH2's analog stage is inverted relative to CH1,
which is why the invert bit and the offset zero point differ between the panels.

Uses the CORRECTED timebase and energy register addresses (docs/REGISTER_ADDRESS_BUG.md):
the project's usual 0x0100000x / 0x020f000x carry an extra hex zero and land 16x
away from the real registers, which is why rate and energy never responded.

This GUI controls the emulator and nothing else -- it does not talk to the
scope. The read-back panel that used to live here is now a separate window,
`scope/monitor.py`, which runs alongside it; `scope/` is bench apparatus and is
not part of this repository (see .gitignore).
"""
import os, sys, time, traceback

from PyQt6.QtCore import QThread, pyqtSignal, QTimer
from PyQt6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QLabel, QPushButton, QComboBox,
    QDoubleSpinBox, QSpinBox, QCheckBox, QGroupBox, QVBoxLayout, QHBoxLayout,
    QPlainTextEdit, QFormLayout, QStatusBar, QFileDialog,
)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pulser as P
import tworegion as T
from pulser import (Pulser, period_for_rate, rate_for_period, volts_for_energy,
                    CORR_DISABLED, CORR_TIMEBASE, DELAY_NS_PER_COUNT,
                    DELAY_ZERO_COUNTS, DELAY_MAX_COUNTS)


LIGHT = dict(bg="#f7f8fa", panel="#ffffff", grid="#dfe3e8", text="#1f2328",
             dim="#6b7280", border="#d0d7de",
             ok="#1d4ed8", warn="#b45309", bad="#b91c1c", field="#ffffff",
             # disabled: Qt style sheets override the palette, so a disabled
             # widget keeps its styled colour unless we say otherwise
             faint="#b9bfc6", mute="#eef0f2", faintborder="#e3e6ea")
DARK = dict(bg="#181b20", panel="#12151a", grid="#2a303a", text="#e5e7eb",
            dim="#9ca3af", border="#333b47",
            ok="#60a5fa", warn="#fbbf24", bad="#f87171", field="#12151a",
            faint="#4b525c", mute="#15181d", faintborder="#252a32")
THEME = LIGHT


def sheet(t):
    return f"""
        QWidget {{ background:{t['bg']}; color:{t['text']}; font-size:12px; }}
        QGroupBox {{ border:1px solid {t['border']}; border-radius:6px;
                     margin-top:9px; padding-top:8px; font-weight:600; }}
        QGroupBox::title {{ subcontrol-origin:margin; left:8px; padding:0 4px;
                            color:{t['dim']}; }}
        QPushButton {{ background:{t['panel']}; border:1px solid {t['border']};
                       border-radius:5px; padding:6px 12px; }}
        QPushButton:hover:enabled {{ background:{t['grid']}; }}
        QPushButton:disabled {{ color:{t['dim']}; }}
        QPlainTextEdit {{ background:{t['panel']}; border:1px solid {t['border']};
                          font-family:monospace; }}
        QSpinBox, QDoubleSpinBox, QComboBox {{ background:{t['field']};
                          border:1px solid {t['border']}; border-radius:4px;
                          padding:3px; }}

        /* Disabled. Without these a disabled control looks identical to a
           live one, because the QWidget colour rule above beats the palette. */
        QWidget:disabled {{ color:{t['faint']}; }}
        QLabel:disabled {{ color:{t['faint']}; }}
        QCheckBox:disabled {{ color:{t['faint']}; }}
        QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
                          background:{t['mute']}; color:{t['faint']};
                          border:1px solid {t['faintborder']}; }}
        QPushButton:disabled {{ background:{t['mute']}; color:{t['faint']};
                          border:1px solid {t['faintborder']}; }}
        QGroupBox:disabled {{ border:1px dashed {t['faintborder']};
                          background:{t['mute']}; }}
        QGroupBox::title:disabled {{ color:{t['faint']}; }}
    """


class Worker(QThread):
    done = pyqtSignal(bool, str)
    note = pyqtSignal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.done.emit(True, self.fn(self.note.emit) or "done")
        except Exception as e:
            self.done.emit(False, f"{type(e).__name__}: {e}\n"
                                  f"{traceback.format_exc(limit=3)}")


class ChannelPanel(QGroupBox):
    """One channel's controls and derived readouts."""

    def __init__(self, ch, win, ch3=False):
        """ch3=True builds the third (coincidence) generator's panel.

        Channel 3 is a FULL channel in the vendor's model, not a stub:
        DDE-Control allocates ChannelConfiguration[NChannels + 1] -- three
        channels for a two-channel emulator -- and its configuration loop runs
        Update_Generals / Update_Energy / Update_Shape / Update_Timebase over
        all three, channel 2 included. Its `ReducedChannel` flag strips only the
        SEQUENCE modes, nothing else. So channel 3 gets rate, energy AND shape.

        What stays hidden here is the analog-stage row set -- baseline, noise
        and polarity -- because there are only two physical output stages and
        those are demonstrably controlled by the CH1 and CH2 panels.

        Still unverified: whether a channel-3 event is rendered by channel 3's
        own shaper or by the shapers of the two channels it emerges through.
        The experiment (t27/README 9h) was inconclusive -- the scope window was
        too short for the longer decays. The controls are exposed because the
        vendor programs them; the measurement is still owed.
        """
        super().__init__("Channel 3 — coincidence source" if ch3
                         else f"Channel {ch + 1}")
        self._title_base = self.title()
        self.ch = ch
        self.win = win
        self.is_ch3 = ch3
        f = QFormLayout(self)

        self.cmb_time = QComboBox()
        self.cmb_time.addItems(["Constant rate", "Poisson"])
        self.cmb_time.setToolTip(
            "How the firing times are generated.\n"
            "Constant rate: a fixed period, one pulse every 1/rate.\n"
            "Poisson: exponentially distributed intervals, like a real source.\n"
            "Poisson needs the timebase LFSR started (register 0x100004); that\n"
            "was missing for the whole project and the mode emitted NOTHING at\n"
            "all -- see README section 9i. It is done automatically now.")
        self.cmb_time.currentTextChanged.connect(self.refresh)
        f.addRow("Timebase", self.cmb_time)

        self.sp_rate = self._dsb(0.01, 5e6, 1000.0, " Hz", 2)
        self.sp_rate.setToolTip(
            "Pulse rate. period = round(312.5e6 / rate) - 1, verified flat to\n"
            "-0.0% from 4 to 31 kHz. Range 0.01 Hz to 5 MHz.\n"
            "Watch rate x decay: above ~0.1 the pulses pile up.\n"
            "Two channels at the SAME rate are phase-locked, not independent --\n"
            "they divide one clock. Detune one ~0.5% to separate them.")
        f.addRow("Rate", self.sp_rate)
        # stash on the widget itself: do_sync() adds and removes a "slaved"
        # note and needs the original back
        self.sp_rate._tip_base = self.sp_rate.toolTip()
        self.sp_amp = self._dsb(P.AMPLITUDE_FLOOR_V, 2.0, 1.0, " V", 3)
        self.sp_amp.setToolTip(
            f"Below {P.AMPLITUDE_FLOOR_V} V the emulator emits nothing at all -- not a\n"
            "small pulse, nothing. The amplitude law is only linear over\n"
            "energy_reg 4000..30000. The box will not go lower.")
        f.addRow("Amplitude", self.sp_amp)

        # --- energy: one fixed amplitude, or drawn from a spectrum ---
        self.cmb_energy = QComboBox()
        self.cmb_energy.addItems(["Fixed", "Gaussian peak", "Two peaks",
                                  "Flat continuum", "CSV file..."])
        self.cmb_energy.setToolTip(
            "Fixed = every pulse the same height (EnergyMode 0).\n"
            "The others load a histogram into the spectrum RAM and let the\n"
            "emulator draw each pulse's amplitude from it (EnergyMode 1), which is\n"
            "what makes this a source emulator rather than a pulser.\n"
            "'Amplitude' above becomes the peak centre / upper edge.")
        self.cmb_energy.currentIndexChanged.connect(self._energy_mode_changed)
        f.addRow("Energy", self.cmb_energy)

        self.sp_sigma = self._dsb(0.002, 1.0, 0.05, " V", 3)
        self.sp_sigma.setToolTip("Gaussian sigma of the peak, in volts.")
        self._row_sigma = f.rowCount(); f.addRow("Peak width", self.sp_sigma)
        self.sp_peak2 = self._dsb(P.AMPLITUDE_FLOOR_V, 2.0, 0.5, " V", 3)
        self.sp_peak2.setToolTip(
            "Centre of the second Gaussian peak, in volts.\n"
            "Its height relative to the first is set by 2nd/1st below.")
        self._row_peak2 = f.rowCount(); f.addRow("2nd peak", self.sp_peak2)
        self.sp_ratio = self._dsb(0.01, 100.0, 1.0, "", 2)
        self.sp_ratio.setToolTip("Intensity of the 2nd peak relative to the 1st.")
        self._row_ratio = f.rowCount(); f.addRow("2nd/1st", self.sp_ratio)
        self.sp_flat_lo = self._dsb(P.AMPLITUDE_FLOOR_V, 2.0, 0.35, " V", 3)
        self.sp_flat_lo.setToolTip(
            "Lower edge of a flat continuum; the upper edge is Amplitude.\n"
            "Use for a Compton-plateau-like background.")
        self._row_flatlo = f.rowCount(); f.addRow("Continuum from", self.sp_flat_lo)
        csv = QHBoxLayout()
        self.lbl_csv = QLabel("(none)"); self.lbl_csv.setWordWrap(True)
        self.btn_csv = QPushButton("Browse...")
        self.btn_csv.clicked.connect(self._pick_csv)
        self.btn_csv.setToolTip(
            "Load a two-column CSV (bin, counts), the same shape the vendor's\n"
            "'Import an Energy Spectrum from File' takes. It is rebinned onto\n"
            "the hardware's 16384-bin grid.")
        csv.addWidget(self.lbl_csv, 1); csv.addWidget(self.btn_csv)
        wcsv = QWidget(); wcsv.setLayout(csv)
        self._row_csv = f.rowCount(); f.addRow("Spectrum file", wcsv)
        self.csv_path = None
        self._form = f
        self.sp_rise = self._dsb(0.0, 500.0, 1.0, " us", 3)
        self.sp_rise.setSpecialValueText("fastest")
        self.sp_rise.setToolTip(
            "Rise time, 10-90%. Applied by low-pass filtering the exponential on\n"
            "the host, exactly as DDE-Control does (manual sec 10 step 2) -- it\n"
            "is NOT a register. The shape geometry is then chosen to suit it.")
        self._row_rise = f.rowCount(); f.addRow("Rise (10-90%)", self.sp_rise)
        self.sp_decay = self._dsb(0.05, 5000.0, 50.0, " us", 2)
        self.sp_decay.setToolTip(
            "Exponential decay constant tau, NOT the 10-90 fall time.\n"
            "The emulator runs long by a constant 3.86 us, so the request has\n"
            "that subtracted before programming (see the compensate box).\n"
            "Corrected accuracy: -3.3 to +7.8% over 10..200 us.")
        self._row_decay = f.rowCount(); f.addRow("Decay tau", self.sp_decay)
        self.sp_base = self._dsb(-1.5, 1.5, 0.0, " V", 3)
        self.sp_base.setToolTip(
            "DC level the pulse sits on, via the offset register 0x0f000000.\n"
            "Solved per channel: CH2's analog stage is inverted, so its offset\n"
            "law is a different one (README section 9d). Calibrated for\n"
            "POSITIVE-going output; negative polarity may land elsewhere.")
        self._row_base = f.rowCount(); f.addRow("Baseline", self.sp_base)
        self.sp_noise = self._dsb(0.0, 230.0, 0.0, " mV rms", 1)
        self.sp_noise.setSpecialValueText("none")
        self.sp_noise.setToolTip(
            "Broadband noise added to the baseline (register 0x1400000,\n"
            "3.55 uV rms per count). The emulator has an intrinsic ~20 mV rms\n"
            "floor that this adds to in quadrature, so small values are\n"
            "swamped by it.")
        self._row_noise = f.rowCount(); f.addRow("Noise", self.sp_noise)

        self.cmb_pol = QComboBox()
        self.cmb_pol.addItems(["Positive-going", "Negative-going"])
        self.cmb_pol.currentTextChanged.connect(self.refresh)
        self.cmb_pol.setToolTip(
            "Output polarity, register 0x0f000004 (manual sec 12 \"Invert\").\n"
            "The bit that gives a positive pulse differs between channels: CH2's\n"
            "analog stage is inverted relative to CH1, so the GUI picks the bit\n"
            "for you (pulser.invert_for_ch).\n"
            "The amplitude and baseline calibrations were measured POSITIVE-going;\n"
            "negative may land its baseline elsewhere.")
        self._row_pol = f.rowCount(); f.addRow("Polarity", self.cmb_pol)

        self.sp_dead = QSpinBox(); self.sp_dead.setRange(0, 2**31 - 1)
        self.sp_dead.setToolTip(
            "Dead time after each pulse, in 312.5 MHz clock counts (3.2 ns\n"
            "each). 0 disables it. Emulates a detector or DAQ that cannot\n"
            "retrigger immediately.")
        f.addRow("Dead time (counts)", self.sp_dead)
        self.chk_paral = QCheckBox("paralyzable")
        self.chk_paral.setToolTip(
            "Dead-time model. Unticked (non-paralyzable): events during the\n"
            "dead time are simply lost. Ticked (paralyzable): each event\n"
            "RESTARTS the dead time, so at high rates the output rate collapses\n"
            "rather than saturating.")
        f.addRow("", self.chk_paral)
        self.chk_comp = QCheckBox(f"compensate decay (−{P.DECAY_OFFSET_US:g} us)")
        self.chk_comp.setToolTip(
            f"The emulator's decay runs long by a constant {P.DECAY_OFFSET_US} us --\n"
            "additive, not a scale factor (README section 9l). With this on,\n"
            "that is subtracted before programming, and 10/20/50/100/200 us\n"
            "come out within -3.3 to +7.8 %. With it off they come out\n"
            "15.0/23.6/53.6/103.4/204.5 us.")
        self.chk_comp.setChecked(True)
        self._row_comp = f.rowCount(); f.addRow("", self.chk_comp)

        self.lbl_reg = QLabel("-"); self.lbl_reg.setWordWrap(True)
        f.addRow("Registers", self.lbl_reg)
        self.lbl_geom = QLabel("-"); self.lbl_geom.setWordWrap(True)
        self._row_shape = f.rowCount(); f.addRow("Shape", self.lbl_geom)
        self.lbl_chk = QLabel("-"); self.lbl_chk.setWordWrap(True)
        f.addRow("Checks", self.lbl_chk)

        row = QHBoxLayout()
        self.btn_apply = QPushButton("Apply + Run")
        self.btn_apply.setToolTip(
            "Program this channel and start it: shape RAM, timebase, energy\n"
            "(fixed or spectrum), gain, offset, polarity and noise, then the\n"
            "run gate. The first call after connecting programs twice -- the\n"
            "first pass does not take and the reason is unknown.")
        self.btn_apply.clicked.connect(
            lambda: (win.do_apply_ch3() if ch3 else win.do_apply(self.ch)))
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setToolTip(
            "Close this channel's run gate (0x01c00006 = 0). The configuration\n"
            "stays programmed; Apply restarts it.")
        self.btn_stop.clicked.connect(
            lambda: (win.do_ch3_off() if ch3 else win.do_stop(self.ch)))
        row.addWidget(self.btn_apply); row.addWidget(self.btn_stop)
        holder = QWidget(); holder.setLayout(row)
        f.addRow("", holder)

        if ch3:
            # Noise stays: the vendor's Noise form has no ReducedChannel check,
            # so channel 3 gets it unrestricted, and noise injected here is
            # COMMON-MODE -- it lands on both outputs, unlike the per-channel
            # noise on the CH1/CH2 panels.
            # Baseline and polarity go: those are the physical output stage, and
            # the vendor's UpdateCalibration only calibrates channels 0 and 1,
            # so there is no third stage for them to act on.
            for r in (self._row_base, self._row_pol):
                f.setRowVisible(r, False)
            self.btn_apply.setText("Apply channel 3")
            self.btn_apply.setToolTip(
                "Program the third generator and put the emulator into\n"
                "coincidence mode: its event is injected into CH1 and CH2\n"
                "together, so the two outputs are ENERGY-correlated.")
            self.btn_stop.setText("Disable")
            self.btn_stop.setToolTip(
                "Leave coincidence mode: sets Correlation back to Off, and the\n"
                "two channels return to their own generators.")
        self._energy_mode_changed()   # sets initial row visibility, then refreshes

    def _energy_mode_changed(self):
        """Show only the fields the chosen energy mode actually uses."""
        m = self.cmb_energy.currentIndex()
        f = self._form
        f.setRowVisible(self._row_sigma, m in (1, 2))
        f.setRowVisible(self._row_peak2, m == 2)
        f.setRowVisible(self._row_ratio, m == 2)
        f.setRowVisible(self._row_flatlo, m == 3)
        f.setRowVisible(self._row_csv, m == 4)
        self.refresh()

    def _pick_csv(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Energy spectrum (two columns: bin, counts)", "",
            "Spectra (*.csv *.txt *.dat);;All files (*)")
        if path:
            self.csv_path = path
            self.lbl_csv.setText(path.rsplit('/', 1)[-1])
            self.refresh()

    def build_spectrum(self, gain):
        """The histogram for the current settings, or None for fixed energy.

        Bins are resolved against `gain` because the bin->volts law is only
        calibrated at DEFAULT_GAIN and scales with it.
        """
        m = self.cmb_energy.currentIndex()
        if m == 0:
            return None
        import spectrum as S
        p = self.win.p
        centre = p.bin_for_volts(self.sp_amp.value(), gain)
        # sigma in bins, from the volts width; bin 0 is unusable so keep >= 1
        sig = max(1.0, abs(p.bin_for_volts(self.sp_amp.value() +
                                           self.sp_sigma.value(), gain) - centre))
        if m == 1:
            return S.peaks((centre, sig, 1.0))
        if m == 2:
            second = p.bin_for_volts(self.sp_peak2.value(), gain)
            return S.peaks((centre, sig, 1.0),
                           (second, sig, self.sp_ratio.value()))
        if m == 3:
            return S.add_flat(S.empty(),
                              p.bin_for_volts(self.sp_flat_lo.value(), gain),
                              centre, 1.0)
        if m == 4:
            if not self.csv_path:
                raise ValueError("no spectrum file chosen")
            return S.from_csv(self.csv_path)
        return None

    def _dsb(self, lo, hi, val, suffix, dec):
        s = QDoubleSpinBox()
        s.setRange(lo, hi); s.setDecimals(dec)
        s.setValue(val); s.setSuffix(suffix)
        s.valueChanged.connect(self.refresh)
        return s

    def settings(self):
        """The kwargs for Pulser.set_pulse, straight off the widgets."""
        rise = self.sp_rise.value() or (T.RISE_FLOOR_S * 1e6)
        return dict(rate_hz=self.sp_rate.value(),
                    amplitude_v=self.sp_amp.value(),
                    decay_us=self.sp_decay.value(),
                    rise_us=rise,
                    baseline_v=self.sp_base.value(),
                    positive_going=(self.cmb_pol.currentIndex() == 0),
                    poisson=(self.cmb_time.currentIndex() == 1),
                    deadtime=self.sp_dead.value(),
                    paralyzable=self.chk_paral.isChecked(),
                    compensate_decay=self.chk_comp.isChecked(),
                    ch=self.ch)

    def noise_mv(self):
        return self.sp_noise.value()

    def refresh(self):
        t = THEME
        s = self.settings()
        try:
            per = period_for_rate(s['rate_hz'])
        except ValueError as e:
            self.lbl_reg.setText(f"<span style='color:{t['bad']}'>{e}</span>")
            return

        try:
            gain, _auto, extrap = P.auto_gain(s['amplitude_v'])
        except ValueError as e:
            self.lbl_reg.setText(f"<span style='color:{t['bad']}'>{e}</span>")
            self.lbl_chk.setText(f"<span style='color:{t['bad']}'>amplitude too low</span>")
            return
        er = max(1, min(32767, int(round(
            (s['amplitude_v'] * P.DEFAULT_GAIN / gain - P.V_INTERCEPT)
            / P.V_PER_ENERGY))))
        off = P.offset_for_ch(s['baseline_v'], self.ch, gain)
        inv = P.invert_for_ch(self.ch, s['positive_going'])
        self.lbl_reg.setText(
            f"<span style='color:{t['dim']}'>period {per} -> "
            f"{rate_for_period(per):.2f} Hz &nbsp; energy {er} -> "
            f"{volts_for_energy(er):.3f} V<br>gain {gain} &nbsp; offset {off} "
            f"&nbsp; invert {inv}</span>")

        samples, corn, rf, tf, sh = T.build(s['rise_us'] * 1e-6,
                                            s['decay_us'] * 1e-6)
        region_us = corn * max(1, rf) * T.DAC_DT * 1e6
        self.lbl_geom.setText(
            f"<span style='color:{t['dim']}'>corner {corn} samples (writes "
            f"{corn >> 1}; hardware doubles it), rise step "
            f"{sh['rise_dt_s']*1e9:.1f} ns, tail step {sh['tail_dt_s']*1e9:.0f} ns"
            f"<br>fine region ends at <b>{region_us:.2f} us</b> — the rise→tail "
            f"transition; peak is at {sh['peak_time_s']*1e6:.2f} us</span>")

        # the checks that actually bite on this instrument
        warn = []
        floor_ns = T.RISE_FLOOR_S * 1e9
        if s['rise_us'] * 1000 < floor_ns:
            warn.append(
                f"rise {s['rise_us']*1000:.0f} ns is at/below the {floor_ns:.0f} ns "
                f"floor — the corner clamps at {T.MIN_RISE_SAMPLES} samples and the "
                f"edge stops getting faster. The vendor reaches 31 ns; we do not, yet.")
        if er >= 32767:
            warn.append("energy register clipped at 32767 — lower the amplitude")
        if extrap:
            warn.append(f"{s['amplitude_v']:g} V needs a gain beyond the "
                        f"measured-linear region; amplitude will fall short")
        if not s['positive_going']:
            warn.append("negative polarity: the amplitude/baseline calibration "
                        "is positive-going only and the output may saturate")
        duty = s['rate_hz'] * s['decay_us'] * 1e-6
        if duty > 0.1:
            warn.append(f"rate x decay = {duty:.2f} — pulses will pile up")
        if self.cmb_energy.currentIndex() != 0:
            # the spectrum RAM tops out at bin 16383, below the fixed-energy max
            vmax = (P.V_PER_ENERGY * 2 * 16383 + P.V_INTERCEPT) * gain / P.DEFAULT_GAIN
            hi = max(s['amplitude_v'],
                     self.sp_peak2.value() if self.cmb_energy.currentIndex() == 2
                     else 0.0)
            if hi > vmax:
                warn.append(f"{hi:g} V is above the {vmax:.2f} V spectrum ceiling "
                            f"(bin 16383) at gain {gain} — it will clip to the top bin")
            if self.cmb_energy.currentIndex() == 4 and not self.csv_path:
                warn.append("no spectrum file chosen")
        if self.is_ch3:
            warn.insert(0, "drives CH1 and CH2 together. Shape and noise here "
                           "are programmed the way the vendor does, but NOT yet "
                           "verified on hardware; noise here should be "
                           "common-mode to both outputs")
        self.lbl_chk.setText(
            f"<span style='color:{t['ok']}'>ok</span>" if not warn else
            f"<span style='color:{t['warn']}'>" + "<br>".join(warn) + "</span>")

    def setEnabled(self, on):
        """Grey out, and say in the title WHY the panel is inert.

        Qt disables children automatically, but with a style sheet in force
        that is not visible on its own -- the disabled colours come from the
        :disabled rules in sheet().
        """
        super().setEnabled(on)
        if getattr(self, 'is_ch3', False):
            self.setTitle(self._title_base if on else
                          self._title_base + "   —  set Correlation to “Coincidence”")
        # The readout labels carry inline HTML colours, and inline colour beats
        # the :disabled style sheet rule -- an orange warning would otherwise
        # keep shouting from a dead panel. Fade them by hand, and let refresh()
        # put the real colours back when the panel comes alive again.
        if not hasattr(self, 'lbl_chk'):
            return                      # still constructing
        if on:
            self.refresh()
        else:
            import re as _re
            for lab in (self.lbl_reg, self.lbl_geom, self.lbl_chk):
                # keep the line breaks: <br> must become a break, not vanish
                plain = _re.sub(r'(?i)<br\s*/?>', '<br>', lab.text())
                plain = _re.sub(r'<(?!br>)[^>]+>', '', plain)
                lab.setText(f"<span style='color:{THEME['faint']}'>{plain}</span>")

    def set_enabled(self, on):
        for w in (self.cmb_time, self.sp_rate, self.sp_amp, self.sp_rise,
                  self.sp_decay, self.sp_base, self.sp_noise, self.cmb_pol,
                  self.sp_dead, self.chk_paral, self.chk_comp,
                  self.btn_apply, self.btn_stop):
            w.setEnabled(on)


class PulserWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DT5810B — Pulser Control")
        self.resize(1000, 720)
        self.p = Pulser()
        self.connected = False
        self.worker = None

        root = QWidget(); self.setCentralWidget(root)
        outer = QVBoxLayout(root)

        # ---- device ----
        g0 = QGroupBox("Device")
        l0 = QHBoxLayout(g0)
        self.lbl_conn = QLabel("connecting...")
        self.lbl_conn.setWordWrap(True)
        self.btn_conn = QPushButton("Retry connection")
        self.btn_conn.setToolTip(
            "Re-attempt the USB connection. If the emulator is at 21e1:000d it\n"
            "is in the FX3 bootloader and the firmware is loaded first; the\n"
            "firmware is volatile and goes after every cold power-up.")
        self.btn_conn.clicked.connect(self.do_connect)
        self.btn_conn.setVisible(False)
        self.chk_dark = QCheckBox("dark")
        self.chk_dark.setToolTip("Switch between the light and dark colour schemes.")
        self.chk_dark.stateChanged.connect(self.toggle_theme)
        l0.addWidget(self.lbl_conn, 1)
        l0.addWidget(self.btn_conn)
        l0.addWidget(self.chk_dark)
        outer.addWidget(g0)

        # ---- correlation block: channel sync and the third channel ----
        g1 = QGroupBox("Correlation")
        v1 = QVBoxLayout(g1)
        r1 = QHBoxLayout()
        self.cmb_corr = QComboBox()
        self.cmb_corr.addItems(["Off — channels free-run",
                                "Shared timebase — CH2 fires with CH1",
                                "Coincidence — channel 3 injects into both"])
        self.cmb_corr.setToolTip(
            "Off: two free-running timebases. Note they are then NOT independent\n"
            "in the useful sense -- at equal rates both divide the same clock, so\n"
            "they sit at a fixed but arbitrary phase offset.\n\n"
            "Shared timebase (mode 2): CH2 stops using its own timebase and fires\n"
            "with CH1, offset by the delay. It keeps its own amplitude, shape and\n"
            "polarity; only its RATE is taken over.\n\n"
            "Coincidence (mode 8, 'Ch3'): a third internal generator with its own\n"
            "rate and energy injects the SAME event into both outputs, so the two\n"
            "channels are ENERGY-correlated. CH1 and CH2 keep emitting their own\n"
            "uncorrelated events as well. The delay does NOT apply here.")
        self.cmb_corr.currentIndexChanged.connect(self.do_sync)
        r1.addWidget(self.cmb_corr)

        lo_ns, hi_ns = (-DELAY_ZERO_COUNTS * DELAY_NS_PER_COUNT,
                        (DELAY_MAX_COUNTS - DELAY_ZERO_COUNTS) * DELAY_NS_PER_COUNT)
        self.sp_delay = QDoubleSpinBox()
        self.sp_delay.setRange(lo_ns, hi_ns)
        self.sp_delay.setDecimals(1); self.sp_delay.setSingleStep(10.0)
        self.sp_delay.setValue(0.0); self.sp_delay.setSuffix(" ns")
        self.sp_delay.setToolTip(
            f"How far CH2 lags CH1 at the outputs. One 1.25 GS/s DAC sample =\n"
            f"{DELAY_NS_PER_COUNT} ns; range {lo_ns:.0f} to {hi_ns:.0f} ns. 0 means aligned --\n"
            "the emulator's fixed pipeline skew is already taken out.\n"
            "Shared-timebase mode only: measured to have NO effect on channel-3\n"
            "events, which arrive with a fixed ~47 ns skew.")
        self.sp_delay.valueChanged.connect(self.do_sync)
        self.lbl_delay = QLabel("CH2 delay")
        r1.addWidget(self.lbl_delay); r1.addWidget(self.sp_delay)
        v1.addLayout(r1)

        self.lbl_sync = QLabel("off — channels free-run")
        self.lbl_sync.setWordWrap(True)
        v1.addWidget(self.lbl_sync)
        outer.addWidget(g1)

        # ---- the two channels, side by side, independent ----
        chans = QHBoxLayout()
        self.panels = [ChannelPanel(0, self), ChannelPanel(1, self)]
        for pn in self.panels:
            chans.addWidget(pn)
        # the third generator: its own timebase and energy, output via CH1/CH2
        self.panel3 = ChannelPanel(P.Pulser.CH3, self, ch3=True)
        self.panel3.sp_rate.setValue(500.0)
        self.panel3.sp_amp.setValue(1.0)
        self.panel3.setEnabled(False)      # until coincidence mode is chosen
        chans.addWidget(self.panel3)
        outer.addLayout(chans, 1)

        self.log = QPlainTextEdit(); self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        self.log.setMaximumHeight(160)
        outer.addWidget(self.log)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage(
            "Gain, offset, polarity bit and shape geometry are all derived — "
            "see README.md. Corrected register addresses (docs/REGISTER_ADDRESS_BUG.md).")

        self.set_enabled(False)
        # connect ourselves once the window is up, rather than making the user do it
        QTimer.singleShot(150, self.do_connect)

    # ---- helpers ----
    def say(self, s):
        self.log.appendPlainText(f"[{time.strftime('%H:%M:%S')}] {s}")

    def set_enabled(self, on):
        for pn in self.panels:
            pn.set_enabled(on)
        self.cmb_corr.setEnabled(on)
        self.sp_delay.setEnabled(on)
        # group-box level, so the whole channel-3 panel greys out together
        self.panel3.setEnabled(on and self.cmb_corr.currentIndex() == 2)
        if on:
            self.do_sync()        # re-assert routing and control availability

    def busy(self, on):
        self.btn_conn.setEnabled(not on)
        self.set_enabled(self.connected and not on)

    # ---- device ----
    def do_connect(self):
        self.lbl_conn.setText("connecting...")
        self.btn_conn.setVisible(False)

        def job(note):
            import subprocess, time as _t

            def usb():
                try:
                    out = subprocess.run(["lsusb"], capture_output=True,
                                         text=True, timeout=5).stdout
                except Exception:
                    return None
                if "21e1:000e" in out:
                    return "run"
                if "21e1:000d" in out:
                    return "boot"
                return None

            st = usb()
            if st is None:
                raise RuntimeError(
                    "No DT5810B on USB. Check it is powered "
                    "(pdu/pduOnOff.sh on 3) and the cable is connected.")
            if st == "boot":
                note("emulator is in bootloader (000d) - loading FX3 firmware")
                loader = os.path.join(HERE, "fx3_firmware_loader.py")
                subprocess.run([sys.executable, loader], capture_output=True,
                               text=True, timeout=120)
                for _ in range(20):
                    _t.sleep(1)
                    if usb() == "run":
                        break
                else:
                    raise RuntimeError(
                        "Firmware load did not bring the emulator to 000e. "
                        "Try running fx3_firmware_loader.py by hand.")
                note("firmware loaded, emulator is at 000e")
            note("opening USB and running FPGA bringup")
            self.p.open()
            self.connected = True
            return "connected and brought up"
        self.start(job, "connect")

    def do_apply(self, ch):
        kw = self.panels[ch].settings()

        panel = self.panels[ch]
        mode = panel.cmb_energy.currentText()

        def job(note):
            note(f"CH{ch+1}: programming shape RAM + timebase + energy")
            # the spectrum must be loaded BEFORE set_pulse, so that _apply sees
            # the channel in _spectrum_ch and writes EnergyMode 1 rather than 0
            if mode == "Fixed":
                self.p.set_energy_fixed(1, ch=ch)     # value is set by set_pulse
            else:
                note(f"CH{ch+1}: building {mode.lower()} spectrum")
                hist = panel.build_spectrum(P.auto_gain(kw['amplitude_v'])[0])
                s = self.p.set_energy_spectrum(hist, ch=ch)
                note(f"CH{ch+1}: {s['nonzero']} of {s['bins']} bins populated")
            info = self.p.set_pulse(**kw)
            nz = panel.noise_mv()
            n = self.p.set_noise(nz, ch=ch)
            if nz:
                note(f"CH{ch+1}: noise {n['rms_mv_achievable']:.1f} mV rms "
                     f"(register {n['counts']})")
            tail = ("fixed energy %d" % info['energy_reg'] if mode == "Fixed"
                    else f"energy from {mode.lower()}")
            return (f"CH{ch+1} running: {info['rate_actual']:.2f} Hz, {tail}, "
                    f"offset {info['offset']}, invert {info['invert']}, "
                    f"{info['programming_passes']} pass(es)")
        self.start(job, f"apply CH{ch+1}")

    def do_sync(self):
        """Apply the correlation block. A handful of register writes, inline."""
        mode = self.cmb_corr.currentIndex()          # 0 off, 1 timebase, 2 ch3
        self.panel3.setEnabled(self.connected and mode == 2)
        self.sp_delay.setEnabled(self.connected and mode == 1)
        self.lbl_delay.setEnabled(mode == 1)
        # CH2's rate is taken over by CH1 in shared-timebase mode; say so rather
        # than leaving a live-looking control that does nothing
        rate2 = self.panels[1].sp_rate
        rate2.setEnabled(self.connected and mode != 1)
        # restore the real tooltip rather than blanking it -- an earlier
        # version set "" here and silently destroyed the control's help
        rate2.setToolTip(
            ("SLAVED TO CH1 while the timebase is shared — this box has no "
             "effect.\n\n" + rate2._tip_base) if mode == 1
            else rate2._tip_base)
        if not self.connected:
            return
        t = THEME
        try:
            if mode == 0:
                self.p.set_correlation(CORR_DISABLED)
                self.lbl_sync.setText(
                    f"<span style='color:{t['dim']}'>off — channels free-run "
                    f"(at equal rates they sit at a fixed arbitrary phase)</span>")
            elif mode == 1:
                info = self.p.set_correlation(CORR_TIMEBASE,
                                              delay_ns=self.sp_delay.value())
                self.lbl_sync.setText(
                    f"<span style='color:{t['ok']}'>CH2 follows CH1, lagging "
                    f"{info['achieved_ns']:+.1f} ns</span> "
                    f"<span style='color:{t['dim']}'>(delay register "
                    f"{info['delay_counts']}, {DELAY_NS_PER_COUNT} ns/step; "
                    f"CH2's own rate is ignored)</span>")
            else:
                self._program_ch3()
            self.say(f"correlation: {self.cmb_corr.currentText()}")
        except Exception as e:
            self.lbl_sync.setText(
                f"<span style='color:{THEME['bad']}'>correlation failed: {e}</span>")
            self.say(f"correlation failed: {e}")

    def _program_ch3(self):
        """Push the channel-3 panel's settings and enable coincidence mode."""
        pn = self.panel3
        st = pn.settings()
        hist = pn.build_spectrum(P.DEFAULT_GAIN)
        info = self.p.set_correlated_source(
            rate_hz=st['rate_hz'], amplitude_v=st['amplitude_v'],
            poisson=st['poisson'], hist=hist,
            rise_us=st['rise_us'], decay_us=st['decay_us'],
            noise_mv=pn.noise_mv())
        what = (f"{pn.cmb_energy.currentText().lower()}" if hist is not None
                else f"{st['amplitude_v']:g} V")
        t = THEME
        self.lbl_sync.setText(
            f"<span style='color:{t['ok']}'>channel 3 injecting {what} at "
            f"{st['rate_hz']:g} Hz into BOTH outputs</span> "
            f"<span style='color:{t['dim']}'>— energies correlated; CH1 and CH2 "
            f"keep their own events too. The CH2 delay does not apply "
            f"(fixed ~47 ns skew).</span>")
        return info

    def do_apply_ch3(self):
        if self.cmb_corr.currentIndex() != 2:
            self.cmb_corr.setCurrentIndex(2)       # triggers do_sync, which programs it
            return

        def job(note):
            note("channel 3: programming timebase + energy")
            info = self._program_ch3()
            return (f"channel 3 running: {info['ch3_rate_hz']:g} Hz, "
                    f"{'spectrum' if info['ch3_spectrum'] else 'fixed energy'}, "
                    f"mode reg 0x{info['mode_reg']:x}")
        self.start(job, "apply channel 3")

    def do_ch3_off(self):
        self.cmb_corr.setCurrentIndex(0)

    def do_stop(self, ch):
        # run() writes through self.ch, so point it at the channel being stopped
        try:
            prev, self.p.ch = self.p.ch, ch
            try:
                self.p.run(False)
            finally:
                self.p.ch = prev
            self.say(f"CH{ch+1} stopped (run gate 0x01c00006 = 0)")
        except Exception as e:
            self.say(f"CH{ch+1} stop failed: {e}")

    def start(self, job, what):
        self.busy(True); self.say(f"{what}...")
        self.worker = Worker(job)
        self.worker.note.connect(self.say)
        self.worker.done.connect(self.finish)
        self.worker.start()

    def finish(self, ok, msg):
        self.say(("OK: " if ok else "FAILED: ") + msg)
        t = THEME
        if self.connected:
            self.lbl_conn.setText(
                f"<span style='color:{t['ok']}'>connected — 21e1:000e</span>")
            self.btn_conn.setVisible(False)
        else:
            first = msg.splitlines()[0] if msg else "connection failed"
            self.lbl_conn.setText(
                f"<span style='color:{t['bad']}'>not connected</span> "
                f"<span style='color:{t['dim']}'>{first}</span>")
            self.btn_conn.setVisible(True)
        self.busy(False)

    # ---- theme ----
    def toggle_theme(self):
        global THEME
        THEME = DARK if self.chk_dark.isChecked() else LIGHT
        QApplication.instance().setStyleSheet(sheet(THEME))
        for pn in self.panels:
            pn.refresh()

    def closeEvent(self, ev):
        if self.connected:
            try:
                self.p.close()
            except Exception:
                pass
        ev.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(sheet(THEME))
    w = PulserWindow(); w.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
