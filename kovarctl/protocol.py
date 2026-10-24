"""ROYUAN YC500 lighting protocol (KOVAR keyboards).

Everything here is pure: it builds and parses 64 byte feature report
payloads but never talks to a device. Byte 0 of a payload is the command.

Most commands carry a checksum in byte 7 (0xFF minus the sum of bytes
0-6). The LED parameter commands carry it in byte 8 (over bytes 0-7).

Flash: LED parameters (0x07), side light parameters (0x08), the custom
picture (0x0C) and single custom keys (0x14) are stored in flash. The
firmware keeps the USB interface busy afterwards and locks up if it
receives anything during that time. The live color (0x0E) is RAM only.
"""

from dataclasses import dataclass

from . import layouts

REPORT_LENGTH = 64

CMD_GET_REV = 0x80
CMD_GET_DEVICE_INFO = 0x8F
CMD_SET_LED_PARAMS = 0x07
CMD_GET_LED_PARAMS = 0x87
CMD_SET_SIDE_PARAMS = 0x08
CMD_GET_SIDE_PARAMS = 0x88
CMD_SET_PICTURE = 0x0C
CMD_GET_PICTURE = 0x8C
CMD_SET_LIVE_COLOR = 0x0E
CMD_SET_PICTURE_KEY = 0x14

# Measured on a KOVAR and a KOVAR Wireless (seconds of USB silence needed)
BUSY_LED_PARAMS = 1.0
BUSY_PICTURE = 1.4
BUSY_PICTURE_KEY = 0.15

PICTURE_SLOTS = 128
PICTURE_SIZE = PICTURE_SLOTS * 3
PICTURE_FRAMES = 7
PICTURE_FRAME_SIZE = 56
PICTURE_PAGE_SIZE = 64
PICTURE_FRAME_DELAY = 0.02

SPEED_MAX = 4
BRIGHTNESS_MAX = 4

OPTION_USER_COLOR = 0x07
OPTION_RAINBOW = 0x08
OPTION_CUSTOM_SLOT_1 = 0x00

EFFECT_OFF = 0x00
EFFECT_CUSTOM = 0x0D
EFFECT_LIVE = 0x15

# The firmware does not take pure white as a user color
WHITE = (0xFF, 0xFF, 0xFF)
WHITE_SUBSTITUTE = (0xFA, 0xFF, 0xFA)

PRESET_COLORS = [
    (0xFF, 0x00, 0x00), (0xFF, 0x80, 0x00), (0xFF, 0xFF, 0x00), (0x00, 0xFF, 0x00),
    (0x00, 0xFF, 0xFF), (0x00, 0x00, 0xFF), (0xFF, 0x00, 0xFF),
]


@dataclass(frozen=True)
class Effect:
    name: str
    value: int
    speed: bool = True
    color: bool = True
    # Direction names in the order of their option value (high nibble)
    directions: tuple = ()


EFFECTS = [
    Effect("off", EFFECT_OFF, speed=False, color=False),
    Effect("static", 0x01, speed=False),
    Effect("breathing", 0x02),
    Effect("spectrum-cycle", 0x03, color=False),
    Effect("wave", 0x04, directions=("right", "left", "down", "up")),
    Effect("ripple", 0x05),
    Effect("raindrop", 0x06),
    Effect("snake", 0x07),
    Effect("reactive", 0x08),
    Effect("converge", 0x09),
    Effect("sine-wave", 0x0A),
    Effect("kaleidoscope", 0x0B),
    Effect("line-wave", 0x0C, directions=("right", "left")),
    Effect("custom", EFFECT_CUSTOM, speed=False, color=False),
    Effect("laser", 0x0E),
    Effect("circle-wave", 0x0F, directions=("anticlockwise", "clockwise")),
    Effect("dazzling", 0x10),
    Effect("rain-down", 0x11),
    Effect("meteor", 0x12),
    Effect("reactive-off", 0x13),
    Effect("live", EFFECT_LIVE, speed=False, color=False),
]

SIDE_EFFECTS = [effect for effect in EFFECTS if effect.value in (0x00, 0x01, 0x02, 0x03)]

EFFECTS_BY_NAME = {effect.name: effect for effect in EFFECTS}
EFFECTS_BY_VALUE = {effect.value: effect for effect in EFFECTS}


@dataclass(frozen=True)
class Model:
    device_id: int
    name: str
    keys: dict
    side_light: bool


MODELS = {
    738: Model(738, "KOVAR", layouts.FULL_KEYS, True),
    723: Model(723, "KOVAR Wireless", layouts.FULL_KEYS, True),
    737: Model(737, "KOVAR TKL", layouts.TKL_KEYS, False),
    736: Model(736, "KOVAR 60", layouts.SIXTY_KEYS, False),
}


@dataclass(frozen=True)
class LightState:
    """Decoded LED or side light parameters. Speed 0 is slowest."""

    effect: int
    speed: int
    brightness: int
    option: int
    color: tuple

    @property
    def rainbow(self):
        return (self.option & 0x0F) == OPTION_RAINBOW

    @property
    def variant(self):
        return self.option >> 4

    @property
    def effective_color(self):
        preset = self.option & 0x0F
        if preset < len(PRESET_COLORS):
            return PRESET_COLORS[preset]
        if self.color == WHITE_SUBSTITUTE:
            return WHITE
        return self.color


def checksum(data):
    return (0xFF - (sum(data) & 0xFF)) & 0xFF


def payload(*values, checksum_at=7):
    """Build a payload from leading bytes and add the checksum."""
    buffer = bytearray(REPORT_LENGTH)
    buffer[:len(values)] = bytes(values)
    if checksum_at is not None:
        buffer[checksum_at] = checksum(buffer[:checksum_at])
    return bytes(buffer)


def query(command):
    return payload(command)


def led_params(effect, speed, brightness, option, color, side=False):
    """SET_LEDPARAM (keys) or SET_SLEDPARAM (side light).

    The keys take the speed reversed (0 is fastest), the side light does not.
    """
    if not 0 <= speed <= SPEED_MAX or not 0 <= brightness <= BRIGHTNESS_MAX:
        raise ValueError("speed and brightness must be between 0 and 4")
    if tuple(color) == WHITE:
        color = WHITE_SUBSTITUTE
    raw_speed = speed if side else SPEED_MAX - speed
    command = CMD_SET_SIDE_PARAMS if side else CMD_SET_LED_PARAMS
    return payload(command, effect, raw_speed, brightness, option, *color, checksum_at=8)


def parse_led_params(response, side=False):
    """Decode a GET_LEDPARAM / GET_SLEDPARAM response (64 bytes, command first)."""
    expected = CMD_GET_SIDE_PARAMS if side else CMD_GET_LED_PARAMS
    if response[0] != expected:
        raise ValueError(f"unexpected response 0x{response[0]:02X}")
    raw_speed = min(response[2], SPEED_MAX)
    speed = raw_speed if side else SPEED_MAX - raw_speed
    return LightState(response[1], speed, min(response[3], BRIGHTNESS_MAX), response[4], tuple(response[5:8]))


def state_bytes(state, side=False):
    """The bytes of a SET payload that matter when comparing with a read back state."""
    return led_params(state.effect, state.speed, state.brightness, state.option, state.color, side)[:8]


def live_color(color):
    return payload(CMD_SET_LIVE_COLOR, *color)


def picture_frames(picture):
    """SET_USERPIC frames for a full 384 byte picture."""
    if len(picture) != PICTURE_SIZE:
        raise ValueError(f"picture must be {PICTURE_SIZE} bytes")
    data = bytes(picture).ljust(PICTURE_FRAMES * PICTURE_FRAME_SIZE, b"\0")
    frames = []
    for frame in range(PICTURE_FRAMES):
        header = payload(CMD_SET_PICTURE, 0x00, PICTURE_SIZE & 0xFF, PICTURE_SIZE >> 8, frame)
        chunk = data[frame * PICTURE_FRAME_SIZE:(frame + 1) * PICTURE_FRAME_SIZE]
        frames.append(header[:8] + chunk)
    return frames


def picture_page_request(page):
    return payload(CMD_GET_PICTURE, 0x00, page)


def picture_key(slot, color, layer=0):
    """SET_USERPIC_SIMPLE: one key of the custom picture."""
    buffer = bytearray(payload(CMD_SET_PICTURE_KEY, layer, slot))
    buffer[8:11] = bytes(color)
    return bytes(buffer)


def parse_device_id(response):
    if response[0] != CMD_GET_DEVICE_INFO:
        raise ValueError(f"unexpected response 0x{response[0]:02X}")
    return int.from_bytes(response[1:5], "little")


def parse_color(text):
    """Parse 'ff8000', '#FF8000' or a few names into an (r, g, b) tuple."""
    names = {
        "black": (0, 0, 0), "off": (0, 0, 0), "white": WHITE, "red": (255, 0, 0),
        "green": (0, 255, 0), "blue": (0, 0, 255), "yellow": (255, 255, 0),
        "cyan": (0, 255, 255), "magenta": (255, 0, 255), "orange": (255, 128, 0),
    }
    text = text.strip().lower()
    if text in names:
        return names[text]
    text = text.removeprefix("#").removeprefix("0x")
    if len(text) != 6:
        raise ValueError(f"not a color: {text!r}")
    value = int(text, 16)
    return (value >> 16, (value >> 8) & 0xFF, value & 0xFF)


def format_color(color):
    return "#{:02X}{:02X}{:02X}".format(*color)
