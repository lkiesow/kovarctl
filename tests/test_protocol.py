"""Protocol tests against packets the real keyboards accepted or produced."""

import pytest

from kovarctl import layouts, protocol


def test_device_info_query():
    # Matches the official software's GET_INFOR request
    assert protocol.query(protocol.CMD_GET_DEVICE_INFO)[:8] == bytes.fromhex("8f00000000000070")


def test_parse_device_id():
    response = bytes.fromhex("8fe2020000000070").ljust(64, b"\0")
    assert protocol.parse_device_id(response) == 738


def test_led_params_reverses_speed_for_keys():
    # Original breathing setting of the test keyboard: raw speed 4 is the slowest
    packet = protocol.led_params(0x02, 0, 4, 0x07, (0x6B, 0xF7, 0x3F))
    assert packet[:9] == bytes.fromhex("0702040407" "6bf73f" "46")


def test_side_params_do_not_reverse_speed():
    packet = protocol.led_params(0x02, 3, 4, 0x08, (0xFF, 0xFF, 0xFF), side=True)
    assert packet[:5] == bytes.fromhex("0802030408")


def test_white_is_substituted():
    packet = protocol.led_params(0x01, 0, 4, 0x07, protocol.WHITE)
    assert packet[5:8] == bytes(protocol.WHITE_SUBSTITUTE)


def test_led_params_range():
    with pytest.raises(ValueError):
        protocol.led_params(0x01, 5, 4, 0x07, (0, 0, 0))


def test_parse_led_params():
    state = protocol.parse_led_params(bytes.fromhex("8702040407e1f73f").ljust(64, b"\0"))
    assert (state.effect, state.speed, state.brightness, state.color) == (2, 0, 4, (0xE1, 0xF7, 0x3F))
    assert not state.rainbow


def test_parse_side_params():
    state = protocol.parse_led_params(bytes.fromhex("8802030408ffffff").ljust(64, b"\0"), side=True)
    assert (state.effect, state.speed, state.brightness) == (2, 3, 4)
    assert state.rainbow


def test_preset_color_option():
    state = protocol.LightState(0x01, 0, 4, 0x05, (1, 2, 3))
    assert state.effective_color == (0x00, 0x00, 0xFF)


def test_state_bytes_round_trip():
    response = bytes.fromhex("8702040407e1f73f").ljust(64, b"\0")
    state = protocol.parse_led_params(response)
    assert protocol.state_bytes(state)[1:8] == response[1:8]


def test_picture_frames():
    picture = bytes(range(256)) + bytes(range(128))
    frames = protocol.picture_frames(picture)
    assert len(frames) == 7
    for index, frame in enumerate(frames):
        assert frame[:7] == bytes([0x0C, 0x00, 0x80, 0x01, index, 0, 0])
        assert frame[7] == protocol.checksum(frame[:7])
        assert len(frame) == 64
    assert frames[0][8:64] == picture[0:56]
    assert frames[6][8:56] == picture[336:384]
    assert frames[6][56:64] == bytes(8)


def test_picture_key():
    packet = protocol.picture_key(14, (0x00, 0xFF, 0x00))
    assert packet[:3] == bytes([0x14, 0x00, 14])
    assert packet[8:11] == bytes([0x00, 0xFF, 0x00])


def test_live_color():
    assert protocol.live_color((1, 2, 3))[:4] == bytes([0x0E, 1, 2, 3])


@pytest.mark.parametrize("text, color", [
    ("ff8000", (255, 128, 0)), ("#00FF00", (0, 255, 0)), ("0x0000ff", (0, 0, 255)), ("red", (255, 0, 0)),
])
def test_parse_color(text, color):
    assert protocol.parse_color(text) == color


def test_parse_color_rejects_garbage():
    with pytest.raises(ValueError):
        protocol.parse_color("12345")


def test_layouts():
    assert (len(layouts.FULL_KEYS), len(layouts.TKL_KEYS), len(layouts.SIXTY_KEYS)) == (109, 88, 62)
    for keys in (layouts.FULL_KEYS, layouts.TKL_KEYS, layouts.SIXTY_KEYS):
        assert len(set(keys.values())) == len(keys)
        assert all(0 <= slot < protocol.PICTURE_SLOTS for slot in keys.values())
    # Verified on hardware
    assert layouts.FULL_KEYS["escape"] == 0
    assert layouts.FULL_KEYS["f1"] == 12
    assert layouts.FULL_KEYS["w"] == 14
    assert [layouts.FULL_KEYS[key] for key in ("calculator", "volume_down", "volume_up", "mute")] == [102, 108, 103, 109]
