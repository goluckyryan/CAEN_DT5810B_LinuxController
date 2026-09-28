#!/usr/bin/env python3
"""DT5810B AWG control GUI (PyQt6).

    python3 awg_gui.py

Focused on AWG mode: pick a shape, set the rate and the shape parameters, see
the exact array that will be uploaded, and apply it. Uploads run on a worker
thread so the UI stays responsive (a 1 kHz array is ~100k samples).

The optional scope panel is STRICTLY READ-ONLY apart from the trigger level,
which is the only write Ryan has authorised.
"""
import sys, time, traceback

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QPointF, QRectF
from PyQt6.QtGui import QPainter, QPen, QColor, QFont, QPainterPath
from PyQt6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QLabel, QPushButton, QComboBox,
    QDoubleSpinBox, QSpinBox, QCheckBox, QGroupBox, QGridLayout, QVBoxLayout,
    QHBoxLayout, QPlainTextEdit, QFormLayout, QStatusBar, QSizePolicy,
)

sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')
from awg_backend import (AWGDevice, build_waveform, plan_rate, SHAPES,
                         FREQ_CLK, MAX_DATALEN, V_PER_LSB_1M, BASELINE_V)


# ------------------------------------------------------------------- preview --
class WavePlot(QWidget):
    """Lightweight waveform preview. No matplotlib dependency."""

    def __init__(self):
        super().__init__()
        self.pts = []
        self.dt = 1e-9
        self.setMinimumHeight(240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)

    def set_wave(self, pts, dt):
        self.pts = pts or []
        self.dt = dt
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(46, 12, -12, -26)
        p.fillRect(self.rect(), QColor("#12151a"))
        p.setPen(QPen(QColor("#2a303a"), 1))
        for i in range(5):
            y = r.top() + r.height() * i / 4
            p.drawLine(int(r.left()), int(y), int(r.right()), int(y))
        for i in range(9):
            x = r.left() + r.width() * i / 8
            p.drawLine(int(x), int(r.top()), int(x), int(r.bottom()))

        if not self.pts:
            p.setPen(QColor("#6b7280"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, "no waveform")
            return

        n = len(self.pts)
        lo, hi = min(self.pts), max(self.pts)
        if hi == lo:
            hi = lo + 1
        # the output is inverted wrt the array: show what the SCOPE will see
        vlo, vhi = -hi * V_PER_LSB_1M, -lo * V_PER_LSB_1M
        span = (vhi - vlo) or 1.0

        # decimate to at most one point per pixel column
        cols = max(2, r.width())
        step = max(1, n // cols)
        path = QPainterPath()
        first = True
        for i in range(0, n, step):
            v = -self.pts[i] * V_PER_LSB_1M
            x = r.left() + r.width() * i / (n - 1)
            y = r.bottom() - r.height() * (v - vlo) / span
            if first:
                path.moveTo(QPointF(x, y))
                first = False
            else:
                path.lineTo(QPointF(x, y))
        p.setPen(QPen(QColor("#4ade80"), 1.6))
        p.drawPath(path)

        f = QFont()
        f.setPointSize(8)
        p.setFont(f)
        p.setPen(QColor("#9ca3af"))
        p.drawText(QRectF(2, r.top() - 8, 42, 16),
                   Qt.AlignmentFlag.AlignRight, f"{vhi:+.2f}V")
        p.drawText(QRectF(2, r.bottom() - 8, 42, 16),
                   Qt.AlignmentFlag.AlignRight, f"{vlo:+.2f}V")
        total_us = n * self.dt * 1e6
        p.drawText(QRectF(r.left(), r.bottom() + 6, 120, 16),
                   Qt.AlignmentFlag.AlignLeft, "0")
        p.drawText(QRectF(r.right() - 120, r.bottom() + 6, 120, 16),
                   Qt.AlignmentFlag.AlignRight, f"{total_us:.1f} us")


# -------------------------------------------------------------------- worker --
class Worker(QThread):
    done = pyqtSignal(bool, str)
    note = pyqtSignal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            msg = self.fn(self.note.emit) or "done"
            self.done.emit(True, msg)
        except Exception as e:
            self.done.emit(False, f"{type(e).__name__}: {e}\n"
                                  f"{traceback.format_exc(limit=3)}")


# ---------------------------------------------------------------------- main --
class AwgWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DT5810B — AWG Control")
        self.resize(1080, 700)
        self.dev = AWGDevice()
        self.worker = None
        self.scope = None

        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        left = QVBoxLayout()
        right = QVBoxLayout()
        outer.addLayout(left, 0)
        outer.addLayout(right, 1)

        # ---- connection ----
        gc = QGroupBox("Device")
        fc = QGridLayout(gc)
        self.lbl_conn = QLabel("not connected")
        self.btn_conn = QPushButton("Connect + bringup")
        self.btn_conn.clicked.connect(self.do_connect)
        fc.addWidget(self.lbl_conn, 0, 0, 1, 2)
        fc.addWidget(self.btn_conn, 1, 0, 1, 2)
        left.addWidget(gc)

        # ---- waveform ----
        gw = QGroupBox("Waveform")
        fw = QFormLayout(gw)
        self.cmb_shape = QComboBox()
        self.cmb_shape.addItems(SHAPES)
        self.cmb_shape.currentTextChanged.connect(self.refresh)
        fw.addRow("Shape", self.cmb_shape)

        self.sp_rate = QDoubleSpinBox()
        self.sp_rate.setRange(0.01, 5e6)
        self.sp_rate.setDecimals(2)
        self.sp_rate.setValue(1000.0)
        self.sp_rate.setSuffix(" Hz")
        self.sp_rate.valueChanged.connect(self.refresh)
        fw.addRow("Rate", self.sp_rate)

        self.chk_autocps = QCheckBox("auto (finest resolution)")
        self.chk_autocps.setChecked(True)
        self.chk_autocps.stateChanged.connect(self.refresh)
        self.sp_cps = QSpinBox()
        self.sp_cps.setRange(1, 4096)
        self.sp_cps.setValue(31)
        self.sp_cps.valueChanged.connect(self.refresh)
        hb = QHBoxLayout()
        hb.addWidget(self.sp_cps)
        hb.addWidget(self.chk_autocps)
        w = QWidget(); w.setLayout(hb)
        fw.addRow("ClockPerStep", w)

        self.sp_peak = QSpinBox()
        self.sp_peak.setRange(1, 32575)
        self.sp_peak.setValue(7065)
        self.sp_peak.valueChanged.connect(self.refresh)
        fw.addRow("Peak (DAC codes)", self.sp_peak)

        self.sp_rise = QDoubleSpinBox()
        self.sp_rise.setRange(1.0, 1e6)
        self.sp_rise.setDecimals(1)
        self.sp_rise.setValue(100.0)
        self.sp_rise.setSuffix(" ns")
        self.sp_rise.valueChanged.connect(self.refresh)
        fw.addRow("Rise (10-90%)", self.sp_rise)

        self.sp_decay = QDoubleSpinBox()
        self.sp_decay.setRange(0.01, 10000.0)
        self.sp_decay.setDecimals(2)
        self.sp_decay.setValue(50.0)
        self.sp_decay.setSuffix(" us")
        self.sp_decay.valueChanged.connect(self.refresh)
        fw.addRow("Decay tau", self.sp_decay)

        self.sp_duty = QDoubleSpinBox()
        self.sp_duty.setRange(0.1, 99.9)
        self.sp_duty.setValue(50.0)
        self.sp_duty.setSuffix(" %")
        self.sp_duty.valueChanged.connect(self.refresh)
        fw.addRow("Duty / symmetry", self.sp_duty)

        self.chk_pos = QCheckBox("positive-going")
        self.chk_pos.setChecked(True)
        self.chk_pos.stateChanged.connect(self.refresh)
        fw.addRow("", self.chk_pos)
        left.addWidget(gw)

        # ---- derived ----
        gd = QGroupBox("Derived")
        fd = QFormLayout(gd)
        self.lbl_dlen = QLabel("-")
        self.lbl_dt = QLabel("-")
        self.lbl_actual = QLabel("-")
        self.lbl_res = QLabel("-")
        self.lbl_amp = QLabel("-")
        for k, v in (("DataLen", self.lbl_dlen), ("Sample period", self.lbl_dt),
                     ("Actual rate", self.lbl_actual),
                     ("Shape resolution", self.lbl_res),
                     ("Amplitude (1 MOhm)", self.lbl_amp)):
            fd.addRow(k, v)
        left.addWidget(gd)

        # ---- actions ----
        ga = QGroupBox("Output")
        fa = QHBoxLayout(ga)
        self.btn_apply = QPushButton("Apply + Run")
        self.btn_apply.clicked.connect(self.do_apply)
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.clicked.connect(self.do_stop)
        for b in (self.btn_apply, self.btn_stop):
            b.setEnabled(False)
        fa.addWidget(self.btn_apply)
        fa.addWidget(self.btn_stop)
        left.addWidget(ga)
        left.addStretch(1)

        # ---- right: preview, scope, log ----
        self.plot = WavePlot()
        right.addWidget(self.plot, 2)

        gs = QGroupBox("Scope readback (read-only; only the trigger level is ever written)")
        fs = QHBoxLayout(gs)
        self.chk_scope = QCheckBox("poll 192.168.2.200")
        self.chk_scope.stateChanged.connect(self.toggle_scope)
        self.lbl_scope = QLabel("-")
        fs.addWidget(self.chk_scope)
        fs.addWidget(self.lbl_scope, 1)
        right.addWidget(gs)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        right.addWidget(self.log, 1)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage(
            f"AWG baseline is fixed at about {BASELINE_V:+.2f} V and cannot be "
            f"moved — see REPORT.md")

        self.scope_timer = QTimer(self)
        self.scope_timer.timeout.connect(self.poll_scope)

        self.refresh()
        self.probe()

    # ------------------------------------------------------------ helpers --
    def say(self, s):
        self.log.appendPlainText(f"[{time.strftime('%H:%M:%S')}] {s}")

    def probe(self):
        st = self.dev.present()
        if st == "run":
            self.lbl_conn.setText("found 21e1:000e — ready to connect")
        elif st == "boot":
            self.lbl_conn.setText("found 21e1:000d (bootloader) — run "
                                  "fx3_firmware_loader.py first")
        else:
            self.lbl_conn.setText("no DT5810B on USB — is it powered?")

    def plan(self):
        cps = None if self.chk_autocps.isChecked() else self.sp_cps.value()
        return plan_rate(self.sp_rate.value(), cps)

    def params(self):
        return dict(rise_ns=self.sp_rise.value(),
                    decay_us=self.sp_decay.value(),
                    duty=self.sp_duty.value(),
                    symmetry=self.sp_duty.value())

    def refresh(self):
        try:
            cps, dlen, actual, cps_min = self.plan()
        except Exception as e:
            self.lbl_dlen.setText(f"<span style='color:#f87171'>{e}</span>")
            return
        dt = cps / FREQ_CLK
        if self.chk_autocps.isChecked():
            self.sp_cps.blockSignals(True)
            self.sp_cps.setValue(cps)
            self.sp_cps.blockSignals(False)
        self.sp_cps.setEnabled(not self.chk_autocps.isChecked())

        self.lbl_dlen.setText(f"{dlen}  (max {MAX_DATALEN})")
        self.lbl_dt.setText(f"{dt*1e9:.2f} ns  (CPS {cps}, min {cps_min})")
        err = abs(actual - self.sp_rate.value()) / max(1e-9, self.sp_rate.value())
        col = "#4ade80" if err < 0.01 else "#fbbf24"
        self.lbl_actual.setText(
            f"<span style='color:{col}'>{actual:.2f} Hz</span>")

        shape = self.cmb_shape.currentText()
        if shape == "Detector pulse":
            nr = self.sp_rise.value() * 1e-9 / dt
            nd = self.sp_decay.value() * 1e-6 / dt
            col = "#4ade80" if nr >= 3 else "#f87171"
            self.lbl_res.setText(
                f"rise <span style='color:{col}'>{nr:.1f} samples</span>, "
                f"decay {nd:.0f} samples")
        else:
            self.lbl_res.setText(f"{dlen} samples/period")

        v = self.sp_peak.value() * V_PER_LSB_1M
        self.lbl_amp.setText(f"~{v:.3f} V peak  (baseline {BASELINE_V:+.2f} V)")

        # preview: decimate long arrays so the UI stays snappy
        prev_n = min(dlen, 4000)
        stride = max(1, dlen // prev_n)
        pts = build_waveform(shape, dlen, dt, self.sp_peak.value(),
                             self.params(), self.chk_pos.isChecked())
        self.plot.set_wave(pts[::stride], dt * stride)
        self._pts = pts
        self._cps = cps

    # -------------------------------------------------------------- actions --
    def busy(self, on):
        for b in (self.btn_conn, self.btn_apply, self.btn_stop):
            b.setEnabled(not on)
        if not on:
            self.btn_apply.setEnabled(self.dev.is_open)
            self.btn_stop.setEnabled(self.dev.is_open)

    def do_connect(self):
        def job(note):
            note("opening USB")
            self.dev.open()
            self.dev.bringup(note)
            return "connected and brought up"
        self.start(job, "connect")

    def do_apply(self):
        pts, cps = self._pts, self._cps

        def job(note):
            n = self.dev.program(pts, cps, progress=note)
            return f"uploaded {n} samples, ClockPerStep={cps}, running"
        self.start(job, "apply")

    def do_stop(self):
        try:
            self.dev.run(False)
            self.say("output stopped (run gate 0x01c00006 = 0)")
        except Exception as e:
            self.say(f"stop failed: {e}")

    def start(self, job, what):
        self.busy(True)
        self.say(f"{what}...")
        self.worker = Worker(job)
        self.worker.note.connect(self.say)
        self.worker.done.connect(lambda ok, msg: self.finish(ok, msg))
        self.worker.start()

    def finish(self, ok, msg):
        self.say(("OK: " if ok else "FAILED: ") + msg)
        self.busy(False)
        if ok:
            self.lbl_conn.setText("connected (21e1:000e)")

    # ---------------------------------------------------------------- scope --
    def toggle_scope(self):
        if self.chk_scope.isChecked():
            try:
                from scope import Scope
                self.scope = Scope()
                self.scope_timer.start(3000)
                self.say("scope connected (read-only)")
            except Exception as e:
                self.say(f"scope unavailable: {e}")
                self.chk_scope.setChecked(False)
        else:
            self.scope_timer.stop()
            if self.scope:
                self.scope.close()
                self.scope = None

    def poll_scope(self):
        if not self.scope:
            return
        try:
            m = self.scope.meas(1)
            f = lambda k: ("%.4g" % m[k]) if m[k] is not None else "-"
            self.lbl_scope.setText(
                f"Vpp {f('VPP')} V   Vmin {f('VMIN')} V   Vmax {f('VMAX')} V   "
                f"FREQ {f('FREQ')} Hz")
        except Exception as e:
            self.lbl_scope.setText(f"read error: {e}")

    def closeEvent(self, ev):
        self.scope_timer.stop()
        if self.scope:
            self.scope.close()
        if self.dev.is_open:
            self.dev.close()
        ev.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet("""
        QWidget { background:#181b20; color:#e5e7eb; font-size:12px; }
        QGroupBox { border:1px solid #2a303a; border-radius:6px; margin-top:9px;
                    padding-top:8px; font-weight:600; }
        QGroupBox::title { subcontrol-origin:margin; left:8px; padding:0 4px;
                           color:#9ca3af; }
        QPushButton { background:#232830; border:1px solid #333b47;
                      border-radius:5px; padding:6px 12px; }
        QPushButton:hover:enabled { background:#2c323c; }
        QPushButton:disabled { color:#6b7280; }
        QPlainTextEdit { background:#12151a; border:1px solid #2a303a;
                         font-family:monospace; }
        QSpinBox, QDoubleSpinBox, QComboBox { background:#12151a;
                         border:1px solid #333b47; border-radius:4px;
                         padding:3px; }
    """)
    w = AwgWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
