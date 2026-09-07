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

# Keys that used to live in the "shaping" block and no longer do. Dropped both on
# load and on save, so an old config stops carrying dead settings that still look
# meaningful. ess_enable is here because the ESS remap is unconditional now.
RETIRED_SHAPING_KEYS = ("ess_input_start", "ess_input_end", "ess_enable")

# The GUI's output-target radio. Values are the GUI's own keys; "pc" maps to the
# "soh" target on disk - see config_target().
SELECTED_TARGET_KEY = "selected_target"
# Per-user UI state lives in its OWN file. It must not go in pyESS_zones.json:
# that file is tracked and copied verbatim into the release zip, so a radio click
# by whoever cut the build would become every downloader's default. Keeping it
# separate also stops a first click creating a one-key pyESS_zones.json that
# silently masks the "config not found" warning.
PREFS_FILENAME = "pyESS_prefs.json"
UI_TARGETS = ("pc", "dolphin")
DEFAULT_UI_TARGET = "dolphin"


def _base_dir():
    """Where pyESS_zones.json lives.

    Running from source that is simply this file's directory. In a PyInstaller build
    it must NOT be: the modules are unpacked inside the bundle (a temp _MEIxxxx dir for
    one-file builds, which is deleted on exit), so saving there would silently discard
    every change. Resolve next to the .exe instead, which is writable and persistent.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    # Modules live in src/; the config sits one level up beside pyess.bat so it is
    # findable without digging through source.
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _config_path():
    return os.path.join(_base_dir(), CONFIG_FILENAME)


def _prefs_path():
    return os.path.join(_base_dir(), PREFS_FILENAME)


def config_target(ui_target):
    """Map a GUI radio value ("pc"/"dolphin") to a config target key.

    The two vocabularies differ because the config predates the GUI: "pc" in the
    radio is the "soh" block on disk. This was written out by hand at five call
    sites; one helper means adding a target touches one place.
    """
    return "dolphin" if ui_target == "dolphin" else "soh"


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

    path = _config_path()
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            file_shaping = {k: v for k, v in (raw.get("shaping") or {}).items()
                            if not k.startswith("_")}
            cfg.update(file_shaping)
            tgt = (raw.get("targets") or {}).get(target) or {}
            for k, v in tgt.items():
                if not k.startswith("_"):
                    cfg[k] = v
            source = CONFIG_FILENAME
        except Exception as e:
            warn(f"could not read {CONFIG_FILENAME} ({e}); using built-in defaults")
    else:
        warn(f"{CONFIG_FILENAME} not found; using built-in defaults")

    # Migrations read `file_shaping`, NOT cfg. cfg is seeded from DEFAULT_SHAPING, so
    # every "is this key absent?" test against it is answered by the default and the
    # migration never fires - which silently replaced a tuned ess_input_end with the
    # built-in 0.35 for every pre-migration config.
    if "ess_zone_size" not in file_shaping and "ess_input_end" in file_shaping:
        cfg["ess_zone_size"] = file_shaping["ess_input_end"]
        warn(f"migrated ess_input_end={file_shaping['ess_input_end']} to ess_zone_size")
    # ess_enable=false was an ESS-remap bypass. ess_zone_size=0 is the exact same
    # passthrough (verified bit-identical), so carry the intent across rather than
    # silently switching shaping back on for someone who had turned it off.
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
    """Write `data` to `path` via a temp file, removing the temp on any failure.

    Without the cleanup, a failed os.replace - the config carrying the Windows read-only
    attribute is routine after copying a release folder off a share or restoring from
    backup - left a permanent `.tmp` sitting beside the .exe, one per attempt.
    """
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
        except Exception as e:
            # NEVER fall back to {} here. That rewrote the file from defaults and
            # destroyed _README, the other target's whole block and its _note - on a
            # config the user was most likely mid-edit. Raise; on_save shows the error.
            raise ValueError(
                f"{CONFIG_FILENAME} exists but will not parse ({e}). Fix or delete it "
                f"first, or saving would overwrite your other settings.") from e
    if not isinstance(raw, dict):
        raise ValueError(f"{CONFIG_FILENAME} must contain a JSON object")

    # WHITELIST, not a denylist. Projecting over SHAPING_KEYS makes retiring a key a
    # one-line deletion from DEFAULT_SHAPING and nothing else; a hand-maintained list of
    # retired keys is the exact mistake TARGET_KEYS was introduced to stop (see the note
    # in pyESS_app.on_target). `_`-prefixed entries are comments and pass through.
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


def load_selected_target():
    """Which output target the GUI had selected last, from the per-user prefs file.

    Falls back to DEFAULT_UI_TARGET for a missing, unreadable or unrecognised value: a
    bad preference must never be the reason the app will not start. Unrecognised values
    always warn - the config's own `targets` block is keyed "soh"/"dolphin" while this
    takes "pc"/"dolphin", so "soh" is the obvious hand-edit and used to revert in
    silence on every launch.
    """
    path = _prefs_path()
    if not os.path.isfile(path):
        return DEFAULT_UI_TARGET
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        val = raw.get(SELECTED_TARGET_KEY) if isinstance(raw, dict) else None
    except Exception:
        return DEFAULT_UI_TARGET
    if val in UI_TARGETS:
        return val
    if val is not None:
        print(f"[pyess_config] WARNING: {SELECTED_TARGET_KEY}={val!r} is not one of "
              f"{UI_TARGETS} - using {DEFAULT_UI_TARGET}", file=sys.stderr)
    return DEFAULT_UI_TARGET


def save_selected_target(target):
    """Persist the radio selection to the per-user prefs file.

    Deliberately NOT in pyESS_zones.json and not routed through save_zones: the radio is
    remembered the moment it is clicked, while tuning values stay behind the Save button,
    so sharing a writer would silently persist unsaved slider edits too. Keeping it in a
    separate file also stops it shipping inside the release zip.

    Returns the path written, or None if the prefs file exists but could not be parsed.
    Raises OSError if the write itself fails.
    """
    if target not in UI_TARGETS:
        raise ValueError(f"unknown target {target!r}; expected one of {UI_TARGETS}")
    path = _prefs_path()
    raw = {}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except Exception:
            # Exists but will not parse - most likely a hand-edit in progress. Refuse
            # to write: clobbering a file to remember a radio button is a bad trade.
            return None
        if not isinstance(raw, dict):
            return None          # valid JSON but not an object - same refusal
    raw[SELECTED_TARGET_KEY] = target
    return _write_json_atomic(path, raw)

