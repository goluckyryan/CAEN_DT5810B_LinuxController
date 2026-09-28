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

Uses the CORRECTED timebase and energy register addresses (REGISTER_ADDRESS_BUG.md):
the project's usual 0x0100000x / 0x020f000x carry an extra hex zero and land 16x
away from the real registers, which is why rate and energy never responded.

The scope readback is strictly read-only apart from the trigger level.
"""
import sys, time, traceback

from PyQt6.QtCore import QThread, pyqtSignal, QTimer
from PyQt6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QLabel, QPushButton, QComboBox,
    QDoubleSpinBox, QSpinBox, QCheckBox, QGroupBox, QVBoxLayout, QHBoxLayout,
    QPlainTextEdit, QFormLayout, QStatusBar, QFileDialog,
)

sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')
import pulser as P
import tworegion as T
from pulser import (Pulser, period_for_rate, rate_for_period, volts_for_energy,
                    CORR_DISABLED, CORR_TIMEBASE, DELAY_NS_PER_COUNT,
                    DELAY_ZERO_COUNTS, DELAY_MAX_COUNTS)


LIGHT = dict(bg="#f7f8fa", panel="#ffffff", grid="#dfe3e8", text="#1f2328",
             dim="#6b7280", border="#d0d7de",
             ok="#1d4ed8", warn="#b45309", bad="#b91c1c", field="#ffffff")
DARK = dict(bg="#181b20", panel="#12151a", grid="#2a303a", text="#e5e7eb",
            dim="#9ca3af", border="#333b47",
            ok="#60a5fa", warn="#fbbf24", bad="#f87171", field="#12151a")
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

    def __init__(self, ch, win):
        super().__init__(f"Channel {ch + 1}")
        self.ch = ch
        self.win = win
        f = QFormLayout(self)

        self.cmb_time = QComboBox()
        self.cmb_time.addItems(["Constant rate", "Poisson"])
        self.cmb_time.currentTextChanged.connect(self.refresh)
        f.addRow("Timebase", self.cmb_time)

        self.sp_rate = self._dsb(0.01, 5e6, 1000.0, " Hz", 2)
        f.addRow("Rate", self.sp_rate)
        self.sp_amp = self._dsb(0.001, 2.0, 1.0, " V", 3)
        f.addRow("Amplitude", self.sp_amp)

        # --- energy: one fixed amplitude, or drawn from a spectrum ---
        self.cmb_energy = QComboBox()
        self.cmb_energy.addItems(["Fixed", "Gaussian peak", "Two peaks",
                                  "Flat continuum", "CSV file..."])
        self.cmb_energy.setToolTip(
            "Fixed = every pulse the same height (EnergyMode 0).\n"
            "The others load a histogram into the spectrum RAM and let the\n"
            "board draw each pulse's amplitude from it (EnergyMode 1), which is\n"
            "what makes this a source emulator rather than a pulser.\n"
            "'Amplitude' above becomes the peak centre / upper edge.")
        self.cmb_energy.currentIndexChanged.connect(self._energy_mode_changed)
        f.addRow("Energy", self.cmb_energy)

        self.sp_sigma = self._dsb(0.002, 1.0, 0.05, " V", 3)
        self.sp_sigma.setToolTip("Gaussian sigma of the peak, in volts.")
        self._row_sigma = f.rowCount(); f.addRow("Peak width", self.sp_sigma)
        self.sp_peak2 = self._dsb(0.005, 2.0, 0.5, " V", 3)
        self._row_peak2 = f.rowCount(); f.addRow("2nd peak", self.sp_peak2)
        self.sp_ratio = self._dsb(0.01, 100.0, 1.0, "", 2)
        self.sp_ratio.setToolTip("Intensity of the 2nd peak relative to the 1st.")
        self._row_ratio = f.rowCount(); f.addRow("2nd/1st", self.sp_ratio)
        self.sp_flat_lo = self._dsb(0.005, 2.0, 0.1, " V", 3)
        self._row_flatlo = f.rowCount(); f.addRow("Continuum from", self.sp_flat_lo)
        csv = QHBoxLayout()
        self.lbl_csv = QLabel("(none)"); self.lbl_csv.setWordWrap(True)
        self.btn_csv = QPushButton("Browse...")
        self.btn_csv.clicked.connect(self._pick_csv)
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
        f.addRow("Rise (10-90%)", self.sp_rise)
        self.sp_decay = self._dsb(0.05, 5000.0, 50.0, " us", 2)
        f.addRow("Decay tau", self.sp_decay)
        self.sp_base = self._dsb(-1.5, 1.5, 0.0, " V", 3)
        f.addRow("Baseline", self.sp_base)

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
        f.addRow("Polarity", self.cmb_pol)

        self.sp_dead = QSpinBox(); self.sp_dead.setRange(0, 2**31 - 1)
        f.addRow("Dead time (counts)", self.sp_dead)
        self.chk_paral = QCheckBox("paralyzable")
        f.addRow("", self.chk_paral)
        self.chk_comp = QCheckBox(f"compensate decay (x1/{P.DECAY_SCALE})")
        self.chk_comp.setChecked(True)
        f.addRow("", self.chk_comp)

        self.lbl_reg = QLabel("-"); self.lbl_reg.setWordWrap(True)
        f.addRow("Registers", self.lbl_reg)
        self.lbl_geom = QLabel("-"); self.lbl_geom.setWordWrap(True)
        f.addRow("Shape", self.lbl_geom)
        self.lbl_chk = QLabel("-"); self.lbl_chk.setWordWrap(True)
        f.addRow("Checks", self.lbl_chk)
        self.lbl_meas = QLabel("-"); self.lbl_meas.setWordWrap(True)
        f.addRow("Scope", self.lbl_meas)

        row = QHBoxLayout()
        self.btn_apply = QPushButton("Apply + Run")
        self.btn_apply.clicked.connect(lambda: win.do_apply(self.ch))
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.clicked.connect(lambda: win.do_stop(self.ch))
        row.addWidget(self.btn_apply); row.addWidget(self.btn_stop)
        holder = QWidget(); holder.setLayout(row)
        f.addRow("", holder)

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

    def refresh(self):
        t = THEME
        s = self.settings()
        try:
            per = period_for_rate(s['rate_hz'])
        except ValueError as e:
            self.lbl_reg.setText(f"<span style='color:{t['bad']}'>{e}</span>")
            return

        gain, _auto, extrap = P.auto_gain(s['amplitude_v'])
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
        self.lbl_chk.setText(
            f"<span style='color:{t['ok']}'>ok</span>" if not warn else
            f"<span style='color:{t['warn']}'>" + "<br>".join(warn) + "</span>")

    def set_meas(self, html):
        self.lbl_meas.setText(html)

    def set_enabled(self, on):
        for w in (self.cmb_time, self.sp_rate, self.sp_amp, self.sp_rise,
                  self.sp_decay, self.sp_base, self.cmb_pol, self.sp_dead,
                  self.chk_paral, self.chk_comp, self.btn_apply, self.btn_stop):
            w.setEnabled(on)


class PulserWindow(QMainWindow):
    # scope readback is an envelope over this many polls (see poll_scope)
    ENV_POLLS = 30

    def __init__(self):
        super().__init__()
        self.setWindowTitle("DT5810B — Pulser Control")
        self.resize(1000, 720)
        self.p = Pulser()
        self.connected = False
        self.scope = None
        self.worker = None

        root = QWidget(); self.setCentralWidget(root)
        outer = QVBoxLayout(root)

        # ---- device ----
        g0 = QGroupBox("Device")
        l0 = QHBoxLayout(g0)
        self.lbl_conn = QLabel("connecting...")
        self.lbl_conn.setWordWrap(True)
        self.btn_conn = QPushButton("Retry connection")
        self.btn_conn.clicked.connect(self.do_connect)
        self.btn_conn.setVisible(False)
        self.chk_scope = QCheckBox("poll scope 192.168.2.200")
        self.chk_scope.stateChanged.connect(self.toggle_scope)
        self.chk_dark = QCheckBox("dark")
        self.chk_dark.stateChanged.connect(self.toggle_theme)
        l0.addWidget(self.lbl_conn, 1)
        l0.addWidget(self.btn_conn)
        l0.addWidget(self.chk_scope)
        l0.addWidget(self.chk_dark)
        outer.addWidget(g0)

        # ---- channel sync (correlation block) ----
        g1 = QGroupBox("Channel sync")
        l1 = QHBoxLayout(g1)
        self.chk_sync = QCheckBox("Sync CH2 to CH1 (shared timebase)")
        self.chk_sync.setToolTip(
            "Correlation mode 2, \"Shared Time Based Generator\" (manual sec 9.3).\n"
            "CH2 stops using its own timebase and fires with CH1, offset by the\n"
            "delay below. CH2 keeps its own amplitude, shape and polarity; only\n"
            "its RATE is taken over by CH1.\n"
            "Without this the two channels free-run. They are then NOT\n"
            "independent in the useful sense: at equal rates both divide the same\n"
            "clock, so they sit at a fixed but arbitrary phase offset.")
        self.chk_sync.stateChanged.connect(self.do_sync)
        lo_ns, hi_ns = (-DELAY_ZERO_COUNTS * DELAY_NS_PER_COUNT,
                        (DELAY_MAX_COUNTS - DELAY_ZERO_COUNTS) * DELAY_NS_PER_COUNT)
        self.sp_delay = QDoubleSpinBox()
        self.sp_delay.setRange(lo_ns, hi_ns)
        self.sp_delay.setDecimals(1); self.sp_delay.setSingleStep(10.0)
        self.sp_delay.setValue(0.0); self.sp_delay.setSuffix(" ns")
        self.sp_delay.setToolTip(
            f"How far CH2 lags CH1 at the outputs. Step is one 1.25 GS/s DAC\n"
            f"sample = {DELAY_NS_PER_COUNT} ns; range {lo_ns:.0f} to {hi_ns:.0f} ns.\n"
            "0 means aligned: the board's fixed pipeline skew (CH2 leads by\n"
            "~57 ns at register 0) is already taken out.")
        self.sp_delay.valueChanged.connect(self.do_sync)
        self.lbl_sync = QLabel("off — channels free-run")
        self.lbl_sync.setWordWrap(True)
        l1.addWidget(self.chk_sync)
        l1.addWidget(QLabel("CH2 delay"))
        l1.addWidget(self.sp_delay)
        l1.addWidget(self.lbl_sync, 1)
        outer.addWidget(g1)

        # ---- the two channels, side by side, independent ----
        chans = QHBoxLayout()
        self.panels = [ChannelPanel(0, self), ChannelPanel(1, self)]
        for pn in self.panels:
            chans.addWidget(pn)
        outer.addLayout(chans, 1)

        self.log = QPlainTextEdit(); self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        self.log.setMaximumHeight(160)
        outer.addWidget(self.log)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage(
            "Gain, offset, polarity bit and shape geometry are all derived — "
            "see README.md. Corrected register addresses (REGISTER_ADDRESS_BUG.md).")

        self.scope_timer = QTimer(self)
        self.scope_timer.timeout.connect(self.poll_scope)
        self.set_enabled(False)
        # connect ourselves once the window is up, rather than making the user do it
        QTimer.singleShot(150, self.do_connect)

    # ---- helpers ----
    def say(self, s):
        self.log.appendPlainText(f"[{time.strftime('%H:%M:%S')}] {s}")

    def set_enabled(self, on):
        for pn in self.panels:
            pn.set_enabled(on)
        self.chk_sync.setEnabled(on)
        self.sp_delay.setEnabled(on)
        if on and self.chk_sync.isChecked():
            self.panels[1].sp_rate.setEnabled(False)   # slaved to CH1

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
                note("board is in bootloader (000d) - loading FX3 firmware")
                loader = "/home/ryan/caen_signal_emulator/linux/fx3_firmware_loader.py"
                subprocess.run([sys.executable, loader], capture_output=True,
                               text=True, timeout=120)
                for _ in range(20):
                    _t.sleep(1)
                    if usb() == "run":
                        break
                else:
                    raise RuntimeError(
                        "Firmware load did not bring the board to 000e. "
                        "Try running fx3_firmware_loader.py by hand.")
                note("firmware loaded, board is at 000e")
            note("opening USB and running FPGA bringup")
            self.p.open()
            self.connected = True
            return "connected and brought up"
        self.start(job, "connect")

    def do_apply(self, ch):
        kw = self.panels[ch].settings()
        # the old envelope describes the old settings
        if hasattr(self.panels[ch], '_env'):
            self.panels[ch]._env.clear()

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
            tail = ("fixed energy %d" % info['energy_reg'] if mode == "Fixed"
                    else f"energy from {mode.lower()}")
            return (f"CH{ch+1} running: {info['rate_actual']:.2f} Hz, {tail}, "
                    f"offset {info['offset']}, invert {info['invert']}, "
                    f"{info['programming_passes']} pass(es)")
        self.start(job, f"apply CH{ch+1}")

    def do_sync(self):
        """Apply the correlation block. Four register writes — done inline."""
        on = self.chk_sync.isChecked()
        # CH2's rate is taken over by CH1 when synced; say so rather than
        # leaving a live-looking control that does nothing
        rate2 = self.panels[1].sp_rate
        rate2.setEnabled(self.connected and not on)
        rate2.setToolTip("slaved to CH1 while Channel sync is on" if on else "")
        if not self.connected:
            return
        try:
            info = self.p.set_correlation(
                CORR_TIMEBASE if on else CORR_DISABLED,
                delay_ns=self.sp_delay.value())
            t = THEME
            if on:
                self.lbl_sync.setText(
                    f"<span style='color:{t['ok']}'>CH2 follows CH1, lagging "
                    f"{info['achieved_ns']:+.1f} ns</span> "
                    f"<span style='color:{t['dim']}'>(register {info['delay_counts']}, "
                    f"{DELAY_NS_PER_COUNT} ns/step; CH2's own rate is ignored)</span>")
            else:
                self.lbl_sync.setText(
                    f"<span style='color:{t['dim']}'>off — channels free-run "
                    f"(at equal rates they sit at a fixed arbitrary phase)</span>")
            self.say(f"sync {'on' if on else 'off'}: {info}")
        except Exception as e:
            self.lbl_sync.setText(
                f"<span style='color:{THEME['bad']}'>sync failed: {e}</span>")
            self.say(f"sync failed: {e}")

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

    # ---- theme / scope ----
    def toggle_theme(self):
        global THEME
        THEME = DARK if self.chk_dark.isChecked() else LIGHT
        QApplication.instance().setStyleSheet(sheet(THEME))
        for pn in self.panels:
            pn.refresh()

    def toggle_scope(self):
        if self.chk_scope.isChecked():
            try:
                from scope import Scope
                self.scope = Scope()
                self.scope_timer.start(1500)
                self.say("scope connected (read-only)")
            except Exception as e:
                self.say(f"scope unavailable: {e}")
                self.chk_scope.setChecked(False)
        else:
            self.scope_timer.stop()
            if self.scope:
                self.scope.close(); self.scope = None

    def poll_scope(self):
        """Read both channels back with per-channel :MEAS:ITEM? queries.

        These need no :WAV:SOUR, so both panels can read without touching the
        scope's setup.

        Reported as an ENVELOPE over the last few polls, not a single snapshot.
        Both channels' timebases divide the same clock, so at equal rates they
        are phase-locked: the channel that is not the trigger source sits at a
        fixed offset and a single acquisition often catches only its baseline
        (0.08 V "negative-going" for a healthy 1 V pulse). The envelope reports
        the real amplitude once the pulse has landed in the window at least
        once. Detune one channel ~0.5% to make its phase walk.
        """
        if not self.scope:
            return
        try:
            trig_src = self.scope.q(':TRIG:EDGE:SOUR?').strip().upper()
        except Exception:
            trig_src = ''
        for i, pn in enumerate(self.panels):
            c = i + 1
            triggered = trig_src.endswith(str(c))
            try:
                g = lambda k: self.scope.qf(f':MEAS:ITEM? {k},CHAN{c}')
                vmin, vmax, vavg = g('VMIN'), g('VMAX'), g('VAVG')
                if None in (vmin, vmax, vavg):
                    pn.set_meas(f"<span style='color:{THEME['dim']}'>scope CH{c}: "
                                f"no measurement (not triggered?)</span>")
                    continue
                hist = getattr(pn, '_env', None)
                if hist is None:
                    from collections import deque
                    hist = pn._env = deque(maxlen=self.ENV_POLLS)
                hist.append((vmin, vmax, vavg))
                lo = min(h[0] for h in hist)
                hi = max(h[1] for h in hist)
                mid = sum(h[2] for h in hist) / len(hist)
                pos = abs(mid - lo) < abs(mid - hi)
                base = lo if pos else hi
                note = (f"envelope, {len(hist)} polls" if triggered else
                        f"envelope, {len(hist)} polls — <b>scope triggers on "
                        f"{trig_src or '?'}</b>, so this amplitude is a LOWER "
                        f"BOUND until the pulse drifts into the window")
                pn.set_meas(
                    f"<span style='color:"
                    f"{THEME['dim'] if triggered else THEME['warn']}'>"
                    f"scope CH{c}: amp {hi-lo:.3f} V, baseline {base:+.3f} V, "
                    f"{'positive' if pos else 'negative'}-going "
                    f"<i>({note})</i></span>")
            except Exception as e:
                pn.set_meas(f"<span style='color:{THEME['bad']}'>read error: "
                            f"{e}</span>")

    def closeEvent(self, ev):
        self.scope_timer.stop()
        if self.scope:
            self.scope.close()
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
