# The register-address bug — root cause of the 318 Hz lock

**Found 2026-09-23 by reading how the Windows controller actually works.**

## Summary

Two whole register families in this project's code and documentation have **one
extra hex zero**, so every write to them has been landing 16× away from the real
register. That is why the pulse rate appeared "locked at 318 Hz" since June, and
why the `energy` argument never affected pulse amplitude.

| block | this project used | **DDE3.dll actually uses** | |
|---|---|---|---|
| Timebase | `0x0100000a`, `0x01000009`, `0x01000006`, `0x01000007`, `0x01000008` | **`0x10000a`, `0x100009`, `0x100006`, `0x100007`, `0x100008`** | ❌ wrong |
| Energy | `0x020f0002`, `0x020f0004`, `0x020f0005` | **`0x20f002`, `0x20f004`, `0x20f005`** | ❌ wrong |
| Analog | `0x0f00000x` | `0xf00000x` | ✅ same number |
| Run control | `0x01c0000x` | `0x1c0000x` | ✅ same number |
| DRC / TR | `0x30000x`, `0x300010`, `0x300014` | same | ✅ |
| Shape gen | `0x50f00x`, `0x50000x` | same | ✅ |

Note the trap: `0x01c00006` and `0x1c00006` are the *same number*, which is why
run control always worked and made the addressing look fine. But
`0x01000009` = 16,777,225 while `0x100009` = 1,048,585 — genuinely different.
The same applies to `0x020f0005` vs `0x20f005`.

## Evidence

`ConfigureTimebase` (`tb_decomp.txt`) is a thin wrapper onto `FUN_10005080`,
whose disassembly (`tb2_asm.txt`) shows:

```
100050ED  SHL ESI,0x1c                  ; ESI = channel << 28
100050F3  LEA EDX,[ESI + 0x10000a]      ; TimeMode
10005154  LEA EBX,[ESI + 0x100009]      ; period
1000519C  LEA EAX,[ESI + 0x100006]      ; Poisson alpha
100051B8  LEA EAX,[ESI + 0x100008]      ; deadtime
100051CF  LEA EAX,[ESI + 0x100007]      ; paralyzable
```

Corroborated independently by two Ghidra C decompilations, `ghidra_tb.txt` and
`w_decomp.txt`, both of which call
`FUN_1000c7a0(dev, &value, iVar3 + 0x100009, 1)`.

For energy, `ghidra_decomp2.txt`:

```
iVar2 = param_1 * 0x10000000 + 0x20f004;    // EnergyMode
iVar2 = param_1 * 0x10000000 + 0x20f005;    // Energy value
```

## The maths, decoded from the same function

```
rate_clamped = max(0.01, rate)                       ; 0.01 cps floor, matches manual
period       = round(clock / rate_clamped - 1)       -> 0x100009
alpha        = 2^32 * 0.25 * rate_clamped / clock    -> 0x100006   (Poisson)
deadtime                                             -> 0x100008
paralyzable                                          -> 0x100007
TimeMode     written FIRST                           -> 0x10000a
```

`clock` is the device field at `[dev+0x10]`, set from the hardware version.

## Verified on hardware

Rate now tracks the period register exactly:

| target | period | measured | error |
|---|---|---|---|
| 4 kHz | 78124 | 4000 Hz | −0.0% |
| 8 kHz | 39061 | 8000 Hz | −0.0% |
| 16 kHz | 19530 | 15982 Hz | −0.1% |
| 31.25 kHz | 9999 | 31250 Hz | −0.0% |

**Derived clock = 312.5 MHz**, i.e. the quarter clock (1.25 GHz / 4) — the same
clock the AWG uses. So:

    period = 312.5e6 / rate - 1

The old "318 Hz" was simply the register's power-on default, never overwritten
because every write went to the wrong address.

Energy likewise became a real control:

| `0x20f005` | amplitude |
|---|---|
| 4000 | 0.157 V |
| 10000 | 0.362 V |
| 16000 | 0.567 V |
| 24000 | 0.840 V |
| 30000 | 1.045 V |

Linear: **V = 3.414e-5 × reg + 0.0205** (at gain 1184, 1 MΩ). Note the register
value is energy_LSB × 2 and must stay ≤ 32767 — writing 40000 wraps.

## Consequences

* **Pulse rate is controllable.** The project's oldest open item is closed.
* **Amplitude should come from energy, not gain.** Pulse height ∝ energy is the
  physically meaningful knob for a detector emulator; the analog gain was only
  ever a workaround.
* **Spectrum mode should now be reachable** — `0x20f004 = 1` plus a programmed
  histogram, which was untestable while the mode register was misaddressed.
* **The Digital RC "no output" result needs revisiting.** DRC was always driven
  with a misaddressed energy register, so it may have been running with zero
  amplitude the whole time rather than being broken. This is the first thing to
  retest.

## Files to fix

`linux/dt5810.py`, `linux_port/core/*`, and the docs `REGISTER_MAP.md`,
`HOW_IT_WORKS.md`, `PROTOCOL.md`, `KNOWLEDGE_BASE.md` §7.2/§7.3 all carry the
wrong addresses.
