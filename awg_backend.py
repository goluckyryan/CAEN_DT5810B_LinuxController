"""DT5810B AWG-mode backend.

Everything the AWG needs, with the behaviour established by measurement on
2026-09-22/23. See REPORT.md and FINDINGS.md for the evidence.

FACTS THAT SHAPE THIS API
-------------------------
* Rate is not a timebase register. In AWG mode:
      rate = FREQ_CLK / (DataLen * ClockPerStep),   FREQ_CLK = 312.5 MHz
  Verified: DataLen=1008, CPS=31 -> 10001 Hz predicted, 10000 Hz measured.

* AWG BYPASSES THE ANALOG STAGE. Measured directly: analog gain 0x0f000001
  (300 -> 1500, a 5x change) and analog offset 0x0f000000 (0 -> -70000) both
  leave the output unchanged, as does invert 0x0f000004. So amplitude, polarity
  and DC must all be baked into the sample array.
  (NOTE: AWG_REGISTERS.md lines 38-39 claim these registers work in AWG mode.
  They do not - that law was almost certainly measured in Pulser mode.)

* The output is INVERTED with respect to the array: a positive array gives a
  negative-going pulse. `positive_going=True` emits negative samples to
  compensate.

* Amplitude is linear in the array peak:
      1 MOhm:  V_peak ~= 1.40e-4 * peak_lsb      (7065 -> ~0.99 V)
      50 Ohm:  about half that.

* The baseline sits at about -0.14 V and CANNOT be moved. Array DC was swept
  -926 to -4085 counts (~0.6 V worth) with no change, so the path appears to be
  AC-coupled or to strip the DC term. There is no working offset control.

* ClockPerStep IS honoured exactly. Verified by reaching the same rate two ways:
      CPS=3,  DataLen=20832 -> 5000 Hz predicted, 5003 Hz measured (implied 3.00)
      CPS=31, DataLen=2016  -> 5000 Hz predicted, 5000 Hz measured (implied 31.00)
      CPS=3,  DataLen=5200  -> 20032 Hz predicted, 20160 measured (implied 2.98)
  (An earlier note in this folder called CPS unreliable. That was a broken peak
  detector splitting one wide pulse into several, not the device.)

  So the rate formula is exact and `plan_rate` can safely pick the smallest CPS
  for the finest time resolution.
"""
import ctypes, math, struct, time

VID, PID, PID_BOOT = 0x21e1, 0x000e, 0x000d
EP_OUT, EP_IN = 0x02, 0x81

FREQ_CLK = 312.5e6          # AWG sample clock
TICK_NS = 1e9 / FREQ_CLK    # 3.2 ns
MAX_DATALEN = 131072
DAC_CLAMP = 32575

V_PER_LSB_1M = 1.40e-4      # measured at 1 MOhm
BASELINE_V = -0.14          # fixed, not controllable

SHAPES = ["Detector pulse", "Sine", "Square", "Triangle", "Sawtooth",
          "Pulse", "Sinc", "DC", "Noise"]


def reg(ch, off):
    return (ch << 28) | off


# ----------------------------------------------------------------- waveform --
def build_waveform(shape, n, dt_s, peak, params=None, positive_going=True):
    """Return a list of n signed DAC codes.

    dt_s is the real time per array sample (ClockPerStep / FREQ_CLK).
    `peak` is the array peak magnitude in DAC codes.
    """
    p = params or {}
    y = [0.0] * n

    if shape == "Detector pulse":
        rise_ns = max(1e-3, float(p.get("rise_ns", 100.0)))
        decay_us = max(1e-3, float(p.get("decay_us", 50.0)))
        tau_r = (rise_ns * 1e-9) / 2.197        # 10-90% of one pole = 2.197 tau
        tau_d = decay_us * 1e-6
        for i in range(n):
            t = i * dt_s
            y[i] = (1.0 - math.exp(-t / tau_r)) * math.exp(-t / tau_d)

    elif shape == "Sine":
        ph = math.radians(float(p.get("phase_deg", 0.0)))
        for i in range(n):
            y[i] = math.sin(2 * math.pi * i / n + ph)

    elif shape == "Square":
        duty = float(p.get("duty", 50.0)) / 100.0
        for i in range(n):
            y[i] = 1.0 if (i / n) < duty else -1.0

    elif shape == "Triangle":
        sym = min(0.999, max(0.001, float(p.get("symmetry", 50.0)) / 100.0))
        for i in range(n):
            f = i / n
            y[i] = (f / sym) if f < sym else (1.0 - (f - sym) / (1.0 - sym))
        y = [2 * v - 1 for v in y]

    elif shape == "Sawtooth":
        for i in range(n):
            y[i] = 2.0 * (i / n) - 1.0

    elif shape == "Pulse":
        duty = float(p.get("duty", 10.0)) / 100.0
        edge = max(0, int(float(p.get("edge_ns", 0.0)) * 1e-9 / dt_s))
        hi = int(n * duty)
        for i in range(n):
            if i < edge and edge:
                y[i] = i / edge
            elif i < hi - edge:
                y[i] = 1.0
            elif i < hi and edge:
                y[i] = max(0.0, (hi - i) / edge)
            else:
                y[i] = 0.0

    elif shape == "Sinc":
        lobes = float(p.get("lobes", 6.0))
        for i in range(n):
            x = (2.0 * i / n - 1.0) * lobes * math.pi
            y[i] = 1.0 if abs(x) < 1e-9 else math.sin(x) / x

    elif shape == "DC":
        y = [1.0] * n

    elif shape == "Noise":
        import random
        rnd = random.Random(int(p.get("seed", 12345)))
        y = [rnd.uniform(-1.0, 1.0) for _ in range(n)]

    m = max(abs(v) for v in y) or 1.0
    sign = -1.0 if positive_going else 1.0     # output is inverted wrt array
    return [max(-DAC_CLAMP, min(DAC_CLAMP, int(round(sign * peak * v / m))))
            for v in y]


def plan_rate(target_hz, cps=None, prefer_fine=True):
    """Pick (ClockPerStep, DataLen) for a target repetition rate.

    DataLen must be a multiple of 16 and <= MAX_DATALEN, so
        CPS >= FREQ_CLK / (rate * MAX_DATALEN)
    A smaller CPS gives finer time resolution; a larger one allows lower rates.
    """
    if target_hz <= 0:
        raise ValueError("rate must be positive")
    cps_min = max(1, math.ceil(FREQ_CLK / (target_hz * MAX_DATALEN)))
    if cps is None:
        cps = cps_min if prefer_fine else max(cps_min, 31)
    cps = max(cps_min, int(cps))
    dlen = int(round(FREQ_CLK / (target_hz * cps) / 16)) * 16
    dlen = max(16, min(dlen, MAX_DATALEN))
    actual = FREQ_CLK / (dlen * cps)
    return cps, dlen, actual, cps_min


# -------------------------------------------------------------------- device --
class AWGDevice:
    def __init__(self):
        self.lib = ctypes.CDLL("libusb-1.0.so.0")
        self.lib.libusb_open_device_with_vid_pid.restype = ctypes.c_void_p
        self.ctx = ctypes.c_void_p()
        self.dev = None

    # ---- lifecycle ----
    def present(self):
        """Return 'run', 'boot' or None without claiming the device."""
        import subprocess
        try:
            out = subprocess.run(["lsusb"], capture_output=True, text=True,
                                 timeout=5).stdout
        except Exception:
            return None
        if "21e1:000e" in out:
            return "run"
        if "21e1:000d" in out:
            return "boot"
        return None

    def open(self):
        if self.lib.libusb_init(ctypes.byref(self.ctx)) != 0:
            raise RuntimeError("libusb_init failed")
        h = self.lib.libusb_open_device_with_vid_pid(self.ctx, VID, PID)
        if not h:
            raise RuntimeError(
                "DT5810B not found at 21e1:000e. If it is at 000d, run "
                "linux/fx3_firmware_loader.py first.")
        self.dev = ctypes.c_void_p(h)
        self.lib.libusb_set_auto_detach_kernel_driver(self.dev, 1)
        self.lib.libusb_claim_interface(self.dev, 0)
        self.lib.libusb_clear_halt(self.dev, EP_OUT)
        self.lib.libusb_clear_halt(self.dev, EP_IN)
        return self

    def close(self):
        if self.dev:
            try:
                self.lib.libusb_release_interface(self.dev, 0)
                self.lib.libusb_close(self.dev)
            except Exception:
                pass
            self.dev = None
        if self.ctx:
            self.lib.libusb_exit(self.ctx)
            self.ctx = ctypes.c_void_p()

    @property
    def is_open(self):
        return self.dev is not None

    # ---- primitives ----
    def wr(self, addr, value):
        pkt = bytes([0xf1, 0xff, 0xba, 0xab]) \
            + struct.pack('<I', addr & 0xFFFFFFFF) \
            + struct.pack('<I', 1) + struct.pack('<I', value & 0xFFFFFFFF)
        buf = (ctypes.c_uint8 * 16)(*pkt)
        a = ctypes.c_int(0)
        self.lib.libusb_bulk_transfer(self.dev, EP_OUT, buf, 16,
                                      ctypes.byref(a), 2000)

    def wr_block(self, addr, words):
        hdr = bytes([0xf1, 0xff, 0xba, 0xab]) \
            + struct.pack('<I', addr & 0xFFFFFFFF) \
            + struct.pack('<I', len(words))
        body = b''.join(struct.pack('<I', w & 0xFFFFFFFF) for w in words)
        pkt = hdr + body
        buf = (ctypes.c_uint8 * len(pkt))(*pkt)
        a = ctypes.c_int(0)
        self.lib.libusb_bulk_transfer(self.dev, EP_OUT, buf, len(pkt),
                                      ctypes.byref(a), 4000)

    # ---- bringup ----
    def bringup(self, progress=None):
        def say(s):
            if progress:
                progress(s)
        say("resetting FPGA")
        for _ in range(3):
            self.wr(0xFFFFFFFE, 0)
        self.wr(0xF0000035, 0)
        time.sleep(0.1)
        self.wr(0xF0000037, 0)
        self.wr(0xF0000003, 0)
        LUT = [(0xF0000002, 0x2000), (0xF0000003, 0x2040), (0xF0000004, 0x2020),
               (0xF0000005, 0x0060), (0xF0000006, 0x5010), (0xF0000007, 0xC050),
               (0xF0000008, 0x1730), (0xF0000009, 0xCC70), (0xF000000A, 0x5908),
               (0xF000000B, 0xD148), (0xF000000C, 0x0028), (0xF000000D, 0x8268)]
        say("programming clock synthesiser")
        for _ in range(2):
            for a, v in LUT:
                self.wr(a, v)
            self.wr(0xF0000001, 1)
            self.wr(0xF0000001, 0)
            time.sleep(0.2)
        self.wr(0xF0000036, 0); self.wr(0xF0000036, 1); self.wr(0xF0000036, 0)
        self.wr(0xF0000034, 0)
        time.sleep(0.5)
        for ch in (0, 1):
            base = ch * 0x40
            for o in (0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xAC, 0xAD, 0xD0, 0xD1, 0xD2):
                self.wr(0xF0000000 + base + o, 0)
            self.wr(0xF00000BC + base, 100)
        self.wr(0xF00000C3, 0xF1CA)
        time.sleep(0.3)
        say("bringup complete")
        return self

    # ---- AWG upload ----
    def program(self, pts, cps, ch=0, progress=None):
        """Upload the sample array to DDR and start playback."""
        dlen = (len(pts) // 16) * 16
        if dlen < 16:
            pts = list(pts) + [0] * (16 - len(pts))
            dlen = 16
        pts = list(pts[:dlen])

        self.wr(0xfa00100a, 1)              # ChannelMode = AWG
        self.wr(0xfa001001, 0)
        time.sleep(0.01)

        spc = 0x800
        nch = math.ceil(dlen / spc)
        for ci in range(nch):
            if progress and (ci % 8 == 0 or ci == nch - 1):
                progress(f"uploading {ci+1}/{nch}")
            self.wr(0xfa001000, ci * 0x400)
            nw = 0x400 if ci < nch - 1 else (dlen - (nch - 1) * spc) // 2
            words = [0] * 0x400
            for k in range(nw):
                s0 = pts[ci * spc + 2 * k] & 0xFFFF
                s1 = pts[ci * spc + 2 * k + 1] & 0xFFFF
                words[k] = (s1 << 16) | s0
            self.wr_block(0xfa000000, words)
            self.wr(0xfa001001, 1)
            self.wr(0xfa001001, 0)
            self.wr(0xfa00100c, 0)

        self.wr(0xfa001000, 0)
        self.wr(0xfa001003, 0x2000000)
        self.wr(0xfa001002, dlen // 16 - 1)
        self.wr(0xfa001004, 99)
        self.wr(0xfa001005, max(1, int(cps)) - 1)   # ClockPerStep - 1
        self.wr(0xfa001001, 8)                      # enable playback
        self.run(True, ch)
        return dlen

    def run(self, on=True, ch=0):
        if on:
            self.wr(reg(ch, 0x01c00006), 0)
            self.wr(reg(ch, 0x01c00003), 1)
            self.wr(reg(ch, 0x01c00006), 1)
        else:
            self.wr(reg(ch, 0x01c00006), 0)
