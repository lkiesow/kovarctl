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
pipx install kovarctl
```

`pip install --user kovarctl` and `uv tool install kovarctl` work as well.

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

## Shell completion

kovarctl can complete commands, effect names and option values such as `--direction`. This needs the installed `kovarctl` command; it does not work with `python3 -m kovarctl`.

**Bash** (version 4.4 or newer), add to `~/.bashrc`:

```sh
eval "$(_KOVARCTL_COMPLETE=bash_source kovarctl)"
```

**Zsh**, add to `~/.zshrc` after `compinit` has run (frameworks like Oh My Zsh run it for you):

```sh
eval "$(_KOVARCTL_COMPLETE=zsh_source kovarctl)"
```

**Fish**, add to `~/.config/fish/config.fish`:

```sh
_KOVARCTL_COMPLETE=fish_source kovarctl | source
```

Open a new shell afterwards. The completion is generated each time a shell starts (about 30 ms), so it always matches the installed version.

## Keyboard flash

The keyboard stores effect settings, the side light and the custom picture in its flash memory. Each write keeps the firmware busy for up to 1.4 seconds. During that time the keyboard ignores requests and can even lock up its lighting interface. kovarctl protects the keyboard in four ways:

- It reads the current state first and skips writes that change nothing.
- It waits until the keyboard is idle before sending anything, including across separate kovarctl calls.
- It refuses more than 12 flash writes per minute unless you pass `--force`.
- It refuses to run while another program, such as OpenRGB, has the keyboard open.

For colors that change often, use `live`. Only switching into live mode is a flash write; after that, colors can change as often as you like.

The blue LED between Esc and F1 cannot be controlled.

## Development

Install from a checkout of the repository and run the tests:

```sh
pip install -e . pytest
python3 -m pytest
```

## License

MIT, see [LICENSE](https://github.com/lkiesow/kovarctl/blob/main/LICENSE).

## Releasing

1. Set the new version in `pyproject.toml`, commit and tag it:

   ```sh
   git commit -am 'Release 0.2.0'
   git tag v0.2.0
   ```

2. Build the packages and check them, in a virtual environment:

   ```sh
   rm -rf dist/
   python3 -m build
   twine check --strict dist/*
   ```

3. Upload to [PyPI](https://pypi.org/project/kovarctl/) and push the tag:

   ```sh
   twine upload dist/*
   git push origin main v0.2.0
   ```
