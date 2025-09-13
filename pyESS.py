import math
import sys
import time
import pygame
import vgamepad as vg

# ----------------- CONFIG -----------------
DEVICE_INDEX = 0
HZ = 250

# Quantization toggles
QUANTIZE_TO_N64 = True
RECLAMP_INT_OCTAGON = True

# ---- N64 geometry ----
N64_RADIUS       = 80.0
N64_RADIUS_DIAG  = 72.0
DIAG_RATIO       = N64_RADIUS_DIAG / N64_RADIUS  # 0.90 (axial 80, diagonal 72)

# ---- Zones in normalized space (1.0 == 80 units) ----
DEAD_R        = 7.0  / 80.0 
ESS_IN_R      = 8.0  / 80.0 
ESS_OUT_R     = 24 / 80.0   

# ESS snap behavior
ESS_DIRECTIONS = 16    
ESS_OUT_MIN    = 0.08
ESS_OUT_MAX    = 0.30

# General
NEUTRAL_EPS    = 0.02 

# SDL/XInput axis indices (adjust if your pad differs)
AXES = {"LX": 0, "LY": 1, "RX": 2, "RY": 3, "LT": 4, "RT": 5}

# Inversion flags (up on LY commonly reads negative)
INVERT = {"LY": True, "RY": True}

# Buttons passthrough (pygame index -> XUSB button)
BUTTON_MAP = {
    0: vg.XUSB_BUTTON.XUSB_GAMEPAD_A,
    1: vg.XUSB_BUTTON.XUSB_GAMEPAD_B,
    2: vg.XUSB_BUTTON.XUSB_GAMEPAD_X,
    3: vg.XUSB_BUTTON.XUSB_GAMEPAD_Y,
    4: vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER,
    5: vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER,
    6: vg.XUSB_BUTTON.XUSB_GAMEPAD_BACK,
    7: vg.XUSB_BUTTON.XUSB_GAMEPAD_START,
    8: vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_THUMB,
    9: vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_THUMB,
}

# If your D-pad is exposed as buttons instead of a hat, set indices here (optional):
DPAD_BUTTONS = {"up": None, "down": None, "left": None, "right": None}

# ----------------- HELPERS -----------------
def clamp(v, lo, hi): return max(lo, min(hi, v))
def scale_axis_to_i16(v): return int(clamp(v, -1.0, 1.0) * 32767)

def to_polar(x, y):
    r = math.hypot(x, y)
    th = math.atan2(y, x)
    return r, th

def to_cart(r, th):
    return r * math.cos(th), r * math.sin(th)

def snap_angle(theta, n_dirs):
    if not n_dirs: return theta
    step = 2 * math.pi / n_dirs
    return round(theta / step) * step

def axis_safe(js, idx, default=0.0):
    try: return js.get_axis(idx)
    except Exception: return default

def get_axis(js, name):
    v = axis_safe(js, AXES[name])
    if INVERT.get(name, False): v = -v
    return v

# ---- Octagon gate clamp (post-mapping) ----
def oct_rmax(theta, diag_ratio=DIAG_RATIO):
    """
    Max radius along angle 'theta' for an octagon where axial radius = 1.0
    and diagonal radius = diag_ratio (e.g., 0.90).
    Constraint: max(|x|, |y|, (|x|+|y|)/S) <= 1 with S = diag_ratio * sqrt(2).
    """
    c, s = abs(math.cos(theta)), abs(math.sin(theta))
    S = diag_ratio * math.sqrt(2.0)
    m = max(c, s, (c + s) / S)
    return 1.0 / max(m, 1e-6)

def clamp_to_octagon(x, y, diag_ratio=DIAG_RATIO):
    r = math.hypot(x, y)
    if r <= 1e-9: return 0.0, 0.0
    theta = math.atan2(y, x)
    rmax = oct_rmax(theta, diag_ratio)
    if r <= rmax: return x, y
    s = rmax / r
    return x * s, y * s

# ---- Integer quantization and optional integer-octagon reclamp ----
def quantize_to_n64_int(ox, oy, reclamp=True):
    """
    Input: normalized ox, oy in [-1, 1] already clamped to float octagon.
    Output: normalized after quantizing to integer N64 units (±80) and optional integer-octagon reclamp.
    """
    qx = int(round(ox * N64_RADIUS))
    qy = int(round(oy * N64_RADIUS))

    if reclamp:
        theta = math.atan2(qy, qx) if (qx or qy) else 0.0
        r = math.hypot(qx, qy)
        rmax_norm = oct_rmax(theta, DIAG_RATIO)
        rmax_int = int(math.floor(rmax_norm * N64_RADIUS + 1e-9))
        if r > rmax_int and r > 0:
            s = rmax_int / r
            qx = int(round(qx * s))
            qy = int(round(qy * s))

    return qx / N64_RADIUS, qy / N64_RADIUS




# ----------------- LEFT STICK (gap-free, no smoothing) -----------------
def map_left_stick_pure(nx, ny):
    def _smoothstep(t): return 3*t*t - 2*t*t*t

    r_raw = math.hypot(nx, ny)
    if r_raw <= DEAD_R:
        return 0.0, 0.0

    theta = math.atan2(ny, nx)

    # ESS band (snap to nearest of 16 directions, fixed output radius band)
    if ESS_IN_R <= r_raw <= ESS_OUT_R:
        t = clamp((r_raw - ESS_IN_R) / max(1e-6, (ESS_OUT_R - ESS_IN_R)), 0.0, 1.0)
        r_out = ESS_OUT_MIN + _smoothstep(t) * (ESS_OUT_MAX - ESS_OUT_MIN)
        th = snap_angle(theta, ESS_DIRECTIONS)
        ox, oy = to_cart(r_out, th)
        ox, oy = clamp_to_octagon(ox, oy, DIAG_RATIO)
        return clamp(ox, -1.0, 1.0), clamp(oy, -1.0, 1.0)

    # Outside ESS: passthrough with N64 octagon enforcement (no walk/run shaping)
    ox, oy = clamp_to_octagon(nx, ny, DIAG_RATIO)
    return clamp(ox, -1.0, 1.0), clamp(oy, -1.0, 1.0)

# ----------------- NEUTRALIZE -----------------
def neutralize_all(gamepad):
    gamepad.left_joystick(0, 0)
    gamepad.right_joystick(0, 0)
    gamepad.left_trigger(0)
    gamepad.right_trigger(0)
    for b in BUTTON_MAP.values():
        gamepad.release_button(b)
    for d in (vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP,
              vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN,
              vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT,
              vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT):
        gamepad.release_button(d)
    gamepad.update()

# ----------------- MAIN -----------------
def main():
    pygame.init()
    pygame.joystick.init()
    pygame.display.init()

    if pygame.joystick.get_count() == 0:
        print("No joystick found.")
        sys.exit(1)
    if DEVICE_INDEX >= pygame.joystick.get_count():
        print(f"DEVICE_INDEX {DEVICE_INDEX} out of {pygame.joystick.get_count()}.")
        sys.exit(1)

    js = pygame.joystick.Joystick(DEVICE_INDEX)
    js.init()
    print(f"Using [{DEVICE_INDEX}]: {js.get_name()}")
    print(f"axes={js.get_numaxes()} buttons={js.get_numbuttons()} hats={js.get_numhats()}")

    gamepad = vg.VX360Gamepad()
    clock = pygame.time.Clock()

    r_prev_dummy = 0.0
    safe_until_ms = 0

    while True:
        for _ in pygame.event.get():
            pass

        # Safe Bind Mode
        if pygame.key.get_pressed()[pygame.K_F9]:
            safe_until_ms = pygame.time.get_ticks() + SAFE_BIND_MS

        # ----- LEFT STICK (gap-free mapping; no smoothing) -----
        lx = get_axis(js, "LX")
        ly = get_axis(js, "LY")
        if math.hypot(lx, ly) <= NEUTRAL_EPS:
            ox, oy = 0.0, 0.0
        else:
            ox, oy = map_left_stick_pure(lx, ly)

        if QUANTIZE_TO_N64:
            ox, oy = quantize_to_n64_int(ox, oy, reclamp=RECLAMP_INT_OCTAGON)

        # ----- RIGHT STICK passthrough (UNCHANGED) -----
        rx = get_axis(js, "RX")
        ry = get_axis(js, "RY")

        # ----- TRIGGERS (adaptive) -----
        lt_raw = axis_safe(js, AXES["LT"])
        rt_raw = axis_safe(js, AXES["RT"])

        if AXES["LT"] == AXES["RT"]:
            # combined trigger axis: value in [-1..1], center ~0
            both = lt_raw  # == rt_raw
            lt_val = max(0.0, -both)  # LT when negative
            rt_val = max(0.0,  both)  # RT when positive
        else:
            # separate axes (either -1..1 or 0..1 depending on device)
            lt_val = (lt_raw + 1.0) / 2.0  # maps -1..1 → 0..1; change to clamp(lt_raw,0,1) if native 0..1
            rt_val = (rt_raw + 1.0) / 2.0

        lt_b = int(clamp(lt_val, 0.0, 1.0) * 255)
        rt_b = int(clamp(rt_val, 0.0, 1.0) * 255)


        # ----- BUTTONS passthrough (unchanged) -----
        btn_states = {BUTTON_MAP[i]: bool(js.get_button(i))
                      for i in range(js.get_numbuttons()) if BUTTON_MAP.get(i)}

        # ----- D-PAD: hat preferred; fallback to buttons if configured -----
        if js.get_numhats() > 0:
            hatx, haty = js.get_hat(0)
            dpad_up    = (haty > 0)
            dpad_down  = (haty < 0)
            dpad_left  = (hatx < 0)
            dpad_right = (hatx > 0)
        else:
            def pressed(idx): return (idx is not None) and bool(js.get_button(idx))
            dpad_up    = pressed(DPAD_BUTTONS["up"])
            dpad_down  = pressed(DPAD_BUTTONS["down"])
            dpad_left  = pressed(DPAD_BUTTONS["left"])
            dpad_right = pressed(DPAD_BUTTONS["right"])

        # ----- Safe Bind Mode -----
        if pygame.time.get_ticks() < safe_until_ms:
            neutralize_all(gamepad)
            clock.tick(HZ)
            continue

        # ----- Send outputs -----
        gamepad.left_joystick(scale_axis_to_i16(ox), scale_axis_to_i16(oy))
        gamepad.right_joystick(scale_axis_to_i16(rx), scale_axis_to_i16(ry))   # RIGHT STICK UNCHANGED
        gamepad.left_trigger(lt_b)
        gamepad.right_trigger(rt_b)

        for bconst, pressed in btn_states.items():
            (gamepad.press_button if pressed else gamepad.release_button)(bconst)

        (gamepad.press_button if dpad_up else gamepad.release_button)(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP)
        (gamepad.press_button if dpad_down else gamepad.release_button)(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN)
        (gamepad.press_button if dpad_left else gamepad.release_button)(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT)
        (gamepad.press_button if dpad_right else gamepad.release_button)(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT)

        gamepad.update()
        clock.tick(HZ)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nExiting.")
