# pyESS.py - Definitive Version (Right-stick Z/ZRotation support + auto-detect)
# Real-time controller remapper to provide an N64-style feel on a modern PC controller.
# Implements a three-zone response curve with octagonal shaping.

import math
import sys
import time
import pygame
import vgamepad as vg

# ----------------- CONFIG -----------------
DEVICE_INDEX = 0      # Which physical controller (0-based)
HZ = 250              # Update frequency (recommended 250)
DEBUG_PRINT_AXES = False   # Print raw axes periodically for debugging
DEBUG_PRINT_INTERVAL_S = 1.0

# Stick curve / ESS
DEADZONE_RADIUS  = 0.01
ESS_INPUT_START  = 0.011
ESS_INPUT_END    = 0.35
ESS_OUTPUT_START = 0.11  # 9 / 84
ESS_OUTPUT_END   = 0.30  # 28.5 / 84

# N64-style octagon shaping: 1.0 circle … lower => pulled-in diagonals (0.9 ≈ authentic)
OCTAGON_DIAGONAL = 0.90

# ----------------- AXIS MAPPING STRATEGY -----------------
# Many DirectInput pads expose the *right stick* as Z Axis (RSX) and Z Rotation (RSY).
# Choose one of: "RX_RY" (classic), "Z_RZ" (DirectInput style), or "AUTO" (prompted detection).
RIGHT_STICK_SOURCE = "Z_RZ"   # <-- set to "Z_RZ" for your new controller per screenshot

# If your device in pygame reports axes as:
#   0 -> X, 1 -> Y, 2 -> Z, 3 -> RZ, 4 -> ???, 5 -> ???
# then Z_RZ maps RSX=2, RSY=3 by default below.
DEFAULT_AXES_BY_SOURCE = {
    "RX_RY": {"LX": 0, "LY": 1, "RX": 2, "RY": 3, "LT": 4, "RT": 5},
    "Z_RZ":  {"LX": 0, "LY": 1, "RX": 2, "RY": 3, "LT": 4, "RT": 5},  # RS uses Z(2)/RZ(3)
}

# Invert Y axes commonly
INVERT_DEFAULT = {"LY": True, "RY": True}

# Button passthrough map (adjust if needed)
BUTTON_MAP = {
    0: vg.XUSB_BUTTON.XUSB_GAMEPAD_A, 1: vg.XUSB_BUTTON.XUSB_GAMEPAD_B,
    2: vg.XUSB_BUTTON.XUSB_GAMEPAD_X, 3: vg.XUSB_BUTTON.XUSB_GAMEPAD_Y,
    4: vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER, 5: vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER,
    6: vg.XUSB_BUTTON.XUSB_GAMEPAD_BACK, 7: vg.XUSB_BUTTON.XUSB_GAMEPAD_START,
    8: vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_THUMB, 9: vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_THUMB,
}

# ----------------- HELPERS -----------------
def clamp(v, lo, hi): return max(lo, min(hi, v))
def scale_axis_to_i16(v): return int(clamp(v, -1.0, 1.0) * 32767)

def axis_safe(js, idx, default=0.0):
    try:
        return js.get_axis(idx)
    except Exception:
        return default

def get_axis(js, axes_map, name, invert_map):
    v = axis_safe(js, axes_map[name], 0.0)
    if invert_map.get(name, False): v = -v
    return v

def snapshot_axes(js):
    return [axis_safe(js, i, 0.0) for i in range(js.get_numaxes())]

def biggest_delta_axis(prev, curr, ignore=set()):
    """Return (axis_index, abs_delta) of axis with largest change, excluding ignore."""
    idx, best = -1, 0.0
    for i, (p, c) in enumerate(zip(prev, curr)):
        if i in ignore: 
            continue
        d = abs(c - p)
        if d > best:
            best, idx = d, i
    return idx, best

def print_axes_once(js):
    vals = snapshot_axes(js)
    print(f" Raw axes ({len(vals)}): " + ", ".join(f"{i}:{v:+.3f}" for i, v in enumerate(vals)))

# ----------------- CORE REMAPPING LOGIC -----------------
def remap_stick(x, y):
    """Three-zone remap + octagonal reshaping."""
    raw_radius = math.hypot(x, y)
    if raw_radius < DEADZONE_RADIUS:
        return 0.0, 0.0

    new_radius = raw_radius
    if ESS_INPUT_START <= raw_radius <= ESS_INPUT_END:
        input_range = ESS_INPUT_END - ESS_INPUT_START
        if input_range > 1e-6:
            output_range = ESS_OUTPUT_END - ESS_OUTPUT_START
            progress = (raw_radius - ESS_INPUT_START) / input_range
            new_radius = ESS_OUTPUT_START + progress * output_range
    elif raw_radius > ESS_INPUT_END:
        input_range = 1.0 - ESS_INPUT_END
        if input_range > 1e-6:
            output_range = 1.0 - ESS_OUTPUT_END
            progress = (raw_radius - ESS_INPUT_END) / input_range
            new_radius = ESS_OUTPUT_END + progress * output_range

    scale = new_radius / raw_radius if raw_radius > 1e-6 else 0.0
    ox, oy = x * scale, y * scale

    # Octagon shaping
    L1 = abs(ox) + abs(oy)
    L_inf = max(abs(ox), abs(oy))
    if L_inf > 0:
        ratio = L1 / L_inf      # 1 (cardinal) .. 2 (diagonal)
        progress = ratio - 1.0
        final_scale = 1.0 + progress * (OCTAGON_DIAGONAL - 1.0)
        ox *= final_scale
        oy *= final_scale
    return ox, oy

# ----------------- AXIS AUTO-DETECT -----------------
def autodetect_right_stick(js, axes_map, invert_map):
    """
    Interactively detect RSX and RSY by asking user to move right-stick horizontally then vertically.
    We avoid LX/LY indices and reuse any triggers if present.
    """
    print("\n=== Right Stick AUTO detection ===")
    print("When prompted, move the RIGHT STICK fully LEFT/RIGHT for ~2 seconds.")
    time.sleep(0.7)
    baseline = snapshot_axes(js)
    start = time.time()
    while time.time() - start < 2.0:
        pygame.event.pump()
        curr = snapshot_axes(js)
        idx, delta = biggest_delta_axis(baseline, curr, ignore={axes_map['LX'], axes_map['LY']})
        if delta > 0.20:  # strong movement
            axes_map['RX'] = idx
            print(f"  Detected RSX axis = {idx} (Δ≈{delta:.2f})")
            break
    if 'RX' not in axes_map:
        print("  Could not detect RSX reliably; defaulting to Z/RZ profile indices.")
        axes_map['RX'] = DEFAULT_AXES_BY_SOURCE['Z_RZ']['RX']

    print("Now move the RIGHT STICK fully UP/DOWN for ~2 seconds.")
    time.sleep(0.7)
    baseline = snapshot_axes(js)
    start = time.time()
    while time.time() - start < 2.0:
        pygame.event.pump()
        curr = snapshot_axes(js)
        idx, delta = biggest_delta_axis(baseline, curr, ignore={axes_map['LX'], axes_map['LY'], axes_map['RX']})
        if delta > 0.20:
            axes_map['RY'] = idx
            invert_map['RY'] = True  # typical Y invert
            print(f"  Detected RSY axis = {idx} (Δ≈{delta:.2f})")
            break
    if 'RY' not in axes_map:
        print("  Could not detect RSY reliably; defaulting to Z/RZ profile indices.")
        axes_map['RY'] = DEFAULT_AXES_BY_SOURCE['Z_RZ']['RY']
        invert_map['RY'] = True

    print(f"Final right-stick mapping -> RX:{axes_map['RX']}, RY:{axes_map['RY']} (invert Y={invert_map['RY']})\n")
    return axes_map, invert_map

# ----------------- MAIN -----------------
def main():
    pygame.init(); pygame.joystick.init(); pygame.display.init()
    if pygame.joystick.get_count() == 0:
        print("No joystick found."); sys.exit(1)

    js = pygame.joystick.Joystick(DEVICE_INDEX); js.init()
    print(f"Using Controller [{DEVICE_INDEX}]: {js.get_name()}")
    print(f"Axes reported by pygame: {js.get_numaxes()} | Buttons: {js.get_numbuttons()} | Hats: {js.get_numhats()}")

    # Build initial axis map from profile
    source = RIGHT_STICK_SOURCE.upper()
    if source not in ("RX_RY", "Z_RZ", "AUTO"):
        source = "Z_RZ"

    axes_map = dict(DEFAULT_AXES_BY_SOURCE["Z_RZ"]) if source == "Z_RZ" else dict(DEFAULT_AXES_BY_SOURCE["RX_RY"])
    invert_map = dict(INVERT_DEFAULT)

    # Ensure left stick is sensible even if device is odd
    axes_map['LX'] = 0
    axes_map['LY'] = 1

    if source == "AUTO":
        axes_map, invert_map = autodetect_right_stick(js, axes_map, invert_map)
    else:
        print(f"Right-stick source profile: {source} (RX={axes_map['RX']}, RY={axes_map['RY']})")

    gamepad = vg.VX360Gamepad()
    clock = pygame.time.Clock()
    last_debug = 0.0

    while True:
        for _ in pygame.event.get(): 
            pass

        # Left stick (remapped curve)
        lx = get_axis(js, axes_map, "LX", invert_map)
        ly = get_axis(js, axes_map, "LY", invert_map)
        ox, oy = remap_stick(lx, ly)

        # Right stick passthrough (from RX/RY OR Z/RZ depending on profile / detection)
        rx = get_axis(js, axes_map, "RX", invert_map)
        ry = get_axis(js, axes_map, "RY", invert_map)

        # Triggers: if axes not present, keep at 0
        lt_idx = axes_map.get("LT", None)
        rt_idx = axes_map.get("RT", None)
        lt_raw = axis_safe(js, lt_idx, 0.0) if lt_idx is not None else 0.0
        rt_raw = axis_safe(js, rt_idx, 0.0) if rt_idx is not None else 0.0

        # Combined trigger axis handling (if device exposes only one)
        if lt_idx is not None and rt_idx is not None and lt_idx == rt_idx:
            lt_val = max(0.0, -lt_raw); rt_val = max(0.0, lt_raw)
        else:
            lt_val = (lt_raw + 1.0) / 2.0
            rt_val = (rt_raw + 1.0) / 2.0

        lt_b = int(clamp(lt_val, 0.0, 1.0) * 255)
        rt_b = int(clamp(rt_val, 0.0, 1.0) * 255)

        # Buttons
        btn_states = {BUTTON_MAP[i]: bool(js.get_button(i)) for i in range(js.get_numbuttons()) if BUTTON_MAP.get(i)}

        # D-Pad
        if js.get_numhats() > 0:
            hatx, haty = js.get_hat(0)
            dpad_up, dpad_down, dpad_left, dpad_right = (haty > 0), (haty < 0), (hatx < 0), (hatx > 0)
        else:
            dpad_up = dpad_down = dpad_left = dpad_right = False

        # --- Output to virtual X360 pad ---
        gamepad.left_joystick(scale_axis_to_i16(ox), scale_axis_to_i16(oy))
        gamepad.right_joystick(scale_axis_to_i16(rx), scale_axis_to_i16(ry))
        gamepad.left_trigger(lt_b); gamepad.right_trigger(rt_b)

        for bconst, is_pressed in btn_states.items():
            (gamepad.press_button if is_pressed else gamepad.release_button)(bconst)

        (gamepad.press_button if dpad_up else gamepad.release_button)(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP)
        (gamepad.press_button if dpad_down else gamepad.release_button)(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN)
        (gamepad.press_button if dpad_left else gamepad.release_button)(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT)
        (gamepad.press_button if dpad_right else gamepad.release_button)(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT)

        gamepad.update()
        clock.tick(HZ)

        if DEBUG_PRINT_AXES and (time.time() - last_debug) >= DEBUG_PRINT_INTERVAL_S:
            last_debug = time.time()
            print_axes_once(js)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nExiting.")
    except Exception as e:
        print(f"\nerror occurred: {e}")
