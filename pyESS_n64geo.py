# pyESS_n64geo.py
# Based on pyESS_wiiclassic.py, but the stick *shaping* uses the intended
# N64 geometry instead of the radial (polar) model:
#   - SQUARE per-axis deadzone   (Cartesian, matches the red box in the reference map)
#   - TRUE octagon gate on output (cardinal + diagonal clamp, matches the green gate)
# The ESS magnitude feature (three-zone compression) is preserved, but it now
# runs *inside* this N64 geometry rather than on a circular deadzone / soft octagon.
#
# Pipeline per frame:
#   raw (x,y)  -> square per-axis deadzone  -> ESS magnitude remap (direction kept)
#             -> octagon gate clamp         -> int16 out to virtual X360 pad.

import math
import sys
import time
import pygame
import vgamepad as vg

# ----------------- CONFIG -----------------
DEVICE_INDEX = 0      # Which physical controller (0-based)
HZ = 250              # Update frequency (recommended 250)
DEBUG_PRINT_AXES = False
DEBUG_PRINT_INTERVAL_S = 1.0

# --- N64 GEOMETRY: square deadzone -------------------------------------------
# Half-size of the SQUARE (per-axis) deadzone box, in normalized units [0..1].
# Real N64/OoT uses a ~7-unit deadzone on an ~80 range => ~0.088.
# The reference map's red box is larger (~0.28 of the gate); set this to ~0.28
# if you want to match that image exactly.
DEADZONE_AXIS = 0.088

# --- ESS magnitude band (unchanged feel from wiiclassic) ---------------------
# A wide input magnitude window is compressed into the narrow ESS output band so
# ESS position is easy to hold. Output band 0.11..0.30 ~= game units 9..25 (/84),
# which brackets the in-game ESS window (16..27).
ESS_INPUT_START  = 0.011   # ESS engages almost immediately past the deadzone
ESS_INPUT_END    = 0.35
ESS_OUTPUT_START = 0.11
ESS_OUTPUT_END   = 0.30

# --- N64 GEOMETRY: octagon gate ----------------------------------------------
# The gate is the convex octagon:  |x|<=CARDINAL, |y|<=CARDINAL, |x|+|y|<=2*DIAGONAL
# CARDINAL = full reach at up/down/left/right; DIAGONAL = per-axis reach at the
# 45-degree corners (gate pull-in). Valid octagon requires CARDINAL/2 < DIAGONAL < CARDINAL.
OCTAGON_CARDINAL = 1.00
OCTAGON_DIAGONAL = 0.70    # N64-ish corner pull-in (~0.70 per axis)

# ----------------- AXIS MAPPING STRATEGY -----------------
# "RX_RY" (classic), "Z_RZ" (DirectInput style), or "AUTO" (prompted detection).
RIGHT_STICK_SOURCE = "Z_RZ"

DEFAULT_AXES_BY_SOURCE = {
    "RX_RY": {"LX": 0, "LY": 1, "RX": 2, "RY": 3, "LT": 4, "RT": 5},
    "Z_RZ":  {"LX": 0, "LY": 1, "RX": 2, "RY": 3, "LT": 4, "RT": 5},
}
INVERT_DEFAULT = {"LY": True, "RY": True}

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

# ----------------- N64-GEOMETRY SHAPING -----------------
def axis_deadzone(v, dz):
    """Square (per-axis) deadzone. Everything with |v|<=dz reads neutral; the
    remaining range [dz,1] is rescaled back to [0,1] so full reach is preserved.
    Applying this independently to X and Y produces the SQUARE dead box."""
    a = abs(v)
    if a <= dz:
        return 0.0
    return math.copysign((a - dz) / (1.0 - dz), v)

def ess_remap_magnitude(m):
    """Three-zone piecewise-linear magnitude remap (the ESS feature).
    m is expected in [0,1]."""
    if m < ESS_INPUT_START:
        return m  # gap: 1:1 passthrough between deadzone and ESS start
    if m <= ESS_INPUT_END:
        p = (m - ESS_INPUT_START) / (ESS_INPUT_END - ESS_INPUT_START)
        return ESS_OUTPUT_START + p * (ESS_OUTPUT_END - ESS_OUTPUT_START)
    p = (m - ESS_INPUT_END) / (1.0 - ESS_INPUT_END)
    return ESS_OUTPUT_END + p * (1.0 - ESS_OUTPUT_END)

def clamp_octagon(x, y):
    """Clamp (x,y) to the N64 octagon gate: cardinal box + diagonal (L1) cut."""
    x = clamp(x, -OCTAGON_CARDINAL, OCTAGON_CARDINAL)
    y = clamp(y, -OCTAGON_CARDINAL, OCTAGON_CARDINAL)
    diag_l1 = 2.0 * OCTAGON_DIAGONAL
    l1 = abs(x) + abs(y)
    if l1 > diag_l1:
        s = diag_l1 / l1
        x *= s
        y *= s
    return x, y

def remap_stick(x, y):
    """N64-geometry shaping: square deadzone -> ESS magnitude remap -> octagon gate."""
    # 1) Square per-axis deadzone (Cartesian) -> the red box in the reference map.
    dx = axis_deadzone(x, DEADZONE_AXIS)
    dy = axis_deadzone(y, DEADZONE_AXIS)
    if dx == 0.0 and dy == 0.0:
        return 0.0, 0.0

    # 2) ESS magnitude remap, direction preserved from the deadzoned vector.
    dmag = math.hypot(dx, dy)
    new_mag = ess_remap_magnitude(min(dmag, 1.0))
    ux, uy = dx / dmag, dy / dmag
    ox, oy = ux * new_mag, uy * new_mag

    # 3) N64 octagon gate -> the green gate in the reference map.
    return clamp_octagon(ox, oy)

# ----------------- AXIS AUTO-DETECT -----------------
def autodetect_right_stick(js, axes_map, invert_map):
    print("\n=== Right Stick AUTO detection ===")
    print("When prompted, move the RIGHT STICK fully LEFT/RIGHT for ~2 seconds.")
    time.sleep(0.7)
    baseline = snapshot_axes(js)
    start = time.time()
    while time.time() - start < 2.0:
        pygame.event.pump()
        curr = snapshot_axes(js)
        idx, delta = biggest_delta_axis(baseline, curr, ignore={axes_map['LX'], axes_map['LY']})
        if delta > 0.20:
            axes_map['RX'] = idx
            print(f"  Detected RSX axis = {idx} (delta~{delta:.2f})")
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
            invert_map['RY'] = True
            print(f"  Detected RSY axis = {idx} (delta~{delta:.2f})")
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
    print(f"Shaping: N64 geometry (square deadzone={DEADZONE_AXIS}, octagon card={OCTAGON_CARDINAL}/diag={OCTAGON_DIAGONAL})")

    source = RIGHT_STICK_SOURCE.upper()
    if source not in ("RX_RY", "Z_RZ", "AUTO"):
        source = "Z_RZ"

    axes_map = dict(DEFAULT_AXES_BY_SOURCE["Z_RZ"]) if source == "Z_RZ" else dict(DEFAULT_AXES_BY_SOURCE["RX_RY"])
    invert_map = dict(INVERT_DEFAULT)
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

        # Left stick (N64-geometry shaping)
        lx = get_axis(js, axes_map, "LX", invert_map)
        ly = get_axis(js, axes_map, "LY", invert_map)
        ox, oy = remap_stick(lx, ly)

        # Right stick passthrough
        rx = get_axis(js, axes_map, "RX", invert_map)
        ry = get_axis(js, axes_map, "RY", invert_map)

        # Triggers
        lt_idx = axes_map.get("LT", None)
        rt_idx = axes_map.get("RT", None)
        lt_raw = axis_safe(js, lt_idx, 0.0) if lt_idx is not None else 0.0
        rt_raw = axis_safe(js, rt_idx, 0.0) if rt_idx is not None else 0.0

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

        # --- Output ---
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
