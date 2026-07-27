# pyESS

Reshapes a modern PC gamepad into **Ocarina of Time**'s stick geometry, so ESS position
is easy to hold and the same physical input produces the **same in-game values** whether
you play on Ship of Harkinian or on the Wii Virtual Console WAD under Dolphin.

It reads your controller, applies the shaping, and writes to a virtual Xbox 360 pad that
the game reads instead.

![zone map](docs/zonemap.png)

## Why it exists

ESS position is a narrow band of stick magnitude (in-game `cur` 8–26) that makes Link
pivot without walking. It is fiddly on a modern round-gate pad, and *much* worse on Wii
VC, which adds its own 15-unit deadzone on top of the game's. pyESS widens that band into
a comfortable plateau and pre-compensates the VC distortion so both targets agree.

## Requirements

- **Windows**, Python **3.11+** (tested on 3.13.3 / Windows 11)
- `pip install -r requirements.txt`
- **[ViGEmBus](https://github.com/nefarius/ViGEmBus/releases)** — required kernel driver;
  the virtual pad cannot be created without it
- **[HidHide](https://github.com/nefarius/HidHide/releases)** — optional but recommended;
  hides the physical pad so games bind only the virtual one

> Whitelist your Python interpreter in HidHide **before** hiding the device, or pyESS
> loses access to the stick too and it looks like the app is broken.

## Running

```
pyess.bat            # or: python pyESS_app.py
python pyESS_app.py --selftest    # headless check, no hardware needed
```

Pick your output target, tick **Send to virtual pad**, and point the game at the virtual
controller.

### Game-side settings

**Ship of Harkinian** — map port 1 to the virtual pad only. SoH takes the *maximum* across
every pad bound to a port, so a physical pad left mapped will silently override the shaping.

**Dolphin (OoT Wii VC WAD)** — GameCube controller in port 1, mapped to the virtual pad.
Set stick **Dead Zone 0**, and **Emulated CPU Clock Override 115%** (fixes OoT-VC's
*framerate* lag in areas like child Market — a different problem from input latency, and
the two get conflated a lot).

## The two controls

Everything else is derived from the game's own constants rather than exposed, because
only one value is ever correct:

| Slider | What it does |
|---|---|
| **Deadzone** | Square per-axis dead box, matching OoT's own shape |
| **ESS zone size** | How much stick past the deadzone holds ESS |

The ESS *output* band, the octagon, and SoH's two output quirks are all computed. See
`pyess_shaping.py` and `pyess_vc.py` — every constant there cites the decomp source it
came from.

## Files

| | |
|---|---|
| `pyESS_app.py` | GUI + the 1 kHz input engine |
| `pyess_shaping.py` | the shaping curve — shared by both targets, which is what keeps them mirrored |
| `pyess_vc.py` | WiiVC inversion tables + a model of OoT's stick handling |
| `pyess_config.py` / `pyESS_zones.json` | settings |

## Accuracy

- SoH's pipeline is modelled from `libultraship ControllerStick::Process` and reproduces
  live PracticeROM readings exactly, on cardinals and diagonals.
- The two targets agree to within **1.4 magnitude units** across a full angle sweep —
  that is the VC quantisation floor, not a tuning error. VC physically cannot produce
  some in-game values (8, 13, 17, 22, 25, …).
- `--selftest` asserts this and fails loudly if a change breaks it.

## Licence

**GPLv3.** `pyess_vc.py` ports the inversion tables and algorithm from
[Skuzee/ESS-Adapter](https://github.com/Skuzee/ESS-Adapter), which is GPLv3, so this
project inherits it. See [LICENSE](LICENSE).
