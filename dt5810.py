"""DT5810B USB driver -- the low-level layer this repository is built on.

Brought into the repo 2026-09-28 so the emulator control software is
self-contained; it previously lived only in ~/caen_signal_emulator/linux/ and
was reached by a hardcoded sys.path. That copy still exists and is still used by
dt5810_gui.py and dt5810_mcp.py, so it was copied rather than moved.

One deliberate difference from that copy: rd() sends the address verbatim with
count = N, instead of (addr-1) and (count+2). See rd() and README section 4.

USB 21e1:000e (21e1:000d is the FX3 bootloader -- run fx3_firmware_loader.py
first after a cold power-up, the firmware is volatile).

    write : f1 ff ba ab | addr(LE) | count(LE) | value(LE)
    read  : f0 ff ba ab | addr(LE) | N(LE)      -> N words back on EP 0x81

Config space is WRITE-ONLY: every config register reads back as the repeating
FF FF BA AB filler, so verification has to happen on the analog output.
"""
import ctypes, struct, time, math

VID, PID = 0x21e1, 0x000e
EP_OUT, EP_IN = 0x02, 0x81

# ---- calibrations (1 MOhm scope load; see caen.md) ----
GAIN_SLOPE   = 8.278e-4     # Vpp ~= GAIN_SLOPE*gain + GAIN_OFFS
GAIN_OFFS    = 0.0837
GAIN_MAX     = 1800         # verified linear region
OFFSET_NULL_K= -6.14e7      # baseline-null offset ~= OFFSET_NULL_K / gain
FREQ_CLK     = 312.5e6      # AWG sample clock (1.25GHz/4)
FCLK         = 1.25e9

def _reg(ch, off): return (ch << 28) | off
def volts_to_gain(v):
    return max(1, min(int(round((v - GAIN_OFFS)/GAIN_SLOPE)), GAIN_MAX))
def gain_to_offset_null(gain):
    return int(round(OFFSET_NULL_K / gain))

# offset register volt-authority is ~proportional to gain (same DAC).
# Calibrated near gain~1120: ~0.467 mV baseline per offset-LSB at that gain.
# So volts_per_lsb(gain) ~= OFFSET_V_PER_LSB_REF * (gain / 1120).
OFFSET_V_PER_LSB_REF = 4.67e-4
def offset_for(gain, offset_v=0.0):
    """Offset register for a target baseline = offset_v (0 => null baseline)."""
    null = OFFSET_NULL_K / gain
    vpl = OFFSET_V_PER_LSB_REF * (gain / 1120.0)
    delta = offset_v / vpl if vpl else 0.0
    return int(round(null + delta))

# ---- host-side AWG waveform math (ports of Calculate*, from awg_wave.py) ----
MEMLIMIT = 100000.0          # exe memorylimit (max host array len before raising ClockPerStep)
DAC_MAX  = 2**15 - 1.0

def _clamp(v):
    if v > DAC_MAX: return int(round(DAC_MAX))
    if v < -DAC_MAX: return int(round(-DAC_MAX))
    return int(round(v))

def _timescale_for(freq):
    ts = math.ceil((2.0*math.pi*FREQ_CLK/freq) / (0.99*MEMLIMIT))
    return (ts, freq*ts) if ts > 1 else (1, freq)

def _calc_sine(amp, offset, freq, phase=0.0):
    ts, freq = _timescale_for(freq)
    num2 = FREQ_CLK/freq
    ph = phase/360.0*2*math.pi
    nper = int(MEMLIMIT/num2)
    target = math.sin(ph)
    best = 1e30; nbest = max(2, int(round(num2)))
    for i in range(1, max(2, nper)+1):
        for n in (math.floor(num2*i), math.ceil(num2*i)):
            dd = abs(math.sin(2*math.pi/FREQ_CLK*n*freq+ph) - target)
            if dd < best: best = dd; nbest = int(n)
    pts = [_clamp(amp*math.sin(2*math.pi/FREQ_CLK*k*freq+ph)+offset) for k in range(nbest)]
    return pts, ts

def _calc_square(amp, offset, freq, phase=0.0, duty=50.0):
    ts, freq = _timescale_for(freq)
    per = FREQ_CLK/freq; nper = max(2, int(round(per)))
    ph = phase/360.0; hi = duty/100.0
    return [_clamp((amp if ((k/per+ph)%1.0) < hi else -amp)+offset) for k in range(nper)], ts

def _calc_saw(amp, offset, freq, phase=0.0, dc=0.0):
    ts, freq = _timescale_for(freq)
    per = FREQ_CLK/freq; nper = max(2, int(round(per)))
    ph = phase/360.0
    return [_clamp((((k/per+ph)%1.0)*2.0-1.0)*amp+offset) for k in range(nper)], ts

def _calc_tri(amp, offset, freq, phase=0.0, symmetry=50.0):
    ts, freq = _timescale_for(freq)
    per = FREQ_CLK/freq; nper = max(2, int(round(per)))
    ph = phase/360.0; pk = min(max(symmetry/100.0, 1e-3), 1-1e-3)
    pts = []
    for k in range(nper):
        frac = (k/per+ph)%1.0
        v = (-amp + 2*amp*(frac/pk)) if frac < pk else (amp - 2*amp*((frac-pk)/(1-pk)))
        pts.append(_clamp(v+offset))
    return pts, ts

def _calc_pulse(amp, offset, freq, phase=0.0, duty=50.0, rise=0.0, fall=0.0):
    ts, freq = _timescale_for(freq)
    per = FREQ_CLK/freq; nper = max(2, int(round(per)))
    ph = phase/360.0
    rs = max(0.0, rise/100.0)*per; fl = max(0.0, fall/100.0)*per; hi = duty/100.0*per
    pts = []
    for k in range(nper):
        x = (k - ph*per) % per
        if x < rs and rs > 0:        v = -amp + 2*amp*(x/rs)
        elif x < rs+hi:              v = amp
        elif x < rs+hi+fl and fl>0:  v = amp - 2*amp*((x-rs-hi)/fl)
        else:                        v = -amp
        pts.append(_clamp(v+offset))
    return pts, ts

def _calc_sinc(amp, offset, freq, phase=0.0, dc=0.0):
    ts, freq = _timescale_for(freq)
    per = FREQ_CLK/freq; nper = max(2, int(round(per)))
    pts = []
    for k in range(nper):
        x = (k/per - 0.5)*2*math.pi*5
        v = amp*(math.sin(x)/x if abs(x) > 1e-9 else 1.0)
        pts.append(_clamp(v+offset))
    return pts, ts

def _calc_dc(amp, offset, freq, *a):
    return [_clamp(offset)]*100, 1

def _calc_noise(amp, offset, freq, *a):
    import random
    return [_clamp(random.uniform(-amp, amp)+offset) for _ in range(4096)], 1


class DT5810:
    def __init__(self):
        self.lib = ctypes.CDLL("libusb-1.0.so.0")
        # Declare the pointer RETURN type so the 64-bit device handle is not
        # truncated to a 32-bit int by ctypes' default int return. We keep the
        # handle as a ctypes.c_void_p object (passed positionally elsewhere) so
        # argtypes can stay at their defaults (verified-working buffer binding).
        self.lib.libusb_open_device_with_vid_pid.restype = ctypes.c_void_p
        self.ctx = ctypes.c_void_p()
        self.dev = None

    # ---- USB primitives ----
    def open(self):
        if self.lib.libusb_init(ctypes.byref(self.ctx)) != 0:
            raise RuntimeError("libusb_init failed")
        h = self.lib.libusb_open_device_with_vid_pid(self.ctx, VID, PID)
        if not h:
            raise RuntimeError("DT5810B not found (VID 21e1 PID 000e). "
                               "If at PID 000d, run fx3_firmware_loader.py first.")
        self.dev = ctypes.c_void_p(h)   # full 64-bit handle (restype set above)
        self.lib.libusb_set_auto_detach_kernel_driver(self.dev, 1)
        self.lib.libusb_claim_interface(self.dev, 0)
        # Recover any halted/wedged endpoints (e.g. after a prior process was killed
        # mid bulk-transfer). Harmless on a healthy device.
        self.lib.libusb_clear_halt(self.dev, EP_OUT)
        self.lib.libusb_clear_halt(self.dev, EP_IN)
        return self

    def close(self):
        if self.dev:
            self.lib.libusb_release_interface(self.dev, 0)
            self.lib.libusb_close(self.dev)
            self.dev = None
        if self.ctx:
            self.lib.libusb_exit(self.ctx); self.ctx = ctypes.c_void_p()

    def wr(self, addr, value):
        pkt = bytes([0xf1,0xff,0xba,0xab]) + struct.pack('<I', addr & 0xFFFFFFFF) \
              + struct.pack('<I', 1) + struct.pack('<I', value & 0xFFFFFFFF)
        buf = (ctypes.c_uint8*16)(*pkt); act = ctypes.c_int(0)
        self.lib.libusb_bulk_transfer(self.dev, EP_OUT, buf, 16, ctypes.byref(act), 2000)

    def wr_block(self, addr, words):
        hdr = bytes([0xf1,0xff,0xba,0xab]) + struct.pack('<I', addr & 0xFFFFFFFF) \
              + struct.pack('<I', len(words))
        body = b''.join(struct.pack('<I', w & 0xFFFFFFFF) for w in words)
        pkt = hdr + body; buf = (ctypes.c_uint8*len(pkt))(*pkt); act = ctypes.c_int(0)
        self.lib.libusb_bulk_transfer(self.dev, EP_OUT, buf, len(pkt), ctypes.byref(act), 4000)

    def _drain_in(self):
        """Flush any stale data on the IN endpoint (prevents read desync across calls)."""
        ib=(ctypes.c_uint8*512)(); a=ctypes.c_int(0)
        for _ in range(8):
            r=self.lib.libusb_bulk_transfer(self.dev, EP_IN, ib, 512, ctypes.byref(a), 40)
            if r != 0 or a.value == 0:
                break

    def rd(self, addr, count=1):
        self._drain_in()
        # Address VERBATIM, count = N exactly. The copy in ../linux/dt5810.py
        # sends (addr-1) and (count+2); both are wrong -- proven by reading
        # 0xFFFF0000 both ways and watching the response shift by one word
        # (README section 4). The "reads are flaky, retry 4x" logic in board_id()
        # is a symptom of that framing, not a trait of the device.
        pkt = bytes([0xf0,0xff,0xba,0xab]) + struct.pack('<I', addr & 0xFFFFFFFF) + struct.pack('<I', count)
        buf=(ctypes.c_uint8*12)(*pkt); a=ctypes.c_int(0)
        self.lib.libusb_bulk_transfer(self.dev, EP_OUT, buf, 12, ctypes.byref(a), 1000); time.sleep(0.05)
        rb=(ctypes.c_uint8*512)(); ra=ctypes.c_int(0)
        self.lib.libusb_bulk_transfer(self.dev, EP_IN, rb, 512, ctypes.byref(ra), 1500)
        return bytes(rb[:ra.value])

    def _strobe(self, addr):
        self.wr(addr, 1); self.wr(addr, 0)

    # ---- FPGA bringup (clock-synth LUT + base config) ----
    def bringup(self):
        for _ in range(3): self.wr(0xFFFFFFFE, 0)
        self.wr(0xFFFF0000, 0xFF); self.rd(0xFFFF0000, 3)
        self.wr(0xF0000035, 0); time.sleep(0.1)
        self.wr(0xF0000037, 0); self.wr(0xF0000003, 0)
        LUT = [(0xF0000002,0x2000),(0xF0000003,0x2040),(0xF0000004,0x2020),(0xF0000005,0x0060),
               (0xF0000006,0x5010),(0xF0000007,0xC050),(0xF0000008,0x1730),(0xF0000009,0xCC70),
               (0xF000000A,0x5908),(0xF000000B,0xD148),(0xF000000C,0x0028),(0xF000000D,0x8268)]
        for a,v in LUT: self.wr(a,v)
        self.wr(0xF0000001,1); self.wr(0xF0000001,0); time.sleep(0.2)
        for a,v in LUT: self.wr(a,v)
        self.wr(0xF0000001,1); self.wr(0xF0000001,0); time.sleep(0.2)
        self.wr(0xF0000036,0); self.wr(0xF0000036,1); self.wr(0xF0000036,0); self.wr(0xF0000034,0)
        time.sleep(0.5)
        for ch in (0,1):
            base = ch*0x40
            for o in [0xA0,0xA1,0xA2,0xA3,0xA4,0xAC,0xAD,0xD0,0xD1,0xD2]: self.wr(0xF0000000+base+o,0)
            self.wr(0xF00000BC+base,100)
        self.wr(0xF00000C3,0xF1CA); time.sleep(0.3)
        # per-channel base enable + LFSR reprogram strobes (CH0)
        CH=0
        for off in [0x20f002,0x0100004,0x1400009,0x1900003]: self._strobe(_reg(CH,off))
        self.wr(_reg(CH,0x0f000035),0); self.wr(_reg(CH,0x0f000002),1)
        self.wr(_reg(CH,0x0f000004),1)   # invert -> upward pulse
        self.wr(0xFA00100A,0)             # Pulser/shape-RAM mode by default
        self.wr(_reg(CH,0x20f004),0); self.wr(_reg(CH,0x20f005),8000)
        self.wr(0xF00000C2,0xF)           # analog mux: output enabled
        return self

    def board_id(self):
        # Clean board_id() string. Raw read is noisy (0xffffabba 'no-data' markers);
        # scan the stream for the real model word 0x1005810B and report it readably.
        for _ in range(4):                      # retry (read is flaky)
            self.wr(0xFFFF0000, 0xFF)
            d = self.rd(0xFFFF0000, 3)
            for o in range(0, max(0, len(d)-3)):
                w = struct.unpack_from('<I', d, o)[0]
                if (w & 0xFFFFF) == 0x5810B:    # model code, any byte alignment
                    return 'DT5810B (0x%08X)' % w
            time.sleep(0.05)
        return 'DT5810B'

    @staticmethod
    def _pack_shape(points, length):
        arr=[0]*(length+2)
        for i in range(0, length-1-4+1):
            v=points[i]
            v = -32575 if v<-32575 else (32575 if v>32575 else v)
            arr[i+4]=int(round(v))
        arr[0]=arr[1]=arr[2]=arr[3]=0
        return arr[:length]

    # ---- SHAPE-RAM detector pulse (triggered, rate fixed ~318 Hz) ----
    def set_detector_pulse(self, width_us=300.0, decay_us=50.0, gain=1107, offset=-55465, invert=1, ch=0):
        """Rise + exp-decay pulse via shape generator. width_us = total pulse width
        (19..1600 us), decay_us = exp decay time constant, amplitude_v = peak volts.
        Repetition rate is a fixed firmware default (~318 Hz) in this mode."""
        LEN=4000
        interp = max(1, int(round(width_us/3.185)))      # ~3.185 us per interp unit
        # array sample spans width/4096 in real time -> tau_samples
        tau_samp = max(2.0, decay_us * 4096.0 / width_us)
        pts=[0.0]*4096
        for n in range(4,4096): pts[n]=32767.0*math.exp(-(n-4)/tau_samp)
        pa=self._pack_shape(pts,LEN)
        words=[0]*0x800
        for i in range(LEN>>3): words[i+2]=((pa[8*i+4]&0xFFFF)<<16)|(pa[8*i]&0xFFFF)
        for sid in range(16):
            b=((ch<<8)|sid)<<20
            self.wr(b+0x50f000,1); self.wr(b+0x50f001,0)
            iv6=0x10000//(interp+1)
            self.wr(b+0x50f007,((0x10000//3)<<16)|2)
            self.wr(b+0x50f006,(iv6<<16)|interp)
            self.wr(b+0x50f004,3); self.wr(b+0x50f005,0)
            self.wr(b+0x50f002,1); self.wr_block(b+0x500000,words)
            self.wr(b+0x500000,words[0]); self.wr(b+0x500001,words[1])
            self.wr(b+0x50f003,(LEN>>3)+3); self.wr(b+0x50f002,0)
        gain=int(gain); off=int(offset)
        self.wr(_reg(ch,0x20f004),0); self.wr(_reg(ch,0x20f005),8000)
        self.wr(_reg(ch,0x0f000000),off & 0xFFFFFFFF)
        self.wr(_reg(ch,0x0f000001),gain & 0xFFFFFFFF)
        self.wr(_reg(ch,0x0f000004), 1 if invert else 0)   # output polarity
        self.wr(_reg(ch,0x0f000002),1)
        self.wr(0xFA00100A,0)
        self.wr(_reg(ch,0x01c00006),0); self.wr(_reg(ch,0x01c00004),1)
        self.wr(_reg(ch,0x01c00003),1); self.wr(_reg(ch,0x01c00006),1)
        return {"mode":"detector","width_us":width_us,"decay_us":decay_us,
                "gain":gain,"offset":off,"invert":int(bool(invert)),"interp":interp,
                "tau_samples":round(tau_samp,1),"rate_hz":318}

    # ---- Digital RC (DRC) — two-IIR-filter exponential pulse (manual sec 10) ----
    def _strobe(self, addr):
        self.wr(addr, 1); self.wr(addr, 0)

    def set_drc_pulse(self, rise_ns=0.0, decay_us=50.0, energy=4000, rate_hz=318.0,
                      gain=3056, offset=0, polarity="positive", invert=None, ch=0):
        """Digital RC pulse: rise + exponential decay via the on-FPGA two-IIR-filter
        path (CAEN 'Digital RC'). Exponential-only, no tail-length/rate limit.
            rise_ns  -> rise time (ns); 0 -> ~1 ns (DAC limit). 2nd IIR pole rise/0.35.
            decay_us -> decay time constant tau (us); 1st IIR pole. Manual: don't use <20 ns.
            energy   -> event amplitude code (Fixed-energy mode)
            rate_hz  -> repetition rate (timebase, constant rate)
            gain     -> digital gain code (0x0f000001); offset -> baseline (0x0f000000)
            polarity -> "positive" (rise-up + decay-down) or "negative" (rise-down).
                        Controlled by reg 0xF00000C2 bit2. (invert=1/0 also accepted.)
        Coefficients computed by drc_coeffs.compute_drc (reverse-engineered FUN_10005340)."""
        from drc_coeffs import compute_drc
        if invert is None:
            invert = 1 if str(polarity).lower().startswith("pos") else 0
        invert = 1 if invert else 0
        CQ = FREQ_CLK            # clock_quarter = 1.25GHz/4 = 312.5e6
        fall_ns = int(round(decay_us*1000.0))
        r = compute_drc(rise_ns=int(round(rise_ns)), fall_ns=fall_ns, clock_quarter=CQ)
        # per-channel pulse setup (assumes bringup already ran)
        for off in (0x20f002, 0x0100004, 0x1400009, 0x1900003):
            self._strobe(_reg(ch, off))
        self.wr(_reg(ch,0x0f000035), 0)
        self.wr(_reg(ch,0x0f000002), 1)                      # channel enable
        self.wr(_reg(ch,0x0f000001), int(gain) & 0xFFFFFFFF) # digital gain
        # original working drc_pulse.py wrote 1 here; DRC polarity is really 0xF00000C2 bit2
        self.wr(_reg(ch,0x0f000004), 1)                      # (legacy; not the DRC polarity)
        self.wr(0xFA00100A, 0)                               # ChannelMode = emulator/pulser
        self.wr(_reg(ch,0x20f004), 0)
        self.wr(_reg(ch,0x20f005), int(energy)*2)          # ENERGY (= LSB x 2), not rate
        self.wr(_reg(ch,0x0f000000), int(offset) & 0xFFFFFFFF)      # baseline
        # Timebase. 0x100009 IS the pulse-rate period:  rate = 312.5e6/(period+1),
        # verified on hardware to -0.0% from 4 kHz to 31 kHz. The old comment here
        # claimed it was an "energy-filter clock" that must stay fixed; that was a
        # consequence of writing it to the wrong address (0x01000009), so it never
        # did anything. See New_attemp_20260922/REGISTER_ADDRESS_BUG.md.
        self.wr(_reg(ch,0x10000a), 0)
        self.wr(_reg(ch,0x100009), max(1, int(round(312.5e6/float(rate_hz))) - 1))
        self.wr(_reg(ch,0x100007), 0); self.wr(_reg(ch,0x100008), 0)
        self.wr(_reg(ch,0x0f000005), 0); self.wr(_reg(ch,0x0f000006), 0)
        # analog mux/range/invert packed reg (FUN_10007650): (range&3) + (invert<<2) + (filter<<3)
        # range=3, filter=1; invert bit2 controls DRC polarity (0xF=invert on, 0xB=off)
        self.wr(0xF00000C2, (3 & 3) | ((1 if invert else 0) << 2) | (1 << 3))
        # DRC IIR config
        self.wr(_reg(ch,0x00300010), 1)                      # TR-enable
        self.wr(_reg(ch,0x0300000d), r['rise_presc_disable'])
        for i, v in enumerate(r['coeffs_reg']):
            self.wr(_reg(ch,0x03000000 + i), v)              # 10 IIR coeffs
        self._strobe(_reg(ch,0x0300000a))
        self.wr(_reg(ch,0x0300000c), 0)
        self.wr(_reg(ch,0x0300000b), 1)                      # enable
        self.wr(_reg(ch,0x00300014), r['prescaler_reg_0x300014'])
        presc_pow = {1:0, 2:1, 4:2, 8:3}[r['presc']]
        self.wr(_reg(ch,0x00300012), (1 << (presc_pow+1)) * int(energy))  # amplitude
        self.wr(_reg(ch,0x00300013), presc_pow)
        # run control
        self.wr(_reg(ch,0x01c00005), 0); self.wr(_reg(ch,0x01c00000), 0)
        self.wr(_reg(ch,0x01c00006), 0); self.wr(_reg(ch,0x01c00004), 1)
        self.wr(_reg(ch,0x01c00003), 1); self.wr(_reg(ch,0x01c00006), 1)
        return {"mode":"drc","rise_ns":int(round(rise_ns)),"decay_us":decay_us,
                "energy":int(energy),"rate_hz":rate_hz,"gain":int(gain),"offset":int(offset),
                "invert":int(bool(invert)),"polarity":"positive" if invert else "negative",
                "prescaler":r['presc'],
                "a_decay":round(r['a_decay'],8),"b_rise":round(r['b_rise'],6)}

    # ---- AWG ProgramDDR upload (ch0, transport==1) — replicated from awg_pulse.py/awg_wave.py ----
    def _program_ddr(self, pts):
        """Upload a signed-16-bit sample array to the AWG DDR region and set DataLen.
        Returns the actual DataLen used (multiple of 16). Reuses the verified
        FUN_10008710 chunk-strobe protocol. Does NOT set ClockPerStep or enable."""
        DLEN = (len(pts)//16)*16
        if DLEN < 16:
            pts = list(pts) + [0]*(16-len(pts)); DLEN = 16
        pts = list(pts[:DLEN])
        self.wr(0xfa00100a, 1)              # ChannelMode = AWG
        self.wr(0xfa001001, 0); time.sleep(0.01)
        spc = 0x800; nch = math.ceil(DLEN/spc)
        for ci in range(nch):
            self.wr(0xfa001000, ci*0x400)
            nw = 0x400 if ci < nch-1 else (DLEN-(nch-1)*spc)//2
            words = [0]*0x400
            for k in range(nw):
                s0 = pts[ci*spc+2*k] & 0xFFFF
                s1 = pts[ci*spc+2*k+1] & 0xFFFF
                words[k] = (s1 << 16) | s0
            self.wr_block(0xfa000000, words)
            self.wr(0xfa001001, 1); self.wr(0xfa001001, 0); self.wr(0xfa00100c, 0)
        self.wr(0xfa001000, 0)
        self.wr(0xfa001003, 0x2000000)
        self.wr(0xfa001002, DLEN//16 - 1)   # DataLen/16 - 1
        self.wr(0xfa001004, 99)
        return DLEN

    def _awg_enable(self, cps, ch=0):
        """Set ClockPerStep and arm/run the AWG channel."""
        self.wr(0xfa001005, max(1, cps) - 1)        # ClockPerStep - 1
        self.wr(0xfa001001, 8)                       # enable playback
        self.wr(_reg(ch,0x01c00006), 0)
        self.wr(_reg(ch,0x01c00003), 1)
        self.wr(_reg(ch,0x01c00006), 1)

    @staticmethod
    def _amp_to_peak_lsb(amplitude_v):
        """Map desired peak volts -> raw DAC peak code. AWG amplitude is baked into
        the array at the device default analog gain, so peak_lsb scales linearly.
        Calibrated @ 50 Ohm scope load (2026-06-29): Vpeak = 8.32e-6*lsb + 0.064,
        so lsb = (V - 0.064)/8.32e-6.  Max output ~0.335 V at full-scale lsb (32575).
        (At 1 MOhm the voltage is ~2x.)"""
        lsb = int(round((amplitude_v - 0.064) / 8.32e-6)) if amplitude_v > 0.064 else 1
        return max(1, min(lsb, 32575))

    # ---- AWG rate-controllable detector-style pulse (rise + exp decay) ----
    MAX_DATALEN = 131072

    def set_awg_pulse(self, clock_per_step=31, data_len=10080, decay_us=50.0,
                      peak_lsb=20000, offset_lsb=0, invert=1, ch=0):
        """Rise+exp-decay pulse via AWG mode (CONTINUOUS playback). The two TRUE hardware
        knobs are exposed directly; dt and rate are derived (read-only):
            clock_per_step -> ClockPerStep (>=1); dt = ClockPerStep * 3.2 ns
            data_len       -> array length (snapped to mult of 16, <=131072); the array
                              loops continuously so period = DataLen * dt, rate = 1/period
            decay_us       -> exp decay time constant of the pulse (rise+decay at array start)
            peak_lsb       -> raw DAC peak code (0..32575); offset_lsb -> baseline code (signed)
        The pulse occupies the start of the array; the remainder is blank baseline."""
        CPS = max(1, int(clock_per_step))
        dt_samp = CPS/FREQ_CLK                 # real seconds per array sample
        DLEN = (int(data_len)//16)*16
        DLEN = max(16, min(DLEN, self.MAX_DATALEN))
        rate_hz = FREQ_CLK/(DLEN*CPS)          # derived repetition rate
        period_us = DLEN*dt_samp*1e6
        decay_samp = max(1.0, decay_us*1e-6/dt_samp)
        peak = max(1, min(int(peak_lsb), 32575))
        off_lsb = int(offset_lsb)
        pts = [off_lsb]*DLEN
        for n in range(2, DLEN):               # pulse fills array start, decays to baseline
            v = peak*math.exp(-(n-2)/decay_samp)
            pts[n] = off_lsb + (int(v) if v > 1 else 0)
        self.wr(_reg(ch,0x0f000004), 1 if invert else 0)   # output polarity
        DLEN = self._program_ddr(pts)
        self._awg_enable(CPS, ch)
        return {"mode":"awg_pulse","rate_hz":round(rate_hz,2),
                "period_us":round(period_us,2),
                "decay_us":decay_us,"peak_lsb":peak,"offset_lsb":off_lsb,"data_len":DLEN,
                "clock_per_step":CPS,"decay_samples":round(decay_samp,1),
                "clock_MHz":FREQ_CLK/1e6,"tick_ns":round(1e9/FREQ_CLK,4),
                "ns_per_sample":round(dt_samp*1e9,2),
                "dt_samp_s":dt_samp,"pts":list(pts[:DLEN])}

    # ---- AWG built-in/arbitrary waveform (sine/square/saw/tri/pulse/sinc/dc/noise) ----
    def set_awg_wave(self, func="sine", freq_hz=1000.0, peak_lsb=20000, offset_lsb=0,
                     invert=1, duty=50.0, symmetry=50.0, rise=0.0, fall=0.0, ch=0):
        """Native built-in/arbitrary waveform generator (CONTINUOUS playback).
        Computes the host sample array (CalculateSin/Square/Saw/TRI/PULSE/SINC ports),
        bakes peak/offset LSB codes into the array, uploads via ProgramDDR, sets
        ClockPerStep from freq, and enables.
            func       in {sine,square,saw,ramp,tri,triangle,pulse,sinc,dc,noise}
            freq_hz    -> ClockPerStep via timescale (output = FREQ_CLK/(DataLen*CPS))
            peak_lsb   -> raw DAC peak code (0..32575); output volts scale with this
            offset_lsb -> raw DC offset code (signed)
            duty (square/pulse), symmetry (tri), rise/fall (pulse) -> shape params"""
        func = func.lower()
        amp = max(1, min(int(peak_lsb), 32575))              # peak LSB
        off = int(offset_lsb)                                 # offset LSB
        if func in ('pulse',):
            pts, ts = _calc_pulse(amp, off, freq_hz, 0.0, duty, rise, fall)
        elif func in ('saw','ramp'):
            pts, ts = _calc_saw(amp, off, freq_hz, 0.0)
        elif func in ('sinc',):
            pts, ts = _calc_sinc(amp, off, freq_hz, 0.0)
        elif func in ('tri','triangle'):
            pts, ts = _calc_tri(amp, off, freq_hz, 0.0, symmetry)
        elif func in ('square','sq'):
            pts, ts = _calc_square(amp, off, freq_hz, 0.0, duty)
        elif func in ('dc',):
            pts, ts = _calc_dc(amp, off, freq_hz)
        elif func in ('noise',):
            pts, ts = _calc_noise(amp, off, freq_hz)
        elif func in ('sine','sin'):
            pts, ts = _calc_sine(amp, off, freq_hz, 0.0)
        else:
            raise ValueError(f"unknown func {func!r}; choose: "
                             "sine square saw ramp tri pulse sinc dc noise")
        DLEN = (len(pts)//16)*16
        if DLEN < 16: DLEN = 16
        actual = FREQ_CLK/(DLEN*ts)
        self.wr(_reg(ch,0x0f000004), 1 if invert else 0)   # output polarity
        DLEN = self._program_ddr(pts)
        self._awg_enable(ts, ch)
        return {"mode":"awg_wave","func":func,"freq_hz_req":freq_hz,
                "freq_hz_actual":round(actual,2),"peak_lsb":amp,
                "offset_lsb":off,"data_len":DLEN,"clock_per_step":ts,
                "clock_MHz":FREQ_CLK/1e6,"tick_ns":round(1e9/FREQ_CLK,4),
                "ns_per_sample":round(ts/FREQ_CLK*1e9,2),
                "dt_samp_s":ts/FREQ_CLK,"pts":list(pts[:DLEN])}

    def run_enable(self, ch=0, on=True):
        self.wr(_reg(ch,0x01c00006), 1 if on else 0)


# ---------------------------------------------------------------------------
if __name__ == '__main__':
    import sys
    # Minimal CLI smoke test (touches the live device). Usage:
    #   python3 dt5810.py                 -> bringup + board_id()
    #   python3 dt5810.py detector        -> set_detector_pulse(300,50,1.0)
    #   python3 dt5810.py awg_pulse       -> set_awg_pulse(1000,50,1.0)
    #   python3 dt5810.py awg_wave sine   -> set_awg_wave('sine',1000,1.0)
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'id'
    dev = DT5810(); dev.open(); dev.bringup()
    print("board_id:", [hex(w) for w in dev.board_id()])
    try:
        if cmd == 'detector':
            print(dev.set_detector_pulse(300, 50, 1.0))
        elif cmd == 'awg_pulse':
            print(dev.set_awg_pulse(1000, 50, 1.0))
        elif cmd == 'awg_wave':
            func = sys.argv[2] if len(sys.argv) > 2 else 'sine'
            print(dev.set_awg_wave(func, 1000, 1.0))
    finally:
        dev.close()
