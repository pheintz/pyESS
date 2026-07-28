# pyess_config.py
# Zone/shaping config loader for pyESS_app.py.
#
# Reads pyESS_zones.json from this file's directory. If the file is missing or
# malformed we fall back to the built-in defaults below, so the app always runs.
#
# The "shaping" block is shared by BOTH output targets - that is what keeps SoH and
# Dolphin/WiiVC mirrored. Per-target keys live under "targets" (see TARGET_KEYS).

import json
import os
import sys

from pyess_shaping import ess_output_band

CONFIG_FILENAME = "pyESS_zones.json"

# Built-in fallback (kept identical to the shipped pyESS_zones.json "shaping" block).
DEFAULT_SHAPING = {
    "deadzone": 0.088,
    "ess_enable": True,
    "ess_zone_size": 0.35,
    # derived from the game constants, not user-tunable - see pyess_shaping
    "ess_output_start": ess_output_band()[0],
    "ess_output_end": ess_output_band()[1],
    "octagon_cardinal": 1.00,
    "octagon_diagonal": 0.70,
}
DEFAULT_TARGETS = {
    "soh": {"max_axis_range": 85.0, "input_lag_ms": 0.0,
            "soh_deadzone": 0.0, "soh_sensitivity": 1.0},
    "dolphin": {"max_axis_range": 85.0, "gate_compensation": 1.0},
}

# Per-target (non-shaping) keys that save_zones is allowed to persist.
TARGET_KEYS = ("max_axis_range", "gate_compensation", "input_lag_ms",
               "soh_deadzone", "soh_sensitivity")


def _base_dir():
    """Where pyESS_zones.json lives.

    Running from source that is simply this file's directory. In a PyInstaller build
    it must NOT be: the modules are unpacked inside the bundle (a temp _MEIxxxx dir for
    one-file builds, which is deleted on exit), so saving there would silently discard
    every change. Resolve next to the .exe instead, which is writable and persistent.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _config_path():
    return os.path.join(_base_dir(), CONFIG_FILENAME)


_NUMERIC = ("deadzone", "ess_zone_size", "octagon_cardinal", "octagon_diagonal",
            "max_axis_range", "gate_compensation", "input_lag_ms",
            "soh_deadzone", "soh_sensitivity")


def _coerce(cfg, warn):
    """Force numeric settings to floats before anything compares them.

    pyESS_zones.json is documented as hand-editable, so a quoted number
    ("deadzone": "0.1") or a null is entirely plausible - and used to crash the app on
    startup with a bare TypeError from the range checks below, no GUI, no message.
    Anything uncoercible falls back to the built-in default.
    """
    for key in _NUMERIC:
        if key not in cfg:
            continue
        val = cfg[key]
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            try:
                cfg[key] = float(val)
            except (TypeError, ValueError):
                fallback = DEFAULT_SHAPING.get(
                    key, DEFAULT_TARGETS.get(cfg.get("_target", ""), {}).get(key, 0.0))
                warn(f"{key}={val!r} is not a number - using {fallback}")
                cfg[key] = fallback
        else:
            cfg[key] = float(val)
    if not isinstance(cfg.get("ess_enable", True), bool):
        cfg["ess_enable"] = bool(cfg.get("ess_enable"))
    return cfg


def _validate(cfg, warn):
    """Sanity-check the resolved zones. Warns (does not raise) so a bad edit is loud
    but never leaves you without a working stick."""
    dz = cfg["deadzone"]
    if not (0.0 <= dz < 1.0):
        warn(f"deadzone {dz} outside [0,1) - clamping to 0.0")
        cfg["deadzone"] = 0.0
    if not (0.0 <= cfg["ess_zone_size"] <= 1.0):
        warn(f"ess_zone_size {cfg['ess_zone_size']} outside [0,1] - clamping")
        cfg["ess_zone_size"] = max(0.0, min(1.0, cfg["ess_zone_size"]))
    if cfg["ess_output_start"] > cfg["ess_output_end"]:
        warn("ess_output_start > ess_output_end - swapping")
        cfg["ess_output_start"], cfg["ess_output_end"] = \
            cfg["ess_output_end"], cfg["ess_output_start"]
    card, diag = cfg["octagon_cardinal"], cfg["octagon_diagonal"]
    if not (card / 2.0 < diag < card):
        warn(f"octagon_diagonal {diag} must satisfy {card/2.0} < diag < {card} - "
             f"gate will not be a valid octagon")
    return cfg


def load_zones(target, verbose=True):
    """Return the resolved shaping dict for `target` ('soh' or 'dolphin').

    Merge order: DEFAULT_SHAPING <- json 'shaping' <- DEFAULT_TARGETS[target]
                 <- json 'targets'[target].
    Keys starting with '_' (comments/notes) are ignored.
    """
    def warn(msg):
        if verbose:
            print(f"[pyess_config] WARNING: {msg}", file=sys.stderr)

    cfg = dict(DEFAULT_SHAPING)
    cfg.update(DEFAULT_TARGETS.get(target, {}))
    source = "built-in defaults"

    path = _config_path()
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            for k, v in (raw.get("shaping") or {}).items():
                if not k.startswith("_"):
                    cfg[k] = v
            tgt = (raw.get("targets") or {}).get(target) or {}
            for k, v in tgt.items():
                if not k.startswith("_"):
                    cfg[k] = v
            source = CONFIG_FILENAME
        except Exception as e:
            warn(f"could not read {CONFIG_FILENAME} ({e}); using built-in defaults")
    else:
        warn(f"{CONFIG_FILENAME} not found; using built-in defaults")

    # Migrate the old two-key form. ess_input_start is gone: only 0 was ever correct.
    if "ess_zone_size" not in cfg and "ess_input_end" in cfg:
        cfg["ess_zone_size"] = cfg["ess_input_end"]
    cfg.pop("ess_input_start", None)
    cfg.pop("ess_input_end", None)

    # The output band is fixed by the game; re-derive it so an old or hand-edited
    # value cannot bring back the dead-diagonal corners.
    cfg["ess_output_start"], cfg["ess_output_end"] = ess_output_band(
        cfg.get("max_axis_range", 85.0))

    cfg["_target"] = target
    cfg = _coerce(cfg, warn)
    cfg = _validate(cfg, warn)
    cfg["_source"] = source
    cfg["_target"] = target
    return cfg


SHAPING_KEYS = tuple(k for k in DEFAULT_SHAPING
                     if k not in ("ess_output_start", "ess_output_end"))


def save_zones(cfg, target=None):
    """Write the 'shaping' values from `cfg` back to pyESS_zones.json.

    Preserves _README, other keys, and the whole 'targets' block; only the shared
    'shaping' values are overwritten. If `target` is given, any target-specific keys
    present in `cfg` (e.g. max_axis_range, gate_compensation) are written too.
    Returns the path written. Raises on I/O failure so the GUI can surface it.
    """
    path = _config_path()
    raw = {}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except Exception:
            raw = {}

    shaping = raw.get("shaping") or {}
    for k in SHAPING_KEYS:
        if k in cfg:
            shaping[k] = cfg[k]
    raw["shaping"] = shaping

    if target:
        targets = raw.get("targets") or {}
        tgt = targets.get(target) or {}
        for k in TARGET_KEYS:
            if k in cfg:
                tgt[k] = cfg[k]
        targets[target] = tgt
        raw["targets"] = targets

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(raw, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)   # atomic-ish; never leaves a half-written config
    return path


def describe(cfg):
    """One-line-per-key summary of what actually got loaded (for --selftest)."""
    lines = [f"[zones] target={cfg['_target']}  source={cfg['_source']}"]
    mar = cfg.get("max_axis_range", 85.0)
    lines.append(
        f"[zones] deadzone={cfg['deadzone']}  ess_enable={cfg['ess_enable']}  "
        f"octagon={cfg['octagon_cardinal']}/{cfg['octagon_diagonal']}")
    lines.append(
        f"[zones] ess_input  {cfg['ess_input_start']}..{cfg['ess_input_end']}   "
        f"ess_output {cfg['ess_output_start']}..{cfg['ess_output_end']}"
        f"  (= cur {cfg['ess_output_start']*mar:.1f}..{cfg['ess_output_end']*mar:.1f})")
    if "gate_compensation" in cfg:
        gc = cfg["gate_compensation"]
        lines.append(f"[zones] max_axis_range={mar}  gate_compensation={gc}"
                     f"{'  (no-op)' if gc == 1.0 else '  (ACTIVE)'}")
    else:
        lines.append(f"[zones] max_axis_range={mar}")
    return "\n".join(lines)


if __name__ == "__main__":
    for t in ("soh", "dolphin"):
        print(describe(load_zones(t)))
        print()
