#!/usr/bin/env bash
# One-time root setup: give non-root users access to the CAEN DT5810B
# (Cypress FX3, vendor 0x21e1, boot PID 000d / normal 000e).
#
# Run once:  sudo bash tools/install_usb_rules.sh
# Afterwards fx3_firmware_loader.py and dt5810.py work without sudo.
# Survives replug and the 000d->000e re-enumeration; the udev rule applies
# to every future appearance of the device.

set -euo pipefail

RULE=/etc/udev/rules.d/99-caen-dt5810.rules

if [ "$(id -u)" -ne 0 ]; then
    echo "not root -- run as: sudo bash $0" >&2
    exit 1
fi

echo 'SUBSYSTEM=="usb", ATTRS{idVendor}=="21e1", MODE="0666"' > "$RULE"
chmod 644 "$RULE"
echo "installed $RULE:"
cat "$RULE"

udevadm control --reload
# Re-apply to already-connected devices, so no replug is needed now.
udevadm trigger --subsystem-match=usb

echo
echo "device nodes for vendor 21e1 now:"
for d in /sys/bus/usb/devices/*/idVendor; do
    if grep -q 21e1 "$d"; then
        dev=$(dirname "$d")
        bus=$(basename "$dev" | cut -d- -f1)
        num=$(cat "$dev/devnum")
        ls -l "/dev/bus/usb/$(printf %03d "$bus")/$(printf %03d "$num")"
    fi
done
echo
echo "done -- rerun 'python3 fx3_firmware_loader.py' without sudo"
