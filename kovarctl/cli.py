"""Command line interface for KOVAR keyboards."""

import colorsys
import json
import sys
import time

import click

from . import protocol
from .device import DONGLE_PRODUCT_IDS, Keyboard, KeyboardError, find_vendor_interfaces


class Context:
    def __init__(self, device, model_id, dry_run, force, verbose):
        self.device = device
        self.model_id = model_id
        self.dry_run = dry_run
        self.force = force
        self.verbose = verbose

    def log(self, message):
        if self.verbose or self.dry_run:
            click.echo(message, err=True)

    def open(self, probe=True):
        if self.device:
            paths = [self.device]
        else:
            paths = find_vendor_interfaces()
            if not paths:
                raise click.ClickException("no KOVAR keyboard found (3151:4010); dongles are not supported yet")

        keyboards = []
        for path in paths:
            try:
                keyboard = Keyboard(path, self.dry_run, self.force, self.log, probe=probe)
            except KeyboardError as error:
                if len(paths) == 1:
                    raise click.ClickException(str(error))
                continue
            if self.model_id is None or keyboard.device_id == self.model_id:
                keyboards.append(keyboard)
            else:
                keyboard.close()

        if len(keyboards) != 1:
            for keyboard in keyboards:
                keyboard.close()
            raise click.ClickException(
                "no matching keyboard" if not keyboards else
                "several keyboards found, select one with --device or --id (see 'kovarctl list')")

        keyboard = keyboards[0]
        if probe and keyboard.model is None:
            keyboard.close()
            raise click.ClickException(f"unknown model ID {keyboard.device_id}")
        return keyboard


pass_context = click.make_pass_decorator(Context)


class Group(click.Group):
    """Report device errors as normal command errors."""

    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except KeyboardError as error:
            raise click.ClickException(str(error))


def color_argument(ctx, param, value):
    if value is None:
        return None
    try:
        return protocol.parse_color(value)
    except ValueError as error:
        raise click.BadParameter(str(error))


def describe(state, side=False):
    effect = protocol.EFFECTS_BY_VALUE.get(state.effect)
    name = effect.name if effect else f"unknown (0x{state.effect:02X})"
    parts = [name]
    if effect is None or effect.speed:
        parts.append(f"speed {state.speed}")
    parts.append(f"brightness {state.brightness}")
    if effect is None or effect.color:
        parts.append("rainbow" if state.rainbow else f"color {protocol.format_color(state.effective_color)}")
    if effect and effect.directions and not side:
        parts.append(f"direction {effect.directions[state.variant % len(effect.directions)]}")
    return ", ".join(parts)


@click.group(cls=Group)
@click.option("--device", metavar="PATH", help="hidraw node of the keyboard's vendor interface.")
@click.option("--id", "model_id", type=int, help="Select the keyboard by firmware model ID.")
@click.option("--dry-run", is_flag=True, help="Read from the keyboard, but only print what would be written.")
@click.option("--force", is_flag=True, help="Ignore the flash write rate limit.")
@click.option("-v", "--verbose", is_flag=True, help="Report flash writes.")
@click.version_option(package_name="kovarctl")
@click.pass_context
def main(ctx, device, model_id, dry_run, force, verbose):
    """Control the lighting of KOVAR keyboards (ROYUAN YC500 firmware).

    Effect settings and custom pictures are stored in the keyboard flash.
    kovarctl skips writes that change nothing and limits how often flash is
    written. Use 'live' for frequently changing colors.
    """
    ctx.obj = Context(device, model_id, dry_run, force, verbose)


@main.command("list")
def list_keyboards():
    """List connected keyboards."""
    for path in find_vendor_interfaces():
        try:
            with Keyboard(path) as keyboard:
                name = keyboard.model.name if keyboard.model else "unknown model"
                click.echo(f"{path}  ID {keyboard.device_id:<4} {name}  (firmware {keyboard.revision()})")
        except (KeyboardError, OSError) as error:
            click.echo(f"{path}  {error}")
    for path in find_vendor_interfaces(DONGLE_PRODUCT_IDS):
        click.echo(f"{path}  wireless dongle (not supported yet, use the USB cable)")


@main.command()
@pass_context
def info(obj):
    """Show model, firmware and current lighting."""
    with obj.open() as keyboard:
        click.echo(f"Model:     {keyboard.model.name} (ID {keyboard.device_id})")
        click.echo(f"Firmware:  {keyboard.revision()}")
        click.echo(f"Device:    {keyboard.path}")
        click.echo(f"Keys:      {describe(keyboard.light_state())}")
        if keyboard.model.side_light:
            click.echo(f"Side:      {describe(keyboard.light_state(side=True), side=True)}")
        picture = keyboard.picture()
        lit = sum(1 for slot in keyboard.model.keys.values() if any(picture[slot * 3:slot * 3 + 3]))
        click.echo(f"Custom:    {lit} of {len(keyboard.model.keys)} keys lit")


@main.command()
def effects():
    """List effects and their options."""
    for effect in protocol.EFFECTS:
        options = []
        if effect.speed:
            options.append("speed")
        if effect.color:
            options.append("color/rainbow")
        if effect.directions:
            options.append("direction: " + "/".join(effect.directions))
        side = " (also side light)" if effect in protocol.SIDE_EFFECTS else ""
        click.echo(f"{effect.name:15} {', '.join(options)}{side}")


@main.command()
@pass_context
def keys(obj):
    """List the key names of the connected keyboard."""
    with obj.open() as keyboard:
        click.echo(" ".join(keyboard.model.keys))


def light_options(command):
    command = click.option("--speed", type=click.IntRange(0, protocol.SPEED_MAX), help="0 (slow) to 4 (fast).")(command)
    command = click.option("--brightness", type=click.IntRange(0, protocol.BRIGHTNESS_MAX), help="0 to 4.")(command)
    command = click.option("--color", callback=color_argument, help="RRGGBB or a color name.")(command)
    command = click.option("--rainbow", is_flag=True, help="Use changing colors instead of one color.")(command)
    return command


def apply_light(keyboard, effect, speed, brightness, color, rainbow, direction=None, side=False):
    current = keyboard.light_state(side=side)
    same_effect = current.effect == effect.value

    speed = speed if speed is not None else (current.speed if same_effect else 2)
    brightness = brightness if brightness is not None else current.brightness or protocol.BRIGHTNESS_MAX

    if effect.value == protocol.EFFECT_OFF:
        speed, brightness, option, color = 0, 0, protocol.OPTION_USER_COLOR, (0, 0, 0)
    elif effect.value == protocol.EFFECT_CUSTOM:
        option, color = protocol.OPTION_CUSTOM_SLOT_1, (0x00, 0xC8, 0xC8)
    elif not effect.color:
        option, color = protocol.OPTION_USER_COLOR, (0, 0, 0)
    else:
        if rainbow:
            option = protocol.OPTION_RAINBOW
        elif color is not None:
            option = protocol.OPTION_USER_COLOR
        else:
            option = protocol.OPTION_RAINBOW if current.rainbow else protocol.OPTION_USER_COLOR
        color = color or current.effective_color

    if effect.directions:
        variant = effect.directions.index(direction) if direction else (current.variant if same_effect else 0)
        option |= variant << 4
    elif direction:
        raise click.BadParameter(f"{effect.name} has no direction", param_hint="--direction")

    if not effect.speed:
        speed = 0

    return keyboard.set_light(effect.value, speed, brightness, option, color, side=side)


@main.command()
@click.argument("effect", type=click.Choice([effect.name for effect in protocol.EFFECTS if effect.name != "live"]))
@light_options
@click.option("--direction", type=click.Choice(["left", "right", "up", "down", "clockwise", "anticlockwise"]))
@pass_context
def mode(obj, effect, speed, brightness, color, rainbow, direction):
    """Set the effect of the keys (stored in flash).

    Options that are not given keep their current value.
    """
    effect = protocol.EFFECTS_BY_NAME[effect]
    if direction and effect.directions and direction not in effect.directions:
        raise click.BadParameter(f"{effect.name} supports {', '.join(effect.directions)}", param_hint="--direction")
    with obj.open() as keyboard:
        changed = apply_light(keyboard, effect, speed, brightness, color, rainbow, direction)
        if not changed:
            click.echo("Keys: unchanged")
        elif not obj.dry_run:
            click.echo(f"Keys: {describe(keyboard.light_state())}")


@main.command()
@pass_context
def off(obj):
    """Turn the key lighting off (stored in flash)."""
    with obj.open() as keyboard:
        apply_light(keyboard, protocol.EFFECTS_BY_NAME["off"], None, None, None, False)


@main.command()
@click.argument("effect", type=click.Choice([effect.name for effect in protocol.SIDE_EFFECTS]))
@light_options
@pass_context
def side(obj, effect, speed, brightness, color, rainbow):
    """Set the side light (stored in flash)."""
    with obj.open() as keyboard:
        if not keyboard.model.side_light:
            raise click.ClickException(f"{keyboard.model.name} has no known side light")
        changed = apply_light(keyboard, protocol.EFFECTS_BY_NAME[effect], speed, brightness, color, rainbow, side=True)
        if changed and not obj.dry_run:
            click.echo(f"Side: {describe(keyboard.light_state(side=True), side=True)}")


@main.command()
@click.argument("color", required=False, callback=color_argument)
@click.option("--fade", type=float, metavar="SECONDS", help="Cycle through all hues for this long.")
@click.option("--stdin", "from_stdin", is_flag=True, help="Read one color per line from standard input.")
@pass_context
def live(obj, color, fade, from_stdin):
    """Set one color for all keys, without writing flash.

    Switching into live mode is one flash write the first time; after that,
    colors can change as often as wanted. The color is lost when the
    keyboard is unplugged.
    """
    if sum(option is not None and option is not False for option in (color, fade, from_stdin)) != 1:
        raise click.UsageError("give exactly one of COLOR, --fade or --stdin")

    with obj.open() as keyboard:
        keyboard.set_light(protocol.EFFECT_LIVE, 0, protocol.BRIGHTNESS_MAX, protocol.OPTION_USER_COLOR, (0, 0, 0))

        if color is not None:
            keyboard.live_color(color)
        elif fade is not None:
            start = time.time()
            while time.time() - start < fade:
                hue = ((time.time() - start) / 5.0) % 1.0
                keyboard.live_color(tuple(int(c * 255) for c in colorsys.hsv_to_rgb(hue, 1.0, 1.0)))
                time.sleep(1 / 30)
        else:
            for line in sys.stdin:
                if line.strip():
                    try:
                        keyboard.live_color(protocol.parse_color(line))
                    except ValueError as error:
                        click.echo(f"skipped: {error}", err=True)


@main.group()
def custom():
    """Per-key colors of the custom effect (stored in flash)."""


def picture_colors(keyboard, picture):
    return {key: tuple(picture[slot * 3:slot * 3 + 3]) for key, slot in keyboard.model.keys.items()}


def write_picture(obj, keyboard, picture, changed_slots, activate):
    if changed_slots and len(changed_slots) <= 3:
        for slot in changed_slots:
            keyboard.set_picture_key(slot, tuple(picture[slot * 3:slot * 3 + 3]))
    elif changed_slots:
        keyboard.set_picture(picture)
    else:
        obj.log("picture unchanged, nothing written")

    if activate:
        apply_light(keyboard, protocol.EFFECTS_BY_NAME["custom"], None, None, None, False)


@custom.command("show")
@click.option("--all", "show_all", is_flag=True, help="Also show keys that are off.")
@click.option("--json", "as_json", is_flag=True, help="Print JSON (the format 'custom load' reads).")
@pass_context
def custom_show(obj, show_all, as_json):
    """Show the stored custom picture."""
    with obj.open() as keyboard:
        colors = picture_colors(keyboard, keyboard.picture())
    if as_json:
        click.echo(json.dumps({key: protocol.format_color(color) for key, color in colors.items()}, indent=2))
        return
    for key, color in colors.items():
        if show_all or any(color):
            click.echo(f"{key:20} {protocol.format_color(color)}")


@custom.command("set")
@click.argument("assignments", nargs=-1, required=True, metavar="KEY=COLOR...")
@click.option("--activate", is_flag=True, help="Also switch the keys to the custom effect.")
@pass_context
def custom_set(obj, assignments, activate):
    """Change keys of the custom picture, e.g. 'escape=red w=00ff00'.

    The key 'all' sets every key. 'kovarctl keys' lists the key names.
    """
    with obj.open() as keyboard:
        picture = keyboard.picture()
        changed = set()
        for assignment in assignments:
            key, _, value = assignment.partition("=")
            try:
                color = protocol.parse_color(value)
            except ValueError as error:
                raise click.BadParameter(str(error), param_hint=assignment)
            if key == "all":
                slots = list(keyboard.model.keys.values())
            elif key in keyboard.model.keys:
                slots = [keyboard.model.keys[key]]
            else:
                raise click.BadParameter(f"unknown key {key!r}, see 'kovarctl keys'", param_hint=assignment)
            for slot in slots:
                if tuple(picture[slot * 3:slot * 3 + 3]) != color:
                    picture[slot * 3:slot * 3 + 3] = bytes(color)
                    changed.add(slot)
        write_picture(obj, keyboard, picture, sorted(changed), activate)


@custom.command("save")
@click.argument("file", type=click.File("w"))
@pass_context
def custom_save(obj, file):
    """Save the custom picture as JSON."""
    with obj.open() as keyboard:
        colors = picture_colors(keyboard, keyboard.picture())
    json.dump({key: protocol.format_color(color) for key, color in colors.items()}, file, indent=2)
    file.write("\n")


@custom.command("load")
@click.argument("file", type=click.File("r"))
@click.option("--activate", is_flag=True, help="Also switch the keys to the custom effect.")
@pass_context
def custom_load(obj, file, activate):
    """Load a custom picture from JSON ({"escape": "#FF0000", ...}).

    Keys missing in the file keep their color.
    """
    try:
        data = json.load(file)
    except ValueError as error:
        raise click.ClickException(f"{file.name}: {error}")
    with obj.open() as keyboard:
        picture = keyboard.picture()
        changed = set()
        for key, value in data.items():
            if key not in keyboard.model.keys:
                raise click.ClickException(f"{file.name}: unknown key {key!r}")
            slot = keyboard.model.keys[key]
            color = protocol.parse_color(value)
            if tuple(picture[slot * 3:slot * 3 + 3]) != color:
                picture[slot * 3:slot * 3 + 3] = bytes(color)
                changed.add(slot)
        write_picture(obj, keyboard, picture, sorted(changed), activate)


@main.command()
@pass_context
def recover(obj):
    """Reset the keyboard if its lighting interface stopped answering."""
    with obj.open(probe=False) as keyboard:
        if obj.dry_run:
            click.echo(f"would reset the keyboard at {keyboard.path}")
            return
        keyboard.reset()
    time.sleep(3)
    paths = find_vendor_interfaces()
    for path in paths:
        try:
            with Keyboard(path) as keyboard:
                click.echo(f"{path}: answers again, model ID {keyboard.device_id}")
        except KeyboardError as error:
            click.echo(f"{path}: {error}")
