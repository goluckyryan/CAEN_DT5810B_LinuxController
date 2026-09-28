"""STRICTLY read-only Rigol DHO4804 helper (192.168.2.200:5555).

Ryan drives the scope. This module MUST NOT send anything but queries.

Enforced below: send() rejects any command that is not a '?' query. An earlier
version of this file sent :WAV:SOUR / :WAV:MODE / :WAV:FORM to set up a waveform
transfer; that is a write and is no longer permitted here. Waveform capture is
therefore query-only and depends on whatever transfer format is already set on
the instrument (read it back with :WAV:PRE?).

ONE persistent socket - the scope drops connections if you open one per query.
"""
import socket, struct, time

ADDR = ('192.168.2.200', 5555)


class Scope:
    def __init__(self, addr=ADDR, timeout=10):
        self.s = socket.create_connection(addr, timeout=timeout)
        self.s.settimeout(timeout)

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass

    # The ONLY setting Ryan has authorised us to change (2026-09-22).
    _ALLOWED_WRITE = ':TRIG:EDGE:LEV'

    def _send(self, cmd):
        """Guard: queries, plus the one authorised write (trigger level)."""
        if '?' not in cmd and not cmd.upper().startswith(self._ALLOWED_WRITE):
            raise PermissionError(
                f"refused SCPI write {cmd!r}: only queries and "
                f"{self._ALLOWED_WRITE} are permitted (Ryan drives the scope)")
        self.s.sendall((cmd + '\n').encode())

    def set_trigger_level(self, volts):
        """The one permitted write."""
        self._send(f':TRIG:EDGE:LEV {volts:.4f}')
        time.sleep(0.2)
        return self.q(':TRIG:EDGE:LEV?')

    def q(self, cmd, nbytes=65536):
        self._send(cmd)
        return self.s.recv(nbytes).decode(errors='replace').strip()

    def qf(self, cmd):
        """Query a float; returns None for the 9.9E37 'invalid' sentinel."""
        try:
            v = float(self.q(cmd))
        except ValueError:
            return None
        return None if abs(v) > 1e30 else v

    def meas(self, ch=1):
        return {m: self.qf(f':MEAS:ITEM? {m},CHAN{ch}')
                for m in ('FREQ', 'PER', 'VPP', 'VMAX', 'VMIN', 'VAVG')}

    def settings(self):
        return {
            'tb_scale': self.q(':TIM:MAIN:SCAL?'),
            'ch1_scale': self.q(':CHAN1:SCAL?'),
            'ch1_offs': self.q(':CHAN1:OFFS?'),
            'ch1_imp': self.q(':CHAN1:IMP?'),
            'trig_stat': self.q(':TRIG:STAT?'),
            'trig_lev': self.q(':TRIG:EDGE:LEV?'),
        }

    def waveform(self, ch=None):
        """Return (times_s, volts) for the source the SCOPE has selected.

        Query-only, so this cannot send :WAV:SOUR -- it reads whatever source the
        instrument is already set to. Passing `ch` asserts that it matches; it
        does NOT switch. Previously this silently returned CH1 data when asked
        for CH2, and a conclusion was drawn from the mislabelled trace.
        """
        src = self.q(':WAV:SOUR?').strip().upper()
        if ch is not None and not src.endswith(str(ch)):
            raise RuntimeError(
                f"scope :WAV:SOUR is {src}, cannot return CH{ch} — this module is "
                f"read-only and cannot switch it. Select CH{ch} on the scope, or "
                f"use :MEAS:ITEM? queries which are per-channel.")
        pre = self.q(':WAV:PRE?')
        p = pre.split(',')
        xinc, xorig, xref = float(p[4]), float(p[5]), float(p[6])
        yinc, yorig, yref = float(p[7]), float(p[8]), float(p[9])

        self.s.sendall(b':WAV:DATA?\n')
        # IEEE block: #<ndigits><length><payload>
        hdr = b''
        while len(hdr) < 2:
            hdr += self.s.recv(2 - len(hdr))
        assert hdr[0:1] == b'#', f"bad block header {hdr!r}"
        nd = int(hdr[1:2])
        lenbuf = b''
        while len(lenbuf) < nd:
            lenbuf += self.s.recv(nd - len(lenbuf))
        n = int(lenbuf)
        buf = b''
        while len(buf) < n:
            chunk = self.s.recv(min(65536, n - len(buf)))
            if not chunk:
                break
            buf += chunk
        self.s.recv(1)   # trailing newline

        volts = [(b - yorig - yref) * yinc for b in buf]
        times = [(i - xref) * xinc + xorig for i in range(len(buf))]
        return times, volts


def describe(times, volts):
    """Crude shape classification: where's the peak, and how does it fall?"""
    if not volts:
        return "empty"
    vmin, vmax = min(volts), max(volts)
    span = vmax - vmin
    n = len(volts)
    ipk = max(range(n), key=lambda i: volts[i])
    out = [f"n={n}  span={span:.4f} V  vmin={vmin:.4f}  vmax={vmax:.4f}",
           f"peak at sample {ipk}/{n} (t={times[ipk]*1e6:.2f} us)"]
    if span < 0.02:
        out.append("=> essentially FLAT (no pulse)")
        return "\n      ".join(out)

    # Look at the falling side after the peak: exponential vs linear.
    tail = volts[ipk:]
    if len(tail) > 20:
        base = vmin
        amp = vmax - base
        # time to fall to 1/e and to 1/e^2 of amplitude
        def cross(frac):
            tgt = base + amp * frac
            for j, v in enumerate(tail):
                if v <= tgt:
                    return j
            return None
        j1, j2 = cross(0.3679), cross(0.1353)
        if j1 and j2:
            ratio = j2 / j1
            # exponential: t(1/e^2) = 2 * t(1/e)  -> ratio ~2.0
            # linear ramp:  ratio ~ (1-0.135)/(1-0.368) = 1.37
            kind = ("EXPONENTIAL decay" if 1.7 < ratio < 2.4 else
                    "LINEAR ramp/triangle" if 1.15 < ratio < 1.6 else
                    f"other (ratio {ratio:.2f})")
            xinc = times[1] - times[0] if len(times) > 1 else 0
            out.append(f"fall 1/e at +{j1*xinc*1e6:.2f} us, 1/e^2 at +{j2*xinc*1e6:.2f} us"
                       f"  ratio={ratio:.2f} => {kind}")
            if 1.7 < ratio < 2.4:
                out.append(f"   implied tau ~ {j1*xinc*1e6:.2f} us")
        else:
            out.append("tail never falls to 1/e within the captured window")
    return "\n      ".join(out)
