"""Access to KOVAR keyboards through Linux hidraw.

Safety rules learned on real hardware:
- Only one program may talk to the vendor interface at a time. A lock file
  guards kovarctl instances, and other programs (e.g. OpenRGB) holding the
  hidraw node open are detected.
- After a flash write the firmware must not receive anything for a while.
  The end of that busy time is stored on disk, so separate kovarctl calls
  respect it too.
- Flash writes are rate limited across calls to protect the flash.
"""

import fcntl
import glob
import json
import os
import time

from . import protocol

VENDOR_ID = 0x3151
PRODUCT_ID = 0x4010
DONGLE_PRODUCT_IDS = (0x4011, 0x4014)
VENDOR_INTERFACE = "input2"

FLASH_WRITE_LIMIT = 12
FLASH_WRITE_WINDOW = 60.0


def _ioc(direction, nr, size):
    return (direction << 30) | (size << 16) | (ord("H") << 8) | nr


HIDIOCSFEATURE = _ioc(3, 0x06, protocol.REPORT_LENGTH + 1)
HIDIOCGFEATURE = _ioc(3, 0x07, protocol.REPORT_LENGTH + 1)
USBDEVFS_RESET = (ord("U") << 8) | 20


class KeyboardError(Exception):
    pass


class FlashGuardError(KeyboardError):
    pass


def _hid_id(hidraw):
    try:
        uevent = open(f"/sys/class/hidraw/{hidraw}/device/uevent").read()
    except OSError:
        return None, None, None
    fields = dict(line.split("=", 1) for line in uevent.splitlines() if "=" in line)
    _, vid, pid = fields.get("HID_ID", "0:0:0").split(":")
    return int(vid, 16), int(pid, 16), fields.get("HID_PHYS", "")


def find_vendor_interfaces(product_ids=(PRODUCT_ID,)):
    """Return /dev/hidrawN paths of the vendor interfaces of matching devices."""
    paths = []
    for node in sorted(glob.glob("/sys/class/hidraw/hidraw*"), key=lambda p: int(p.rsplit("hidraw", 1)[1])):
        hidraw = os.path.basename(node)
        vid, pid, phys = _hid_id(hidraw)
        if vid == VENDOR_ID and pid in product_ids and phys.endswith(VENDOR_INTERFACE):
            paths.append("/dev/" + hidraw)
    return paths


def other_users(path):
    """Names of other processes that have the given device node open."""
    users = []
    for fd_dir in glob.glob("/proc/[0-9]*/fd"):
        pid = fd_dir.split("/")[2]
        if int(pid) == os.getpid():
            continue
        try:
            for fd in os.listdir(fd_dir):
                if os.readlink(os.path.join(fd_dir, fd)) == path:
                    users.append(f"{open(f'/proc/{pid}/comm').read().strip()} ({pid})")
                    break
        except OSError:
            continue
    return users


def _state_dir():
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/kovarctl-{os.getuid()}"
    path = os.path.join(base, "kovarctl")
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


class Keyboard:
    """One KOVAR keyboard, opened through its vendor hidraw interface."""

    def __init__(self, path, dry_run=False, force=False, log=None, probe=True):
        vid, pid, phys = _hid_id(os.path.basename(path))
        if vid != VENDOR_ID or pid != PRODUCT_ID or not phys.endswith(VENDOR_INTERFACE):
            raise KeyboardError(f"{path} is not the vendor interface of a 3151:4010 keyboard")

        self.path = path
        self.dry_run = dry_run
        self.force = force
        self.log = log or (lambda message: None)
        self.usb_port = phys.split("/")[0]

        name = self.usb_port.replace("/", "_").replace(":", "_")
        self._lock_file = open(os.path.join(_state_dir(), name + ".lock"), "w")
        self._state_path = os.path.join(_state_dir(), name + ".json")
        try:
            fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise KeyboardError("another kovarctl is using this keyboard") from None

        users = other_users(path)
        if users:
            raise KeyboardError(f"{path} is in use by {', '.join(users)}; close it first (e.g. OpenRGB)")

        self._fd = os.open(path, os.O_RDWR)
        self.device_id = None
        self.model = None

        if probe:
            try:
                self.device_id = protocol.parse_device_id(self.query(protocol.CMD_GET_DEVICE_INFO))
            except (KeyboardError, ValueError) as error:
                self.close()
                raise KeyboardError(f"{path} does not answer ({error}); try 'kovarctl recover'") from None
            self.model = protocol.MODELS.get(self.device_id)

    def close(self):
        os.close(self._fd)
        self._lock_file.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # Persistent state shared between calls

    def _load_state(self):
        try:
            with open(self._state_path) as state_file:
                return json.load(state_file)
        except (OSError, ValueError):
            return {"busy_until": 0.0, "flash_writes": []}

    def _save_state(self, state):
        with open(self._state_path, "w") as state_file:
            json.dump(state, state_file)

    def wait_idle(self):
        remaining = self._load_state()["busy_until"] - time.time()
        if remaining > 0:
            time.sleep(remaining)

    # Raw transfers

    def _send(self, data):
        report = b"\0" + bytes(data).ljust(protocol.REPORT_LENGTH, b"\0")
        try:
            fcntl.ioctl(self._fd, HIDIOCSFEATURE, report)
        except OSError as error:
            raise KeyboardError(f"sending to {self.path} failed ({error}); "
                                "if the keyboard stopped answering, run 'kovarctl recover'") from None

    def _receive(self):
        report = bytearray(protocol.REPORT_LENGTH + 1)
        try:
            fcntl.ioctl(self._fd, HIDIOCGFEATURE, report)
        except OSError as error:
            raise KeyboardError(f"reading from {self.path} failed ({error}); "
                                "if the keyboard stopped answering, run 'kovarctl recover'") from None
        return bytes(report[1:])

    def query(self, command, *arguments, wait=0.02):
        """Send a read request and return the 64 byte response."""
        self.wait_idle()
        self._send(protocol.payload(command, *arguments))
        time.sleep(wait)
        return self._receive()

    # Reading

    def revision(self):
        response = self.query(protocol.CMD_GET_REV)
        return f"{response[2]}.{response[1]:02d}"

    def light_state(self, side=False):
        command = protocol.CMD_GET_SIDE_PARAMS if side else protocol.CMD_GET_LED_PARAMS
        return protocol.parse_led_params(self.query(command), side=side)

    def picture(self):
        data = b"".join(self.query(protocol.CMD_GET_PICTURE, 0x00, page) for page in range(protocol.PICTURE_FRAMES))
        return bytearray(data[:protocol.PICTURE_SIZE])

    # Writing

    def flash_write(self, packets, busy, what):
        """Send packets that end in a flash write, respecting busy time and rate limit."""
        state = self._load_state()
        now = time.time()
        recent = [stamp for stamp in state["flash_writes"] if now - stamp < FLASH_WRITE_WINDOW]

        if len(recent) >= FLASH_WRITE_LIMIT and not self.force:
            raise FlashGuardError(
                f"{len(recent)} flash writes in the last {FLASH_WRITE_WINDOW:.0f} s; refusing to write more "
                "to protect the keyboard flash (use --force to override, or 'live' for fast changes)")

        if self.dry_run:
            for packet in packets:
                self.log(f"would send {packet[:16].hex(' ')} ...")
            return

        self.wait_idle()
        for index, packet in enumerate(packets):
            if index > 0:
                time.sleep(protocol.PICTURE_FRAME_DELAY)
            self._send(packet)

        state["busy_until"] = time.time() + busy
        state["flash_writes"] = recent + [time.time()]
        self._save_state(state)
        self.log(f"flash write: {what}")

    def set_light(self, effect, speed, brightness, option, color, side=False):
        """Write LED or side light parameters unless the keyboard already has them."""
        packet = protocol.led_params(effect, speed, brightness, option, color, side=side)
        if protocol.state_bytes(self.light_state(side=side), side=side) == packet[:8]:
            self.log("unchanged, nothing written")
            return False
        self.flash_write([packet], protocol.BUSY_LED_PARAMS, "side light" if side else "LED parameters")
        return True

    def set_picture(self, picture):
        if bytes(self.picture()) == bytes(picture):
            self.log("picture unchanged, nothing written")
            return False
        self.flash_write(protocol.picture_frames(picture), protocol.BUSY_PICTURE, "custom picture")
        return True

    def set_picture_key(self, slot, color):
        self.flash_write([protocol.picture_key(slot, color)], protocol.BUSY_PICTURE_KEY, f"custom key slot {slot}")

    def live_color(self, color):
        """RAM only, safe to send often."""
        if self.dry_run:
            self.log(f"would send {protocol.live_color(color)[:8].hex(' ')}")
            return
        self.wait_idle()
        self._send(protocol.live_color(color))

    def reset(self):
        """USB reset of the keyboard, recovers a locked up vendor interface."""
        usb_device = os.path.realpath(f"/sys/class/hidraw/{os.path.basename(self.path)}/device/../..")
        bus = int(open(os.path.join(usb_device, "busnum")).read())
        device = int(open(os.path.join(usb_device, "devnum")).read())
        fd = os.open(f"/dev/bus/usb/{bus:03d}/{device:03d}", os.O_WRONLY)
        try:
            fcntl.ioctl(fd, USBDEVFS_RESET, 0)
        finally:
            os.close(fd)
