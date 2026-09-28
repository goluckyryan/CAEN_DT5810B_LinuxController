"""DT5810B Digital RC pulse generator - clean implementation from the manual.

Written 2026-09-22 for New_attemp_20260922. Does NOT inherit the assumptions in
linux/dt5810.py; see ../docs/FINDINGS.md for what was wrong with that one.

Datapath (manual UM5312 rev5 sec 10 "Shape Datapath", sec 12 "Signal Shape"):

    ChannelMode = Pulser            0xFA00100A = 0
    Shape       = Digital RC        0x300010 = 0   <- TR=1 would select PULSED
                                                      RESET, the staircase
                                                      integrator (sec 10). That
                                                      is the long-standing
                                                      "triangle" bug.
    Energy      = Fixed             0x020f0004 = 0, amplitude in 0x020f0005 (x2)
    Timebase    = Constant rate     0x0100000a = 0, period in 0x01000009

Digital RC is two cascaded first-order IIRs: the first sets the fall time, the
second the rise time with a pole at rise_time/0.35 (sec 10 "Digital RC").
Coefficients come from drc_coeffs.compute_drc, which was verified line-by-line
against the DLL disassembly and independently sanity-checked here: fall=50 us
yields a=0.99996741 -> 1/(1-a)=30680 samples -> 49.1 us at 625 MS/s.

Manual limits honoured:
  * fall and rise time must be >= 20 ns (sec 10 note; sec 12 says 16 ns)
  * constant rate range 1e-2 cps .. 5 Mcps (sec 12 "Channel Timebase")
"""
import ctypes, math, struct, time

VID, PID = 0x21e1, 0x000e
EP_OUT, EP_IN = 0x02, 0x81

# --- clocks -----------------------------------------------------------------
# CLOCK_QUARTER is the value the DLL's coefficient routine uses (1.25 GHz / 4).
CLOCK_QUARTER = 312.5e6
# DRC filter runs at half the 1.25 GHz DAC clock (sec 10: "the DAC to operate at
# half of its operating frequency").
DRC_CLOCK_HZ = 625e6
# Timebase counter clock. NOT independently confirmed -- derived from the old
# library hardcoding period=3,124,999 while the board ran at the long-observed
# ~318-320 Hz:  320 Hz x 3,125,000 = 1.000 GHz. Matches the manual's repeated
# "system clock is equal to 1GHz". Override if a measurement says otherwise.
TIMEBASE_CLOCK_HZ = 1.0e9

MARKERS = (0xFFABBAFF, 0xFFFFABBA, 0xABBAFFFF, 0xBAFFFFAB)


def reg(ch, off):
    return (ch << 28) | off


class DT5810DRC:
    def __init__(self, timebase_clock_hz=TIMEBASE_CLOCK_HZ):
        self.lib = ctypes.CDLL("libusb-1.0.so.0")
        self.lib.libusb_open_device_with_vid_pid.restype = ctypes.c_void_p
        self.ctx = ctypes.c_void_p()
        self.dev = None
        self.timebase_clock_hz = timebase_clock_hz

    # ---------------- USB ----------------
    def open(self):
        if self.lib.libusb_init(ctypes.byref(self.ctx)) != 0:
            raise RuntimeError("libusb_init failed")
        h = self.lib.libusb_open_device_with_vid_pid(self.ctx, VID, PID)
        if not h:
            raise RuntimeError("DT5810B not found at 21e1:000e "
                               "(if at 000d run fx3_firmware_loader.py first)")
        self.dev = ctypes.c_void_p(h)
        self.lib.libusb_set_auto_detach_kernel_driver(self.dev, 1)
        self.lib.libusb_claim_interface(self.dev, 0)
        self.lib.libusb_clear_halt(self.dev, EP_OUT)
        self.lib.libusb_clear_halt(self.dev, EP_IN)
        return self

    def close(self):
        if self.dev:
            self.lib.libusb_release_interface(self.dev, 0)
            self.lib.libusb_close(self.dev)
            self.dev = None
        if self.ctx:
            self.lib.libusb_exit(self.ctx)
            self.ctx = ctypes.c_void_p()

    def wr(self, addr, value):
        pkt = bytes([0xf1, 0xff, 0xba, 0xab]) + struct.pack('<I', addr & 0xFFFFFFFF) \
              + struct.pack('<I', 1) + struct.pack('<I', value & 0xFFFFFFFF)
        buf = (ctypes.c_uint8 * 16)(*pkt)
        a = ctypes.c_int(0)
        self.lib.libusb_bulk_transfer(self.dev, EP_OUT, buf, 16, ctypes.byref(a), 2000)

    def _drain(self):
        ib = (ctypes.c_uint8 * 512)()
        a = ctypes.c_int(0)
        for _ in range(8):
            r = self.lib.libusb_bulk_transfer(self.dev, EP_IN, ib, 512, ctypes.byref(a), 40)
            if r != 0 or a.value == 0:
                break

    def rd(self, addr, n=1):
        """Correct framing: address VERBATIM, count field = N exactly.

        linux/dt5810.py sends addr-1 and count+2; both are wrong (FINDINGS B1).
        Note most config space is write-only and returns the FF FF BA AB filler.
        """
        self._drain()
        pkt = bytes([0xf0, 0xff, 0xba, 0xab]) + struct.pack('<I', addr & 0xFFFFFFFF) \
              + struct.pack('<I', n)
        buf = (ctypes.c_uint8 * 12)(*pkt)
        a = ctypes.c_int(0)
        self.lib.libusb_bulk_transfer(self.dev, EP_OUT, buf, 12, ctypes.byref(a), 1000)
        time.sleep(0.04)
        rb = (ctypes.c_uint8 * 2048)()
        ra = ctypes.c_int(0)
        self.lib.libusb_bulk_transfer(self.dev, EP_IN, rb, 2048, ctypes.byref(ra), 1500)
        raw = bytes(rb[:ra.value])
        return [struct.unpack_from('<I', raw, i)[0] for i in range(0, len(raw) - 3, 4)]

    def _strobe(self, addr):
        self.wr(addr, 1)
        self.wr(addr, 0)

    # ---------------- bringup ----------------
    def bringup(self):
        """FPGA clock-synth + base config. Same sequence as the verified
        linux/dt5810.py bringup (this part was never in question)."""
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
        return self

    # ---------------- period / rate ----------------
    def period_for_rate(self, rate_hz):
        """Constant-rate period register value (0x01000009)."""
        if not (1e-2 <= rate_hz <= 5e6):
            raise ValueError(f"rate {rate_hz} outside manual range 1e-2 .. 5e6 cps")
        p = int(round(self.timebase_clock_hz / rate_hz)) - 1
        return max(1, p)

    def rate_for_period(self, period):
        return self.timebase_clock_hz / (period + 1)

    # ---------------- the pulse ----------------
    def set_drc_pulse(self, rate_hz, rise_us, decay_us,
                      energy=4000, gain=1107, offset=0, invert=1, ch=0,
                      run=True):
        """Configure a Digital RC exponential pulse.

        rate_hz   repetition rate, constant-rate timebase
        rise_us   rise time (>= 0.02 us per manual)
        decay_us  decay time constant tau (>= 0.02 us per manual)
        energy    Fixed-energy amplitude code; written x2 to 0x020f0005
        gain      digital gain 0x0f000001 (final multiplier on the whole chain)
        """
        from drc_coeffs import compute_drc

        rise_ns = int(round(rise_us * 1000.0))
        fall_ns = int(round(decay_us * 1000.0))
        if fall_ns < 20 or (rise_ns and rise_ns < 20):
            raise ValueError("manual sec 10: do not use Digital RC below 20 ns")

        r = compute_drc(rise_ns=rise_ns, fall_ns=fall_ns,
                        clock_quarter=CLOCK_QUARTER)
        period = self.period_for_rate(rate_hz)

        # --- LFSR reprogram strobes ---
        for off in (0x020f0002, 0x0100004, 0x1400009, 0x1900003):
            self._strobe(reg(ch, off))

        # --- channel / analog stage ---
        self.wr(reg(ch, 0x0f000035), 0)
        self.wr(reg(ch, 0x0f000002), 1)                          # enable
        self.wr(reg(ch, 0x0f000001), int(gain) & 0xFFFFFFFF)     # digital gain
        self.wr(reg(ch, 0x0f000000), int(offset) & 0xFFFFFFFF)   # digital offset
        self.wr(reg(ch, 0x0f000004), 1 if invert else 0)
        self.wr(0xFA00100A, 0)                                   # ChannelMode=Pulser

        # --- energy: Fixed, amplitude = LSB x 2 ---
        self.wr(reg(ch, 0x020f0004), 0)
        self.wr(reg(ch, 0x020f0005), int(energy) * 2)

        # --- timebase: constant rate ---
        self.wr(reg(ch, 0x0100000a), 0)
        self.wr(reg(ch, 0x01000009), period)
        self.wr(reg(ch, 0x01000007), 0)
        self.wr(reg(ch, 0x01000008), 0)
        self.wr(reg(ch, 0x0f000005), 0)
        self.wr(reg(ch, 0x0f000006), 0)

        # --- analog mux: range/invert/filter ---
        self.wr(0xF00000C2, 3 | ((1 if invert else 0) << 2) | (1 << 3))

        # --- Digital RC ---
        self.wr(reg(ch, 0x00300010), 0)              # TR OFF -> not pulsed reset
        self.wr(reg(ch, 0x0300000d), r['rise_presc_disable'])
        for i, v in enumerate(r['coeffs_reg']):
            self.wr(reg(ch, 0x03000000 + i), v)
        self._strobe(reg(ch, 0x0300000a))            # latch coefficients
        self.wr(reg(ch, 0x0300000c), 0)
        self.wr(reg(ch, 0x0300000b), 0xFFFFFFFF)     # IENABLE = -1
        self.wr(reg(ch, 0x00300014), r['prescaler_reg_0x300014'])
        # 0x300012 / 0x300013 deliberately NOT written: pulsed-reset only.

        # --- run control ---
        self.wr(reg(ch, 0x01c00005), 0)
        self.wr(reg(ch, 0x01c00000), 0)
        self.wr(reg(ch, 0x01c00006), 0)
        self.wr(reg(ch, 0x01c00004), 1)
        self.wr(reg(ch, 0x01c00003), 1)
        if run:
            self.wr(reg(ch, 0x01c00006), 1)

        tau_fall_us = r['fall_shifted'] * r['K'] / DRC_CLOCK_HZ * 1e6
        tau_rise_us = r['rise_pole_idx'] * r['K'] / DRC_CLOCK_HZ * 1e6
        return {
            'rate_hz': rate_hz, 'period_reg': period,
            'rate_implied_hz': self.rate_for_period(period),
            'rise_us': rise_us, 'decay_us': decay_us,
            'energy': energy, 'gain': gain, 'offset': offset,
            'prescaler': r['presc'],
            'a_decay': r['a_decay'], 'b_rise': r['b_rise'],
            'tau_fall_model_us': tau_fall_us,
            'tau_rise_pole_us': tau_rise_us,
            'rise_10_90_model_us': 2.197 * tau_rise_us,
        }

    def run_enable(self, on=True, ch=0):
        self.wr(reg(ch, 0x01c00006), 1 if on else 0)
