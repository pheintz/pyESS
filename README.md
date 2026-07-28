# pyESS

Reshape input to N64 style zone shaping. 
Adjust the ESS band as you see fit. 
Two output modes, SoH / WiiVC out will adjust the output and keep controller consistent across environments.

It reads your controller, applies the shaping, and writes to a virtual Xbox 360 pad that the game reads instead.

![zone map](docs/zonemap.png)

## Requirements

- **Windows**, Python **3.11+** (tested on 3.13.3 / Windows 11)
- `pip install -r requirements.txt`
- **[ViGEmBus](https://github.com/nefarius/ViGEmBus/releases)** — required kernel driver;
  the virtual pad cannot be created without it
- **[HidHide](https://github.com/nefarius/HidHide/releases)** — optional but recommended hides the physical pad so games bind only the virtual one. More important in SoH where two controllers will be bound every time you start up the game with potentially different bindings.

## Running

`pyess.bat`

Pick your output target, tick **Send to virtual pad**, and point the game at the virtual controller.

## Licence

**GPLv3.** `pyess_vc.py` ports the inversion tables and algorithm from
[Skuzee/ESS-Adapter](https://github.com/Skuzee/ESS-Adapter).
