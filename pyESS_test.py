# pyESS_profiles.py — console, profile at startup, with C-axis fallback + diagnostics
#
# Key features:
# - Quick Mapping Wizard (press A/B/L/R/Z/START and C-UP/DOWN/LEFT/RIGHT).
# - Supports C mapped as BUTTONS or as AXES (digital deflections).
# - **Fallback** for 8BitDo/SDL pads: if C mapping is missing, assumes C on axes 4/5 (±).
# - --diag prints raw Buttons/Axes/Hats at a steady rate to verify inputs.
#
# Common commands:
#   python pyESS_profiles.py --device 1 --learn     # run wizard on device 1
#   python pyESS_profiles.py --device 1 -p 2        # use learned mapping, profile 2
#   python pyESS_profiles.py --device 1 --diag      # show raw input stream
#
import math, sys, argparse, json, os, time
import pygame
try:
    import vgamepad as vg
except Exception:
    print("vgamepad is required at runtime. Install via: pip install vgamepad")
    raise

MAP_FILE = os.path.join(os.path.dirname(__file__), "pyESS_device_mappings.json")

# ----------------- CORE / ESS CONFIG -----------------
DEVICE_INDEX = 0
HZ = 250
DEADZONE_RADIUS   = 0.1
ESS_INPUT_START   = 0.1
ESS_INPUT_END     = 0.5
ESS_OUTPUT_START  = 0.1
ESS_OUTPUT_END    = 0.25
OCTAGON_DIAGONAL = 0.9

SHOW_CURRENT_PRESSED_LINE = True
LOG_BUTTON_TRANSITIONS    = True
TRIGGER_THRESHOLD         = 140
C_AXIS_THRESHOLD          = 0.6
C_AXIS_HYSTERESIS         = 0.45

# ----------------- XUSB CONSTANT SHORTCUTS -----------------
def XUSB(alias: str):
    return {
        'A': vg.XUSB_BUTTON.XUSB_GAMEPAD_A,
        'B': vg.XUSB_BUTTON.XUSB_GAMEPAD_B,
        'X': vg.XUSB_BUTTON.XUSB_GAMEPAD_X,
        'Y': vg.XUSB_BUTTON.XUSB_GAMEPAD_Y,
        'LB': vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER,
        'RB': vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER,
        'BACK': vg.XUSB_BUTTON.XUSB_GAMEPAD_BACK,
        'START': vg.XUSB_BUTTON.XUSB_GAMEPAD_START,
        'L3': vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_THUMB,
        'R3': vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_THUMB,
        'DPAD_UP': vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP,
        'DPAD_DOWN': vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN,
        'DPAD_LEFT': vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT,
        'DPAD_RIGHT': vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT,
    }[alias]

# ----------------- SAFE HELPERS -----------------
def clamp(v, lo, hi): return max(lo, min(hi, v))
def scale_axis_to_i16(v): return int(clamp(v, -1.0, 1.0) * 32767)
def axis_safe(js, idx, default=0.0):
    try:
        if idx is None: return default
        return js.get_axis(idx)
    except Exception:
        return default
def btn_safe(js, idx):
    try:
        if idx is None: return False
        n = js.get_numbuttons()
        if idx < 0 or idx >= n: return False
        return bool(js.get_button(idx))
    except Exception:
        return False
def get_axis(js, pconf, name):
    idx = pconf['AXES'].get(name)
    v = axis_safe(js, idx, 0.0)
    if pconf['INVERT'].get(name, False): v = -v
    return v

# ----------------- ESS + OCTAGON -----------------
def remap_stick(x, y):
    raw_radius = math.hypot(x, y)
    if raw_radius < DEADZONE_RADIUS: return 0.0, 0.0
    new_radius = raw_radius
    if ESS_INPUT_START <= raw_radius <= ESS_INPUT_END:
        ir = ESS_INPUT_END - ESS_INPUT_START
        if ir > 1e-6:
            orng = ESS_OUTPUT_END - ESS_OUTPUT_START
            prog = (raw_radius - ESS_INPUT_START) / ir
            new_radius = ESS_OUTPUT_START + prog * orng
    elif raw_radius > ESS_INPUT_END:
        ir = 1.0 - ESS_INPUT_END
        if ir > 1e-6:
            orng = 1.0 - ESS_OUTPUT_END
            prog = (raw_radius - ESS_INPUT_END) / ir
            new_radius = ESS_OUTPUT_END + prog * orng
    scale = new_radius / raw_radius if raw_radius > 1e-6 else 0.0
    ox, oy = x*scale, y*scale
    L1 = abs(ox) + abs(oy); Linf = max(abs(ox), abs(oy))
    if Linf > 0:
        ratio = L1 / Linf; prog = ratio - 1.0
        final_scale = 1.0 + prog * (OCTAGON_DIAGONAL - 1.0)
        ox *= final_scale; oy *= final_scale
    return ox, oy

# ----------------- IO READERS -----------------
def read_triggers(js, pconf):
    t = pconf['TRIGGERS']
    if t['type'] == 'axes':
        lt_raw = get_axis(js, pconf, t['lt'])
        rt_raw = get_axis(js, pconf, t['rt'])
        lt_b = int(clamp((lt_raw + 1.0)/2.0, 0.0, 1.0)*255) if t['lt'] is not None else 0
        rt_b = int(clamp((rt_raw + 1.0)/2.0, 0.0, 1.0)*255) if t['rt'] is not None else 0
    else:
        lt_b = 255 if btn_safe(js, t.get('lt')) else 0
        rt_b = 255 if btn_safe(js, t.get('rt')) else 0
    return lt_b, rt_b

def read_dpad(js, pconf):
    if js.get_numhats() > 0:
        hatx, haty = js.get_hat(0)
        return (haty>0, haty<0, hatx<0, hatx>0)
    m = pconf.get('DPAD_AS_BUTTONS', {}) or {}
    def idx_for(alias):
        return next((i for i,a in m.items() if a==alias), None)
    return (btn_safe(js, idx_for('DPAD_UP')),
            btn_safe(js, idx_for('DPAD_DOWN')),
            btn_safe(js, idx_for('DPAD_LEFT')),
            btn_safe(js, idx_for('DPAD_RIGHT')))

def read_buttons(js, pconf):
    btn_states = {}
    for idx, alias in pconf['BUTTONS'].items():
        btn_states[XUSB(alias)] = btn_safe(js, idx)
    return btn_states

def read_c_buttons(js, pconf):
    if pconf.get('C_AXIS_INDICES'):
        c = {}
        for name, desc in pconf['C_AXIS_INDICES'].items():
            ax = desc['axis']; sign = desc['sign']
            val = axis_safe(js, ax, 0.0)
            if sign > 0: c[name] = val >= C_AXIS_THRESHOLD
            else:        c[name] = val <= -C_AXIS_THRESHOLD
        return c
    if pconf.get('C_BUTTON_INDICES'):
        return {k: btn_safe(js, idx) for k, idx in pconf['C_BUTTON_INDICES'].items()}
    return {'UP': False, 'DOWN': False, 'LEFT': False, 'RIGHT': False}

def apply_c_mode(c, pconf, rx, ry, dpad_tuple, btn_states):
    mode = pconf.get('C_MODE', 'OFF')
    if mode == 'OFF': return rx, ry, dpad_tuple, btn_states
    if mode == 'RIGHT_STICK':
        mag = pconf.get('C_RS_VALUE', 1.0)
        want_x = (-mag if c.get('LEFT') else (mag if c.get('RIGHT') else 0.0))
        want_y = (-mag if c.get('UP')   else (mag if c.get('DOWN')  else 0.0))
        rx = want_x if abs(want_x) > abs(rx) else rx
        ry = want_y if abs(want_y) > abs(ry) else ry
        return rx, ry, dpad_tuple, btn_states
    if mode == 'DPAD':
        up,down,left,right = dpad_tuple
        up   = up   or c.get('UP', False)
        down = down or c.get('DOWN', False)
        left = left or c.get('LEFT', False)
        right= right or c.get('RIGHT', False)
        return rx, ry, (up,down,left,right), btn_states
    if mode == 'ABXY':
        if c.get('UP'):    btn_states[XUSB('Y')] = True
        if c.get('DOWN'):  btn_states[XUSB('A')] = True
        if c.get('LEFT'):  btn_states[XUSB('X')] = True
        if c.get('RIGHT'): btn_states[XUSB('B')] = True
    return rx, ry, dpad_tuple, btn_states

# ----------------- PROFILES (BASELINES) -----------------
BASE_DINPUT = {
    'AXES':   {"LX": 0, "LY": 1, "RX": None, "RY": None, "LT": None, "RT": None},
    'INVERT': {"LY": True, "RY": True},
    'TRIGGERS': {'type': 'buttons', 'lt': None, 'rt': None},
    'BUTTONS': {},
    'DPAD_AS_BUTTONS': {},
    'C_MODE': 'RIGHT_STICK',
    'C_BUTTON_INDICES': {},
    'C_AXIS_INDICES': {},
    'C_RS_VALUE': 1.0
}
PROFILES = {
    'GENERIC_XINPUT': {
        'AXES':   {"LX": 0, "LY": 1, "RX": 2, "RY": 3, "LT": 4, "RT": 5},
        'INVERT': {"LY": True, "RY": True},
        'TRIGGERS': {'type': 'axes', 'lt': 'LT', 'rt': 'RT'},
        'BUTTONS': {0:'A',1:'B',2:'X',3:'Y',4:'LB',5:'RB',6:'BACK',7:'START',8:'L3',9:'R3'},
        'DPAD_AS_BUTTONS': {},
        'C_MODE': 'OFF',
        'C_BUTTON_INDICES': None,
    },
    'N64_SWITCH_DINPUT_RS_C': {},
    'N64_SWITCH_DINPUT_DPAD_C': {},
}
PROFILE_ORDER = ['GENERIC_XINPUT', 'N64_SWITCH_DINPUT_RS_C', 'N64_SWITCH_DINPUT_DPAD_C']

# ----------------- MAPPING STORE -----------------
def load_all_mappings():
    if os.path.isfile(MAP_FILE):
        try:
            with open(MAP_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}
def save_all_mappings(store):
    try:
        with open(MAP_FILE, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=2)
        print(f"[mapper] Saved mapping to {MAP_FILE}")
    except Exception as e:
        print(f"[mapper] Failed to save mapping: {e}")
def device_key(js):
    try: return js.get_name()
    except Exception: return "Unknown_Device"

# ----------------- QUICK MAPPING WIZARD -----------------
def wait_for_new_button(js, already=set(), prompt="Press the button now..."):
    print(prompt); last = set(already)
    while True:
        pygame.event.pump()
        pressed = set(i for i in range(js.get_numbuttons()) if btn_safe(js, i))
        newly = pressed - last
        if newly:
            idx = sorted(list(newly))[0]
            print(f"  Detected button index: {idx}")
            while btn_safe(js, idx): pygame.event.pump(); time.sleep(0.02)
            return idx, pressed
        _ = [js.get_hat(i) for i in range(js.get_numhats())]
        time.sleep(0.01)

def wait_for_c_input(js, already_buttons=set(), prompt="Press the C-button now..."):
    print(prompt); last_btns = set(already_buttons)
    while True:
        pygame.event.pump()
        btns = set(i for i in range(js.get_numbuttons()) if btn_safe(js, i))
        newly = btns - last_btns
        if newly:
            idx = sorted(list(newly))[0]
            print(f"  Detected C as BUTTON index: {idx}")
            while btn_safe(js, idx): pygame.event.pump(); time.sleep(0.02)
            return ('button', idx), btns
        for ax in range(js.get_numaxes()):
            val = js.get_axis(ax)
            if val >= C_AXIS_THRESHOLD:
                print(f"  Detected C as AXIS {ax} POS (+) (value={val:.2f})")
                while js.get_axis(ax) >= C_AXIS_HYSTERESIS: pygame.event.pump(); time.sleep(0.02)
                return ('axis', {'axis': ax, 'sign': +1}), btns
            if val <= -C_AXIS_THRESHOLD:
                print(f"  Detected C as AXIS {ax} NEG (-) (value={val:.2f})")
                while js.get_axis(ax) <= -C_AXIS_HYSTERESIS: pygame.event.pump(); time.sleep(0.02)
                return ('axis', {'axis': ax, 'sign': -1}), btns
        _ = [js.get_hat(i) for i in range(js.get_numhats())]
        time.sleep(0.01)

def run_mapping_wizard(js):
    print("\n=== Quick Mapping Wizard ===")
    print("PRESS and RELEASE when prompted.\n")
    has_hat = js.get_numhats() > 0
    print("DPAD hat:", "yes" if has_hat else "no (we'll map as buttons)")

    mapped = {
        "BUTTONS": {},
        "C_BUTTON_INDICES": {},
        "C_AXIS_INDICES": {},
        "TRIGGERS": {"type": "buttons", "lt": None, "rt": None},
        "DPAD_AS_BUTTONS": {},
    }
    already = set()
    def map_btn(label, alias):
        nonlocal already
        idx, already = wait_for_new_button(js, already, f"Press {label}...")
        mapped["BUTTONS"][idx] = alias

    map_btn("A", "A"); map_btn("B", "B"); map_btn("L", "LB"); map_btn("R", "RB")
    idx_z, already = wait_for_new_button(js, already, "Press Z..."); mapped["TRIGGERS"]["lt"] = idx_z
    idx_start, already = wait_for_new_button(js, already, "Press START..."); mapped["BUTTONS"][idx_start] = "START"
    print("Press BACK/SELECT (or press START again to skip)...")
    idx_maybe, already2 = wait_for_new_button(js, already, "Waiting for BACK/SELECT (or press START to skip)...")
    if idx_maybe != idx_start:
        mapped["BUTTONS"][idx_maybe] = "BACK"; already = already2
        print("  (Mapped BACK)")
    else:
        print("  (Skipped BACK)")
    for cname,label in [("UP","C-UP"),("DOWN","C-DOWN"),("LEFT","C-LEFT"),("RIGHT","C-RIGHT")]:
        res, already = wait_for_c_input(js, already, f"Press {label}...")
        if res[0]=='button': mapped["C_BUTTON_INDICES"][cname] = res[1]
        else:                mapped["C_AXIS_INDICES"][cname]   = res[1]
    if not has_hat:
        for d,alias in [("UP","DPAD_UP"),("DOWN","DPAD_DOWN"),("LEFT","DPAD_LEFT"),("RIGHT","DPAD_RIGHT")]:
            idx_d, already = wait_for_new_button(js, already, f"Press D-Pad {d}...")
            mapped["DPAD_AS_BUTTONS"][idx_d] = alias
    print("\nMapping complete.\n"); return mapped, has_hat

def apply_mapping_to_profiles(js, mapping, has_hat):
    # Build DINPUT profiles from BASE + mapping, with C-axis fallback if needed
    for name, c_mode in [("N64_SWITCH_DINPUT_RS_C", "RIGHT_STICK"),
                         ("N64_SWITCH_DINPUT_DPAD_C", "DPAD")]:
        conf = json.loads(json.dumps(BASE_DINPUT))  # deep copy
        conf["BUTTONS"] = mapping.get("BUTTONS", {})
        conf["TRIGGERS"] = mapping.get("TRIGGERS", {'type':'buttons','lt':None,'rt':None})
        conf["DPAD_AS_BUTTONS"] = {} if has_hat else mapping.get("DPAD_AS_BUTTONS", {})
        conf["C_MODE"] = c_mode
        c_btn = mapping.get("C_BUTTON_INDICES", {}) or {}
        c_ax  = mapping.get("C_AXIS_INDICES", {}) or {}
        if not c_btn and not c_ax:
            # Fallback assume axes 4 (X) and 5 (Y) for C-stick
            if js.get_numaxes() >= 6:
                c_ax = {
                    'LEFT':  {'axis': 4, 'sign': -1},
                    'RIGHT': {'axis': 4, 'sign': +1},
                    'UP':    {'axis': 5, 'sign': -1},
                    'DOWN':  {'axis': 5, 'sign': +1},
                }
                print("[mapper] Using C-axis fallback (axes 4/5, ±).")
            else:
                print("[mapper] No C mapping and not enough axes for fallback.")
        conf["C_BUTTON_INDICES"] = c_btn
        conf["C_AXIS_INDICES"]   = c_ax
        PROFILES[name] = conf

# ----------------- STARTUP / ARGS -----------------
def parse_args():
    p = argparse.ArgumentParser(description="pyESS profiles — console (mapping + diagnostics)")
    p.add_argument("-p", "--profile", help="Profile index (1..3) or name")
    p.add_argument("--device", type=int, default=DEVICE_INDEX, help="Joystick device index (default: %(default)s)")
    p.add_argument("--learn", action="store_true", help="Force run mapping wizard and overwrite saved mapping")
    p.add_argument("--diag", action="store_true", help="Print raw Buttons/Axes/Hats at ~5Hz for debugging")
    return p.parse_args()

def normalize_profile_choice(choice: str):
    if choice is None: return PROFILE_ORDER[0]
    choice = str(choice).strip()
    idx_map = {"1":0,"2":1,"3":2}
    name_map = {"generic_xinput":"GENERIC_XINPUT","xinput":"GENERIC_XINPUT",
                "rs-c":"N64_SWITCH_DINPUT_RS_C","right_stick":"N64_SWITCH_DINPUT_RS_C",
                "dpad-c":"N64_SWITCH_DINPUT_DPAD_C","dpad":"N64_SWITCH_DINPUT_DPAD_C"}
    if choice in idx_map: return PROFILE_ORDER[idx_map[choice]]
    key = choice.lower()
    if key in name_map: return name_map[key]
    if choice in PROFILES: return choice
    print(f"Unrecognized profile '{choice}'. Defaulting to {PROFILE_ORDER[0]}")
    return PROFILE_ORDER[0]

def print_device_summary(js):
    print("---- Device Summary ----")
    try:
        print(f"Name: {js.get_name()}")
        print(f"Axes: {js.get_numaxes()}  Buttons: {js.get_numbuttons()}  Hats: {js.get_numhats()}")
    except Exception:
        pass
    print("------------------------")

# ----------------- MAIN -----------------
def main():
    args = parse_args()
    pygame.init(); pygame.joystick.init()
    if pygame.joystick.get_count()==0:
        print("No joystick found."); sys.exit(1)
    dev_idx = args.device
    if not (0 <= dev_idx < pygame.joystick.get_count()):
        print(f"Device index {dev_idx} out of range (0..{pygame.joystick.get_count()-1})"); sys.exit(1)
    js = pygame.joystick.Joystick(dev_idx); js.init()
    print(f"Using Controller [{dev_idx}]: {js.get_name()}")
    print_device_summary(js)

    # Diagnostics stream (optional)
    if args.diag:
        print("Entering diagnostics stream. Ctrl+C to exit.")
        clock = pygame.time.Clock()
        last_buttons = last_axes = last_hats = None
        try:
            while True:
                pygame.event.pump()
                buttons = [i for i in range(js.get_numbuttons()) if btn_safe(js,i)]
                axes = [round(axis_safe(js,i,0.0),3) for i in range(js.get_numaxes())]
                hats = [js.get_hat(i) for i in range(js.get_numhats())]
                if buttons != last_buttons:
                    print("Buttons:", buttons); last_buttons = buttons
                if axes != last_axes or hats != last_hats:
                    print("Axes:", axes, "Hats:", hats, end="\r"); last_axes, last_hats = axes, hats
                clock.tick(5)
        except KeyboardInterrupt:
            print("\nLeaving diagnostics.")
        return

    # Load or learn mapping
    store = load_all_mappings(); key = device_key(js); dev_map = store.get(key)
    if args.learn or not dev_map:
        mapping, has_hat = run_mapping_wizard(js)
        store[key] = {"mapping": mapping, "has_hat": has_hat}; save_all_mappings(store)
        dev_map = store[key]
    mapping = dev_map["mapping"]; has_hat = dev_map.get("has_hat", js.get_numhats()>0)
    print("[mapper] Loaded mapping:", json.dumps(mapping, indent=2))
    apply_mapping_to_profiles(js, mapping, has_hat)

    selected = normalize_profile_choice(args.profile) if args.profile else None
    if not selected:
        print("Select a profile:\n  1. GENERIC_XINPUT\n  2. N64_SWITCH_DINPUT_RS_C\n  3. N64_SWITCH_DINPUT_DPAD_C")
        raw = input("Enter 1-3 (default 2): ").strip()
        selected = normalize_profile_choice(raw if raw else "2")
    print(f"Starting with profile: {selected}")
    pconf = PROFILES[selected]

    gamepad = vg.VX360Gamepad(); clock = pygame.time.Clock(); last_labels = None
    try:
        while True:
            lx = get_axis(js, pconf, "LX"); ly = get_axis(js, pconf, "LY")
            ox, oy = remap_stick(lx, ly)
            rx = get_axis(js, pconf, "RX"); ry = get_axis(js, pconf, "RY")
            lt_b, rt_b = read_triggers(js, pconf)
            btn_states = read_buttons(js, pconf)
            dpad_up, dpad_down, dpad_left, dpad_right = read_dpad(js, pconf)
            c = read_c_buttons(js, pconf)
            rx, ry, (dpad_up, dpad_down, dpad_left, dpad_right), btn_states = apply_c_mode(
                c, pconf, rx, ry, (dpad_up, dpad_down, dpad_left, dpad_right), btn_states
            )
            labels = set()
            for const,is_pressed in btn_states.items():
                if is_pressed:
                    # reverse-lookup small alias label for display
                    for idx, alias in pconf['BUTTONS'].items():
                        if XUSB(alias)==const: labels.add(alias); break
            if dpad_up: labels.add('DPAD_UP')
            if dpad_down: labels.add('DPAD_DOWN')
            if dpad_left: labels.add('DPAD_LEFT')
            if dpad_right: labels.add('DPAD_RIGHT')
            if c.get('UP'): labels.add('C_UP')
            if c.get('DOWN'): labels.add('C_DOWN')
            if c.get('LEFT'): labels.add('C_LEFT')
            if c.get('RIGHT'): labels.add('C_RIGHT')
            if pconf['TRIGGERS']['type']=='buttons':
                if btn_safe(js, pconf['TRIGGERS'].get('lt')): labels.add('LT')
                if btn_safe(js, pconf['TRIGGERS'].get('rt')): labels.add('RT')
            else:
                if lt_b>TRIGGER_THRESHOLD: labels.add('LT')
                if rt_b>TRIGGER_THRESHOLD: labels.add('RT')
            if labels != last_labels:
                line = ", ".join(sorted(labels)) if labels else "(none)"
                print(f"Pressed: {line}"); last_labels = labels

            gamepad.left_joystick(scale_axis_to_i16(ox), scale_axis_to_i16(oy))
            gamepad.right_joystick(scale_axis_to_i16(rx), scale_axis_to_i16(ry))
            gamepad.left_trigger(lt_b); gamepad.right_trigger(rt_b)
            for alias in ['A','B','X','Y','LB','RB','BACK','START','L3','R3']:
                try: gamepad.release_button(XUSB(alias))
                except Exception: pass
            for const,is_pressed in btn_states.items():
                (gamepad.press_button if is_pressed else gamepad.release_button)(const)
            (gamepad.press_button if dpad_up    else gamepad.release_button)(XUSB('DPAD_UP'))
            (gamepad.press_button if dpad_down  else gamepad.release_button)(XUSB('DPAD_DOWN'))
            (gamepad.press_button if dpad_left  else gamepad.release_button)(XUSB('DPAD_LEFT'))
            (gamepad.press_button if dpad_right else gamepad.release_button)(XUSB('DPAD_RIGHT'))
            gamepad.update(); clock.tick(HZ)
    except KeyboardInterrupt:
        print("\nExiting (Ctrl+C).")
    except Exception as e:
        print(f"\nAn error occurred: {e}")
    finally:
        try: gamepad.reset(); gamepad.update()
        except Exception: pass

if __name__ == "__main__":
    main()
