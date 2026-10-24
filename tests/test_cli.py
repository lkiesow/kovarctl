"""CLI tests against a simulated keyboard firmware."""

import json

import pytest
from click.testing import CliRunner

from kovarctl import cli, device, protocol


class FakeKeyboard(device.Keyboard):
    """Keyboard with the hidraw transfers replaced by a firmware model."""

    instances = []

    def __init__(self, path, dry_run=False, force=False, log=None, probe=True):
        self.path = path
        self.dry_run = dry_run
        self.force = force
        self.log = log or (lambda message: None)
        self._state_path = str(FakeKeyboard.state_path)
        self.device_id = 738
        self.model = protocol.MODELS[738]
        self.led = bytes.fromhex("02040407e1f73f")
        self.side = bytes.fromhex("02030408ffffff")
        self.picture_data = bytearray(protocol.PICTURE_SIZE)
        self.frames = []
        self.sent = []
        self.response = bytes(64)
        FakeKeyboard.instances.append(self)

    def close(self):
        pass

    def wait_idle(self):
        pass

    def _send(self, data):
        data = bytes(data)
        self.sent.append(data)
        command = data[0]
        if command == protocol.CMD_GET_LED_PARAMS:
            self.response = bytes([command]) + self.led
        elif command == protocol.CMD_GET_SIDE_PARAMS:
            self.response = bytes([command]) + self.side
        elif command == protocol.CMD_GET_PICTURE:
            page = data[2]
            self.response = bytes(self.picture_data[page * 64:(page + 1) * 64]).ljust(64, b"\0")
        elif command == protocol.CMD_GET_DEVICE_INFO:
            self.response = bytes([command]) + (738).to_bytes(4, "little")
        elif command == protocol.CMD_GET_REV:
            self.response = bytes([command, 0x03, 0x05])
        elif command == protocol.CMD_SET_LED_PARAMS:
            self.led = data[1:8]
        elif command == protocol.CMD_SET_SIDE_PARAMS:
            self.side = data[1:8]
        elif command == protocol.CMD_SET_PICTURE:
            frame = data[4]
            chunk = data[8:8 + protocol.PICTURE_FRAME_SIZE]
            start = frame * protocol.PICTURE_FRAME_SIZE
            end = min(start + len(chunk), protocol.PICTURE_SIZE)
            self.picture_data[start:end] = chunk[:end - start]
            self.frames.append(frame)
        elif command == protocol.CMD_SET_PICTURE_KEY:
            slot = data[2]
            self.picture_data[slot * 3:slot * 3 + 3] = data[8:11]

    def _receive(self):
        return self.response.ljust(64, b"\0")

    def writes(self, command):
        return [packet for packet in self.sent if packet[0] == command]


@pytest.fixture
def run(monkeypatch, tmp_path):
    FakeKeyboard.instances = []
    FakeKeyboard.state_path = tmp_path / "state.json"
    monkeypatch.setattr(cli, "Keyboard", FakeKeyboard)
    monkeypatch.setattr(cli, "find_vendor_interfaces", lambda *args: ["/dev/hidraw-test"])
    state = {}

    def invoke(*args, keep=None):
        if keep is not None:
            # Carry the simulated firmware over to the next call
            original = FakeKeyboard.__init__

            def init(self, *a, **k):
                original(self, *a, **k)
                self.led, self.side, self.picture_data = keep.led, keep.side, keep.picture_data
            monkeypatch.setattr(FakeKeyboard, "__init__", init)
        result = CliRunner().invoke(cli.main, list(args), catch_exceptions=False)
        if keep is not None:
            monkeypatch.setattr(FakeKeyboard, "__init__", original)
        state["keyboard"] = FakeKeyboard.instances[-1] if FakeKeyboard.instances else None
        return result, state["keyboard"]

    return invoke


def test_info(run):
    result, _ = run("info")
    assert result.exit_code == 0, result.output
    assert "KOVAR (ID 738)" in result.output
    assert "breathing, speed 0, brightness 4, color #E1F73F" in result.output
    assert "Side:      breathing, speed 3, brightness 4, rainbow" in result.output


def test_mode_keeps_unspecified_options(run):
    result, keyboard = run("mode", "breathing", "--speed", "3")
    assert result.exit_code == 0, result.output
    assert keyboard.led == bytes.fromhex("02010407e1f73f")


def test_mode_unchanged_does_not_write(run):
    result, keyboard = run("mode", "breathing")
    assert "unchanged" in result.output
    assert keyboard.writes(protocol.CMD_SET_LED_PARAMS) == []


def test_mode_direction(run):
    result, keyboard = run("mode", "wave", "--direction", "up", "--color", "ff0000")
    assert result.exit_code == 0, result.output
    assert keyboard.led[0] == 0x04 and keyboard.led[3] == 0x37


def test_mode_rejects_wrong_direction(run):
    result, _ = run("mode", "line-wave", "--direction", "up")
    assert result.exit_code != 0


def test_side_speed_not_reversed(run):
    result, keyboard = run("side", "breathing", "--speed", "1")
    assert result.exit_code == 0, result.output
    # Speed 1 is sent as 1 (keys would get 3), rainbow and color are kept
    assert keyboard.side == bytes.fromhex("02010408" + "fafffa")


def test_dry_run_writes_nothing(run):
    result, keyboard = run("--dry-run", "mode", "static", "--color", "00ff00")
    assert result.exit_code == 0
    assert keyboard.writes(protocol.CMD_SET_LED_PARAMS) == []


def test_custom_set_few_keys_uses_single_key_writes(run):
    result, keyboard = run("custom", "set", "escape=red", "w=00ff00")
    assert result.exit_code == 0, result.output
    assert len(keyboard.writes(protocol.CMD_SET_PICTURE_KEY)) == 2
    assert keyboard.writes(protocol.CMD_SET_PICTURE) == []
    assert keyboard.picture_data[0:3] == bytes([255, 0, 0])
    assert keyboard.picture_data[42:45] == bytes([0, 255, 0])


def test_custom_set_all_uploads_picture_once(run):
    result, keyboard = run("custom", "set", "all=blue", "--activate")
    assert result.exit_code == 0, result.output
    assert keyboard.frames == list(range(7))
    assert all(keyboard.picture_data[slot * 3:slot * 3 + 3] == bytes([0, 0, 255]) for slot in protocol.MODELS[738].keys.values())
    assert keyboard.led[0] == protocol.EFFECT_CUSTOM


def test_custom_save_and_load(run, tmp_path):
    first, keyboard = run("custom", "set", "all=blue")
    out = tmp_path / "picture.json"
    run("custom", "save", str(out), keep=keyboard)
    data = json.loads(out.read_text())
    assert data["escape"] == "#0000FF" and len(data) == 109

    data["escape"] = "#FF0000"
    out.write_text(json.dumps(data))
    result, again = run("custom", "load", str(out), keep=keyboard)
    assert result.exit_code == 0, result.output
    assert len(again.writes(protocol.CMD_SET_PICTURE_KEY)) == 1


def test_unknown_key(run):
    result, _ = run("custom", "set", "nokey=red")
    assert result.exit_code != 0
    assert "unknown key" in result.output


def test_flash_rate_limit(run):
    for index in range(device.FLASH_WRITE_LIMIT):
        result, _ = run("mode", "static", "--color", f"0000{index + 1:02x}")
        assert result.exit_code == 0, result.output
    result, _ = run("mode", "static", "--color", "ff0000")
    assert result.exit_code != 0
    assert "refusing to write" in result.output
    result, _ = run("--force", "mode", "static", "--color", "ff0000")
    assert result.exit_code == 0
