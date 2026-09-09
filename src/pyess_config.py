"""Zone/shaping config loader for pyESS_app.

Reads pyESS_zones.json, falling back to built-in defaults so the app always runs.
The "shaping" block is shared by both output targets, which is what keeps them
mirrored; per-target keys live under "targets".
"""

import json
import os
import sys

from pyess_shaping import ess_output_band

CONFIG_FILENAME = "pyESS_zones.json"          # shipped defaults; never written to
USER_CONFIG_FILENAME = "pyESS_zones.local.json"   # your tuning; what Save writes

# Built-in fallback (kept identical to the shipped pyESS_zones.json "shaping" block).
DEFAULT_SHAPING = {
    "deadzone": 0.088,
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

# Dropped on load and on save so old configs stop carrying dead settings.
RETIRED_SHAPING_KEYS = ("ess_input_start", "ess_input_end", "ess_enable")

# GUI radio state. Kept in its own file - pyESS_zones.json is tracked and ships in
# the release zip, so a radio click there would become every downloader's default.
SELECTED_TARGET_KEY = "selected_target"
PREFS_FILENAME = "pyESS_prefs.json"
UI_TARGETS = ("pc", "dolphin")
DEFAULT_UI_TARGET = "dolphin"


def _base_dir():
    """Where the config lives: beside the .exe when frozen, else the repo root.

    Must not be the bundle dir when frozen - PyInstaller unpacks to a temp dir that
    is deleted on exit, so saves would be silently discarded.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _config_path():
    return os.path.join(_base_dir(), CONFIG_FILENAME)


def _prefs_path():
    return os.path.join(_base_dir(), PREFS_FILENAME)


def _user_config_path():
    return os.path.join(_base_dir(), USER_CONFIG_FILENAME)


def config_target(ui_target):
    """Map a GUI radio value ("pc"/"dolphin") to a config target key ("soh"/"dolphin")."""
    return "dolphin" if ui_target == "dolphin" else "soh"


_NUMERIC = ("deadzone", "ess_zone_size", "octagon_cardinal", "octagon_diagonal",
            "max_axis_range", "gate_compensation", "input_lag_ms",
            "soh_deadzone", "soh_sensitivity")


def _coerce(cfg, warn):
    """Force numeric settings to floats; uncoercible values fall back to defaults.

    The config is hand-editable, so a quoted number or a null is plausible and used
    to crash startup with no GUI and no message.
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
    mar = cfg.get("max_axis_range")
    if mar is not None and not (mar > 0.0):
        warn(f"max_axis_range {mar} must be > 0 - using 85.0")
        cfg["max_axis_range"] = 85.0
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
    file_shaping = {}          # exactly what the JSON supplied, for the migrations

    # Two layers: the SHIPPED defaults, then the user's own tuning on top. Save only
    # ever writes the second, so a developer's personal settings cannot end up as the
    # default in the release zip - and the shipped baseline stays readable and
    # restorable by deleting one file.
    for path, is_user in ((_config_path(), False), (_user_config_path(), True)):
        if not os.path.isfile(path):
            if not is_user:
                warn(f"{CONFIG_FILENAME} not found; using built-in defaults")
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if not isinstance(raw, dict):
                raise ValueError("not a JSON object")
            layer = {k: v for k, v in (raw.get("shaping") or {}).items()
                     if not k.startswith("_")}
            file_shaping.update(layer)
            cfg.update(layer)
            tgt = (raw.get("targets") or {}).get(target) or {}
            for k, v in tgt.items():
                if not k.startswith("_"):
                    cfg[k] = v
            source = os.path.basename(path) if not is_user else (
                f"{CONFIG_FILENAME} + {USER_CONFIG_FILENAME}")
        except Exception as e:
            warn(f"could not read {os.path.basename(path)} ({e}); ignoring it")

    # Migrations must read file_shaping, not cfg: cfg is seeded from DEFAULT_SHAPING,
    # so any "key absent?" test against it is answered by the default and never fires.
    if "ess_zone_size" not in file_shaping and "ess_input_end" in file_shaping:
        cfg["ess_zone_size"] = file_shaping["ess_input_end"]
        warn(f"migrated ess_input_end={file_shaping['ess_input_end']} to ess_zone_size")
    # ess_enable=false was a remap bypass; ess_zone_size=0 is the same passthrough.
    if file_shaping.get("ess_enable") is False:
        cfg["ess_zone_size"] = 0.0
        warn("ess_enable=false is retired; carried over as ess_zone_size=0 "
             "(same passthrough). Raise ess_zone_size to enable ESS shaping.")
    for _k in RETIRED_SHAPING_KEYS:
        cfg.pop(_k, None)

    cfg["_target"] = target
    # Coerce FIRST. _coerce exists so a hand-edited value cannot crash startup, but
    # ess_output_band divides by max_axis_range - running it first meant "85", 0 or
    # null raised TypeError/ZeroDivisionError before the guard ever ran, killing the
    # app with no window at all in the windowed build.
    cfg = _coerce(cfg, warn)
    cfg = _validate(cfg, warn)

    # The output band is fixed by the game; re-derive it so an old or hand-edited
    # value cannot bring back the dead-diagonal corners.
    cfg["ess_output_start"], cfg["ess_output_end"] = ess_output_band(
        cfg.get("max_axis_range", 85.0))
    cfg["_source"] = source
    cfg["_target"] = target
    return cfg


SHAPING_KEYS = tuple(k for k in DEFAULT_SHAPING
                     if k not in ("ess_output_start", "ess_output_end"))


def _write_json_atomic(path, data):
    """Write via a temp file, removing the temp on failure so no orphan is left."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return path


def save_zones(cfg, target=None):
    """Write `cfg`'s shaping values back to the config, preserving everything else.

    Also writes target-specific keys when `target` is given. Raises on failure so
    the GUI can surface it.
    """
    path = _user_config_path()
    raw = {}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except Exception as e:
            # Never fall back to {}: that rewrites the file from defaults and destroys
            # _README and the other target's block on a config being hand-edited.
            raise ValueError(
                f"{USER_CONFIG_FILENAME} exists but will not parse ({e}). Fix or delete "
                f"it first, or saving would overwrite your other settings.") from e
    if not isinstance(raw, dict):
        raise ValueError(f"{USER_CONFIG_FILENAME} must contain a JSON object")

    # Whitelist, not a denylist: retiring a key is then a one-line deletion from
    # DEFAULT_SHAPING. `_`-prefixed entries are comments and pass through.
    old_shaping = raw.get("shaping") or {}
    shaping = {k: v for k, v in old_shaping.items() if k.startswith("_")}
    for k in SHAPING_KEYS:
        if k in cfg:
            shaping[k] = cfg[k]
        elif k in old_shaping:
            shaping[k] = old_shaping[k]
    raw["shaping"] = shaping

    if target:
        targets = raw.get("targets") or {}
        tgt = targets.get(target) or {}
        for k in TARGET_KEYS:
            if k in cfg:
                tgt[k] = cfg[k]
        targets[target] = tgt
        raw["targets"] = targets

    return _write_json_atomic(path, raw)


def load_prefs():
    """Whole prefs dict, or {} if missing or unreadable."""
    path = _prefs_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def save_prefs(**values):
    """Merge `values` into the prefs file.

    Returns the path, or None if the file exists but will not parse - a hand-edit in
    progress must not be clobbered to store a UI setting. Raises OSError on write
    failure. Not routed through save_zones: prefs save on change while tuning values
    stay behind the Save button, so a shared writer would persist unsaved slider edits.
    """
    path = _prefs_path()
    raw = {}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except Exception:
            return None
        if not isinstance(raw, dict):
            return None
    raw.update(values)
    return _write_json_atomic(path, raw)


def load_selected_target():
    """Last selected output target, or DEFAULT_UI_TARGET if missing or unreadable.

    Unrecognised values warn: this takes "pc"/"dolphin" while the config's `targets`
    block is keyed "soh"/"dolphin", so "soh" is an easy hand-edit to get wrong.
    """
    val = load_prefs().get(SELECTED_TARGET_KEY)
    if val in UI_TARGETS:
        return val
    if val is not None:
        print(f"[pyess_config] WARNING: {SELECTED_TARGET_KEY}={val!r} is not one of "
              f"{UI_TARGETS} - using {DEFAULT_UI_TARGET}", file=sys.stderr)
    return DEFAULT_UI_TARGET


def save_selected_target(target):
    """Persist the radio selection; returns the path, or None if prefs will not parse."""
    if target not in UI_TARGETS:
        raise ValueError(f"unknown target {target!r}; expected one of {UI_TARGETS}")
    return save_prefs(**{SELECTED_TARGET_KEY: target})

