# pyESS.py - Definitive Version
# A real-time controller remapper to provide an N64-style feel on a modern PC controller.
# Implements a three-zone response curve with octagonal shaping.

import math
import sys
import pygame
import vgamepad as vg

# ----------------- CONFIG -----------------
# --- Core Settings ---
DEVICE_INDEX = 0  # The index of the controller you want to use (0 is usually the first one)
HZ = 250          # Update frequency in Hertz. 250 is recommended for low latency.

# --- Stick Response Curve ---
DEADZONE_RADIUS = 0.1 


ESS_INPUT_START = 0.1
ESS_INPUT_END = 0.5
ESS_OUTPUT_START = 0.1
ESS_OUTPUT_END = 0.25

# --- N64 Shaping ---
# How much to reshape the circular output into an N64-style octagon.
# 1.0 is a perfect circle. A lower value pulls in the corners. 0.9 is authentic.
OCTAGON_DIAGONAL = 0.9

# ----------------- HARDWARE MAPPING -----------------
AXES = {"LX": 0, "LY": 1, "RX": 2, "RY": 3, "LT": 4, "RT": 5}
INVERT = {"LY": True, "RY": True}
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
    try: return js.get_axis(idx)
    except Exception: return default
def get_axis(js, name):
    v = axis_safe(js, AXES[name])
    if INVERT.get(name, False): v = -v
    return v

# ----------------- CORE REMAPPING LOGIC -----------------
def remap_stick(x, y):
    """
    Applies the three-zone remapping and octagonal shaping to the stick coordinates.
    """
    raw_radius = math.hypot(x, y)

    # Zone 0: Deadzone
    if raw_radius < DEADZONE_RADIUS:
        return 0.0, 0.0

    new_radius = raw_radius
    # Zone 1: ESS Boost
    if ESS_INPUT_START <= raw_radius <= ESS_INPUT_END:
        input_range = ESS_INPUT_END - ESS_INPUT_START
        if input_range > 1e-6:
            output_range = ESS_OUTPUT_END - ESS_OUTPUT_START
            progress = (raw_radius - ESS_INPUT_START) / input_range
            new_radius = ESS_OUTPUT_START + progress * output_range
    # Zone 2: Normal Scaling
    elif raw_radius > ESS_INPUT_END:
        input_range = 1.0 - ESS_INPUT_END
        if input_range > 1e-6:
            output_range = 1.0 - ESS_OUTPUT_END
            progress = (raw_radius - ESS_INPUT_END) / input_range
            new_radius = ESS_OUTPUT_END + progress * output_range
    # (The "gap" zone between deadzone and ESS is implicitly a 1-to-1 passthrough)
    
    # Calculate the scaled (x, y) coordinates
    scale = new_radius / raw_radius if raw_radius > 1e-6 else 0.0
    ox, oy = x * scale, y * scale

    # --- Apply Final Octagonal Shape ---
    # This new algorithm correctly reshapes the output while preserving the 1.0 cardinal range.
    L1_norm = abs(ox) + abs(oy)
    L_inf_norm = max(abs(ox), abs(oy))
    
    # If the vector is on a cardinal axis, L1_norm == L_inf_norm.
    # If it's on a diagonal, L1_norm is greater. We use this ratio to interpolate.
    if L_inf_norm > 0:
        ratio = L1_norm / L_inf_norm
        # Map the ratio from its range [1, 2] to the desired output scale [1.0, OCTAGON_DIAGONAL]
        progress = ratio - 1.0
        final_scale = 1.0 + progress * (OCTAGON_DIAGONAL - 1.0)
        ox *= final_scale
        oy *= final_scale
    
    return ox, oy

# ----------------- MAIN -----------------
def main():
    pygame.init(); pygame.joystick.init(); pygame.display.init()
    if pygame.joystick.get_count() == 0: print("No joystick found."); sys.exit(1)
    
    js = pygame.joystick.Joystick(DEVICE_INDEX); js.init()
    print(f"Using Controller [{DEVICE_INDEX}]: {js.get_name()}")
    
    gamepad = vg.VX360Gamepad(); clock = pygame.time.Clock()

    while True:
        for _ in pygame.event.get(): pass

        # --- Left Stick Remapping ---
        lx = get_axis(js, "LX"); ly = get_axis(js, "LY")
        ox, oy = remap_stick(lx, ly)

        # --- Right Stick, Triggers, and Buttons (Passthrough) ---
        rx = get_axis(js, "RX"); ry = get_axis(js, "RY")

        lt_raw = axis_safe(js, AXES["LT"]); rt_raw = axis_safe(js, AXES["RT"])
        if AXES["LT"] == AXES["RT"]: # Combined trigger axis
            lt_val = max(0.0, -lt_raw); rt_val = max(0.0, lt_raw)
        else: # Separate trigger axes
            lt_val = (lt_raw + 1.0) / 2.0; rt_val = (rt_raw + 1.0) / 2.0
        lt_b = int(clamp(lt_val, 0.0, 1.0) * 255); rt_b = int(clamp(rt_val, 0.0, 1.0) * 255)
        
        btn_states = {BUTTON_MAP[i]: bool(js.get_button(i)) for i in range(js.get_numbuttons()) if BUTTON_MAP.get(i)}
        
        if js.get_numhats() > 0:
            hatx, haty = js.get_hat(0); dpad_up, dpad_down, dpad_left, dpad_right = (haty > 0), (haty < 0), (hatx < 0), (hatx > 0)
        else: # D-pad as buttons fallback
            dpad_up, dpad_down, dpad_left, dpad_right = False, False, False, False

        # --- Send all outputs to the virtual gamepad ---
        gamepad.left_joystick(scale_axis_to_i16(ox), scale_axis_to_i16(oy))
        gamepad.right_joystick(scale_axis_to_i16(rx), scale_axis_to_i16(ry))
        gamepad.left_trigger(lt_b); gamepad.right_trigger(rt_b)
        for bconst, is_pressed in btn_states.items():
            (gamepad.press_button if is_pressed else gamepad.release_button)(bconst)
        (gamepad.press_button if dpad_up else gamepad.release_button)(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP)
        (gamepad.press_button if dpad_down else gamepad.release_button)(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN)
        (gamepad.press_button if dpad_left else gamepad.release_button)(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT)
        (gamepad.press_button if dpad_right else gamepad.release_button)(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT)

        gamepad.update(); clock.tick(HZ)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nExiting.")
    except Exception as e:
        print(f"\nAn error occurred: {e}")