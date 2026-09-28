#!/usr/bin/env python3
"""Fix the extra-hex-zero register addresses across the project.

See New_attemp_20260922/REGISTER_ADDRESS_BUG.md. The timebase and energy register
families were written with one extra hex zero, landing 16x away from the real
register. This rewrites them BY VALUE rather than by text, because the correct and
incorrect spellings differ by a single zero:

    0x0100006  == 0x100006   CORRECT    (1,048,582)
    0x01000006 == 0x1000006  WRONG     (16,777,222)

Ground-truth files are never touched: the Ghidra/objdump dumps and the C#
decompilation record what the DLL actually does and must stay as they are.

Usage:  python3 fix_register_addresses.py [--apply]
"""
import os, re, sys

ROOT = '/home/ryan/caen_signal_emulator'
APPLY = '--apply' in sys.argv

# wrong value -> correct literal
FIX = {
    0x1000004: '0x100004',    # LFSR timebase strobe
    0x1000006: '0x100006',    # Poisson alpha
    0x1000007: '0x100007',    # paralyzable
    0x1000008: '0x100008',    # dead time
    0x1000009: '0x100009',    # period
    0x100000a: '0x10000a',    # TimeMode
    0x20f0002: '0x20f002',    # energy LFSR strobe
    0x20f0004: '0x20f004',    # EnergyMode
    0x20f0005: '0x20f005',    # energy value
}

# never rewrite these - they are records of the DLL, not our code
SKIP_DIRS = {'.git', 'New_attemp_20260922', 'decompiled', 'ghidra_proj',
             'extracted', 'CAEN', 'build', 'build_core_test', '__pycache__',
             'usb_captures', 'plots', 'wine_shim'}
SKIP_EXT = {'.txt', '.pcap', '.exe', '.zip', '.niu', '.pdf', '.img', '.a', '.o'}
TAKE_EXT = {'.py', '.cpp', '.h', '.hpp', '.c', '.md', '.sh'}

HEX = re.compile(r'0[xX]([0-9a-fA-F]+)')


def fix_text(txt):
    hits = []

    def sub(m):
        val = int(m.group(1), 16)
        if val in FIX:
            new = FIX[val]
            if m.group(0).isupper() or m.group(1).isupper():
                new = '0x' + new[2:].upper()
            hits.append((m.group(0), new))
            return new
        return m.group(0)

    return HEX.sub(sub, txt), hits


def main():
    total = 0
    files = 0
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if ext in SKIP_EXT or ext not in TAKE_EXT:
                continue
            path = os.path.join(dirpath, fn)
            try:
                txt = open(path, encoding='utf-8', errors='replace').read()
            except Exception:
                continue
            new, hits = fix_text(txt)
            if not hits:
                continue
            files += 1
            total += len(hits)
            rel = os.path.relpath(path, ROOT)
            counts = {}
            for a, b in hits:
                counts[(a, b)] = counts.get((a, b), 0) + 1
            print(f"{rel}")
            for (a, b), n in sorted(counts.items()):
                print(f"    {a} -> {b}   x{n}")
            if APPLY:
                open(path, 'w', encoding='utf-8').write(new)
    print()
    print(f"{'APPLIED' if APPLY else 'DRY RUN'}: {total} replacements in {files} files")
    if not APPLY:
        print("re-run with --apply to write")


if __name__ == '__main__':
    main()
