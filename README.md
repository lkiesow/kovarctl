# kovarctl

Command line tool for the lighting of KOVAR keyboards (ROYUAN YC500 firmware, USB ID `3151:4010`) on Linux.

Supported models, identified by the firmware model ID:

| ID  | Model                       | Tested |
|-----|-----------------------------|--------|
| 738 | KOVAR                       | yes    |
| 723 | KOVAR Wireless (USB cable)  | yes    |
| 737 | KOVAR TKL                   | no     |
| 736 | KOVAR 60                    | no     |

The 2.4 GHz dongle (`3151:4011`) is not supported, because it uses a different protocol. Connect the keyboard with a USB cable instead.

## Installation

```sh
pip install --user .
```

The only dependency is [click](https://click.palletsprojects.com/). Your user needs access to the keyboard's `/dev/hidraw*` nodes. Desktop sessions usually grant it, and the OpenRGB udev rules do too.

## Usage

```sh
kovarctl list                                    # connected keyboards
kovarctl info                                    # model, firmware, current lighting
kovarctl effects                                 # effects and their options
kovarctl mode wave --direction left --color 00ffff
kovarctl mode breathing --speed 3                # options not given stay as they are
kovarctl mode static --rainbow
kovarctl off
kovarctl side breathing --color red              # side light
kovarctl live ff8000                             # one color for all keys, no flash write
kovarctl live --fade 30                          # rainbow fade for 30 s
some-program | kovarctl live --stdin             # one color per line
kovarctl keys                                    # key names
kovarctl custom set escape=red w=green --activate
kovarctl custom set all=000040
kovarctl custom save picture.json
kovarctl custom load picture.json --activate
kovarctl recover                                 # if the keyboard stopped answering
```

Use `--dry-run` to see what would be written, `-v` to report flash writes, and `--device` or `--id` to choose a keyboard when several are connected.

## Keyboard flash

The keyboard stores effect settings, the side light and the custom picture in its flash memory. Each write keeps the firmware busy for up to 1.4 seconds. During that time the keyboard ignores requests and can even lock up its lighting interface. kovarctl protects the keyboard in four ways:

- It reads the current state first and skips writes that change nothing.
- It waits until the keyboard is idle before sending anything, including across separate kovarctl calls.
- It refuses more than 12 flash writes per minute unless you pass `--force`.
- It refuses to run while another program, such as OpenRGB, has the keyboard open.

For colors that change often, use `live`. Only switching into live mode is a flash write; after that, colors can change as often as you like.

The blue LED between Esc and F1 cannot be controlled.

## Development

```sh
python3 -m pytest
```

## License

MIT, see [LICENSE](LICENSE).
