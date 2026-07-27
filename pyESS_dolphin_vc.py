# pyESS_dolphin_vc.py
# Digital-stick compensator for the OoT Wii Virtual Console WAD running in Dolphin.
#
# It pre-applies the INVERSE of VC's stick map so that, after VC mangles the
# input, the game receives N64-accurate stick values (restores ESS + linear feel).
# Pipeline:  physical stick -> N64 intent (octagon) -> INVERT VC -> GC coords 0..255
#            -> virtual X360 pad -> Dolphin (emulated GC controller) -> VC WAD -> OoT
#
# The two inversion tables and the algorithm are ported from Skuzee's ESS-Adapter
# (generate-map.py / ESS.cpp), which is GPLv3. This file is therefore GPLv3.
# VC model constants: extra deadzone=15, length clamp=56, per-axis 1-sqrt(1-x) curve.
#
# DOLPHIN SETUP (critical - we must own the whole curve):
#   * Run the OoT N64 *Virtual Console WAD* (not an N64 core).
#   * Config > GameCube > Port 1 = "Standard Controller", mapped to this virtual pad.
#   * On that GC controller's Main Stick: Dead Zone = 0, Range = 100%,
#     and calibrate so the FULL range/corners pass through (don't let Dolphin
#     clamp the stick to a circle) - otherwise the inverse won't cancel cleanly.
#   * The VC WAD must read the GameCube controller (the path this map inverts).
#
# Run:  python pyESS_dolphin_vc.py            (live remap)
#       python pyESS_dolphin_vc.py --selftest (validate inversion, no controller)

import math
import sys
import time

# ----------------- CONFIG -----------------
DEVICE_INDEX = 0
HZ = 250
DEBUG_PRINT_AXES = False
DEBUG_PRINT_INTERVAL_S = 1.0

# Small square deadzone on the PHYSICAL stick just to kill slop (not the VC deadzone).
PHYS_DEADZONE = 0.06

# N64 intent octagon (what the player is asking the game for).
N64_CARDINAL = 80.0    # full reach at up/down/left/right (OoT range)
N64_DIAGONAL = 70.0    # per-axis reach at the 45-degree corners (N64 gate)

# ESS-band widening (same three-zone scheme as pyESS_wiiclassic.py).
# A wide PHYSICAL input window [ESS_INPUT_START..ESS_INPUT_END] is compressed into a
# narrow N64-magnitude OUTPUT band [ESS_OUTPUT_START..ESS_OUTPUT_END] so ESS position
# is easy to hold. OUTPUT is a FRACTION of N64 range: x N64_CARDINAL(=80) = in-game
# magnitude. NOTE: unlike wiiclassic (which fed a raw virtual N64 stick), this script
# produces true in-game intent, so the band maps DIRECTLY onto the in-game scale.
# In-game ESS window is 16..27  =>  0.20..0.34. The SCHEME mirrors wiiclassic; the
# OUTPUT values are shifted onto the real ESS window (wiiclassic's raw 0.11..0.30 would
# put the lower half below the 16 threshold = dead here). Widen/shift to taste
# (see the shaping table printed by --selftest). Wiiclassic input values reused as-is.
ESS_ENABLE       = True
ESS_INPUT_START  = 0.011   # ESS engages almost immediately past the physical deadzone
ESS_INPUT_END    = 0.35    # top of the wide physical window that lands in the band
ESS_OUTPUT_START = 0.20    # 0.20 * 80 = 16  (bottom of in-game ESS)
ESS_OUTPUT_END   = 0.34    # 0.34 * 80 ~= 27 (top of in-game ESS)

RIGHT_STICK_SOURCE = "Z_RZ"   # "RX_RY" | "Z_RZ" | "AUTO"
DEFAULT_AXES_BY_SOURCE = {
    "RX_RY": {"LX": 0, "LY": 1, "RX": 2, "RY": 3, "LT": 4, "RT": 5},
    "Z_RZ":  {"LX": 0, "LY": 1, "RX": 2, "RY": 3, "LT": 4, "RT": 5},
}
INVERT_DEFAULT = {"LY": True, "RY": True}

# ----------------- VC INVERSION (ported from ESS-Adapter, GPLv3) -----------------
OOT_MAX = 80
BOUNDARY = 39
ONE_DIMENSIONAL_MAP = b'\x00\x00\x10\x10\x11\x11\x12\x12\x13\x13\x14\x14\x15\x15\x16\x16\x16\x17\x17\x17\x18\x18\x19\x19\x1a\x1a\x1a\x1b\x1b\x1b\x1c\x1c\x1d\x1d\x1d\x1e\x1e\x1e\x1f\x1f  !!!"""###$$$%%%&&&\'\'\'((()))***+++,,,,---...///00001111222333344445555666677778888899999::::;;;;;<<<<<=====>>>>>??????@@@'
TRIANGULAR_MAP = b',,-,.,.,/,0,0,1,1,2,2,3,3,4,4,5,5,6,6,7,7,8,8,9,9,9,:,:,;,;,<,<,<,=,=,>,>,>,?,?,?,@,--.-.-/-0-0-1-1-2-2-3-3-4-4-5-5-6-6-7-7-8-8-9-9-9-:-:-;-;-<-<-<-=-=->->->-?-?-?-@,..../.0.0.1.1.2.2.3.3.4.4.5.5.6.6.7.7.8.8.9.9.9.:.:.;.;.<.<.<.=.=.>.>.>.?-?-?-?-../.0.0.1.1.2.2.3.3.4.4.5.5.6.6.7.7.8.8.9.9.9.:.:.;.;.<.<.<.=.=.>.>.>.?-?-?-?-//0/0/1/1/2/2/3/3/4/4/5/5/6/6/7/7/8/8/9/9/9/:/:/;/;/</</</=/=/>/>/>/>/>/?-?-000010102020303040405050606070708080909090:0:0;0;0<0<0<0=0=0>/>/>/>/>/>/>/0010102020303040405050606070708080909090:0:0;0;0<0<0<0=0=0=0>/>/>/>/>/>/11112121313141415151616171718181919191:1:1;1;1<1<1<1=0=0=0>/>/>/>/>/>/112121313141415151616171718181919191:1:1;1;1<1<1<1<1<1=0=0>/>/>/>/>/2222323242425252626272728282929292:2:2;2;2<1<1<1<1<1<1=0=0>/>/>/>/22323242425252626272728282929292:2:2;2;2;2<1<1<1<1<1<1<1=0=0>/>/333343435353636373738383939393:3:3;3;3;3;3<1<1<1<1<1<1<1=0=0>/3343435353636373738383939393:3:3;3;3;3;3;3<1<1<1<1<1<1<1<1=044445454646474748484949494:4:4:4;3;3;3;3;3<1<1<1<1<1<1<1<1445454646474748484949494:4:4:4:4;3;3;3;3;3;3<1<1<1<1<1<1555565657575858595959595:4:4:4:4;3;3;3;3;3;3<1<1<1<1<1556565757585859595959595:4:4:4:4;3;3;3;3;3;3<1<1<1<1666676768686869595959595:4:4:4:4;3;3;3;3;3;3;3<1<1667676868686959595959595:4:4:4:4:4;3;3;3;3;3;3<1777777868686959595959595:4:4:4:4:4;3;3;3;3;3;3777777868686869595959595:4:4:4:4:4;3;3;3;3;377777786868686959595959595:4:4:4:4:4;3;3;377777786868686959595959595:4:4:4:4:4;3;377777786868686959595959595:4:4:4:4:4;377777786868686959595959595:4:4:4:4:477777786868686959595959595:4:4:4:47777778686868695959595959595:4:47777778686868695959595959595:4777777868686869595959595959577777786868686959595959595777777868686869595959595777777868686869595959577777786868686869595777777868686868695777777868686868677777786868686777777868686777777868677777786777777777777'

def _tri_index(row, col, size):
    return (size * (size - 1) // 2) - (size - row) * ((size - row) - 1) // 2 + col

def _invert_vc(c0, c1):
    # doubled-resolution unsigned inputs (0..2*OOT_MAX)
    if c0 > 2 * OOT_MAX: c0 = 2 * OOT_MAX
    if c1 > 2 * OOT_MAX: c1 = 2 * OOT_MAX
    if c0 >= 2 * BOUNDARY and c1 >= 2 * BOUNDARY:
        remainder = OOT_MAX + 1 - BOUNDARY
        a = (c0 // 2) - BOUNDARY
        b = (c1 // 2) - BOUNDARY
        idx = _tri_index(b, a, remainder)
        return TRIANGULAR_MAP[2 * idx], TRIANGULAR_MAP[2 * idx + 1]
    return ONE_DIMENSIONAL_MAP[c0], ONE_DIMENSIONAL_MAP[c1]

def invert_vc_n64(x, y):
    """Signed N64 coords (-128..127) -> unsigned GC coords (0..255) that survive VC."""
    xp = 1 if x >= 0 else 0
    yp = 1 if y >= 0 else 0
    ux = 2 * x if x >= 0 else (2 * 127 if x == -128 else -2 * x)
    uy = 2 * y if y >= 0 else (2 * 127 if y == -128 else -2 * y)
    swap = 0
    if uy > ux:
        swap = 1; ux, uy = uy, ux
    ux, uy = _invert_vc(ux, uy)
    if swap:
        ux, uy = uy, ux
    ux = ux + 128 if xp else 128 - ux
    uy = uy + 128 if yp else 128 - uy
    return ux, uy

# Precompute the full N64->GC inverse table once at startup (index by nx+80, ny+80).
_INV = [[invert_vc_n64(nx, ny) for ny in range(-OOT_MAX, OOT_MAX + 1)]
        for nx in range(-OOT_MAX, OOT_MAX + 1)]

def lookup_gc(nx, ny):
    nx = max(-OOT_MAX, min(OOT_MAX, nx))
    ny = max(-OOT_MAX, min(OOT_MAX, ny))
    return _INV[nx + OOT_MAX][ny + OOT_MAX]

# ----------------- FORWARD VC MAP (for --selftest only) -----------------
_DZ, _MAXLEN = 15, 56
def _sub_dz(c):
    if c > _DZ: return c - _DZ
    if c < -_DZ: return c + _DZ
    return 0
def _map_coord(c):
    c = math.trunc(c / _MAXLEN * 127)
    sign = 1 if c >= 0 else -1
    c /= 127
    c = 1 - math.sqrt(1 - abs(c))
    return int(math.trunc(c * sign * 127))
def vc_map(x, y):
    x = _sub_dz(int(x)); y = _sub_dz(int(y))
    L = math.sqrt(x * x + y * y)
    if L > _MAXLEN:
        x = x * _MAXLEN / math.trunc(L); y = y * _MAXLEN / math.trunc(L)
    return _map_coord(math.trunc(x)), _map_coord(math.trunc(y))

# ----------------- INPUT SHAPING -----------------
def clamp(v, lo, hi): return max(lo, min(hi, v))

def axis_deadzone(v, dz):
    a = abs(v)
    if a <= dz: return 0.0
    return math.copysign((a - dz) / (1.0 - dz), v)

def clamp_octagon(x, y, card, diag):
    x = clamp(x, -card, card); y = clamp(y, -card, card)
    dl = 2.0 * diag; l1 = abs(x) + abs(y)
    if l1 > dl:
        s = dl / l1; x *= s; y *= s
    return x, y

def ess_remap_magnitude(m):
    """Three-zone widening (same shape as pyESS_wiiclassic.py): a wide physical input
    window is compressed into a narrow N64-magnitude band so ESS is easy to hold.
    Input m in [0,1]; returns a fraction of N64 range."""
    if m < ESS_INPUT_START:
        return m  # gap: 1:1 passthrough between deadzone and ESS start
    if m <= ESS_INPUT_END:
        p = (m - ESS_INPUT_START) / (ESS_INPUT_END - ESS_INPUT_START)
        return ESS_OUTPUT_START + p * (ESS_OUTPUT_END - ESS_OUTPUT_START)
    p = (m - ESS_INPUT_END) / (1.0 - ESS_INPUT_END)
    return ESS_OUTPUT_END + p * (1.0 - ESS_OUTPUT_END)

def stick_to_gc(lx, ly):
    """Physical stick [-1,1] -> N64 intent (ESS-band widened) -> inverse VC -> GC 0..255."""
    dx = axis_deadzone(lx, PHYS_DEADZONE)
    dy = axis_deadzone(ly, PHYS_DEADZONE)
    if dx == 0.0 and dy == 0.0:
        return 128, 128
    mag = math.hypot(dx, dy)
    new_mag = ess_remap_magnitude(min(mag, 1.0)) if ESS_ENABLE else min(mag, 1.0)
    ux, uy = dx / mag, dy / mag  # direction from the square-deadzoned vector
    nx, ny = clamp_octagon(ux * new_mag * N64_CARDINAL, uy * new_mag * N64_CARDINAL,
                           N64_CARDINAL, N64_DIAGONAL)
    return lookup_gc(int(round(nx)), int(round(ny)))

def gc_to_i16(g):
    return int(clamp((g - 128) / 128.0, -1.0, 1.0) * 32767)

# ----------------- SELF TEST -----------------
def selftest():
    worst = 0.0; worst_pt = None; over3 = 0; n = 0
    for ny in range(0, OOT_MAX + 1):
        for nx in range(ny, OOT_MAX + 1):
            if nx * nx + ny * ny > OOT_MAX * OOT_MAX:
                continue
            gx, gy = invert_vc_n64(nx, ny)
            ox, oy = vc_map(gx - 128, gy - 128)
            ox = clamp(ox, -OOT_MAX, OOT_MAX); oy = clamp(oy, -OOT_MAX, OOT_MAX)
            d = math.hypot(ox - nx, oy - ny)
            n += 1
            if d > 3: over3 += 1
            if d > worst: worst = d; worst_pt = (nx, ny, gx, gy, ox, oy)
    print(f"[selftest] {n} reachable targets, worst error {worst:.2f}, error>3: {over3}")
    print(f"[selftest] worst (nx,ny)->(gcx,gcy)->(ingame): {worst_pt}")
    for t in (18, 22, 27):
        print(f"[selftest] intend N64 x={t:2d} -> GC {invert_vc_n64(t, 0)}  (feed to Dolphin)")
    print(f"[selftest] ESS widening ({'ON' if ESS_ENABLE else 'OFF'}): "
          f"phys window [{ESS_INPUT_START},{ESS_INPUT_END}] -> "
          f"N64 [{ESS_OUTPUT_START*N64_CARDINAL:.0f}..{ESS_OUTPUT_END*N64_CARDINAL:.0f}]")
    for pm in (0.03, 0.06, 0.08, 0.12, 0.20, 0.35, 0.50, 1.00):
        gcx, _ = stick_to_gc(pm, 0.0)
        dm = axis_deadzone(pm, PHYS_DEADZONE)
        nm = (ess_remap_magnitude(min(dm, 1.0)) if ESS_ENABLE else min(dm, 1.0)) * N64_CARDINAL
        tag = " <- ESS" if 16 <= nm <= 27 else ""
        print(f"    stick {pm:4.2f} -> N64 ~{nm:5.1f} -> GC x={gcx}{tag}")

# ----------------- HELPERS (hardware) -----------------
def axis_safe(js, idx, default=0.0):
    try: return js.get_axis(idx)
    except Exception: return default
def get_axis(js, axes_map, name, invert_map):
    v = axis_safe(js, axes_map[name], 0.0)
    if invert_map.get(name, False): v = -v
    return v
def snapshot_axes(js):
    return [axis_safe(js, i, 0.0) for i in range(js.get_numaxes())]
def biggest_delta_axis(prev, curr, ignore=set()):
    idx, best = -1, 0.0
    for i, (p, c) in enumerate(zip(prev, curr)):
        if i in ignore: continue
        d = abs(c - p)
        if d > best: best, idx = d, i
    return idx, best

def autodetect_right_stick(pygame, js, axes_map, invert_map):
    print("\n=== Right Stick AUTO detection ===")
    print("Move the RIGHT STICK fully LEFT/RIGHT for ~2 seconds.")
    time.sleep(0.7)
    baseline = snapshot_axes(js); start = time.time()
    while time.time() - start < 2.0:
        pygame.event.pump(); curr = snapshot_axes(js)
        idx, delta = biggest_delta_axis(baseline, curr, ignore={axes_map['LX'], axes_map['LY']})
        if delta > 0.20:
            axes_map['RX'] = idx; print(f"  RSX axis = {idx}"); break
    axes_map.setdefault('RX', DEFAULT_AXES_BY_SOURCE['Z_RZ']['RX'])
    print("Move the RIGHT STICK fully UP/DOWN for ~2 seconds.")
    time.sleep(0.7)
    baseline = snapshot_axes(js); start = time.time()
    while time.time() - start < 2.0:
        pygame.event.pump(); curr = snapshot_axes(js)
        idx, delta = biggest_delta_axis(baseline, curr, ignore={axes_map['LX'], axes_map['LY'], axes_map['RX']})
        if delta > 0.20:
            axes_map['RY'] = idx; invert_map['RY'] = True; print(f"  RSY axis = {idx}"); break
    axes_map.setdefault('RY', DEFAULT_AXES_BY_SOURCE['Z_RZ']['RY'])
    return axes_map, invert_map

# ----------------- MAIN -----------------
def main():
    import pygame
    import vgamepad as vg

    button_map = {
        0: vg.XUSB_BUTTON.XUSB_GAMEPAD_A, 1: vg.XUSB_BUTTON.XUSB_GAMEPAD_B,
        2: vg.XUSB_BUTTON.XUSB_GAMEPAD_X, 3: vg.XUSB_BUTTON.XUSB_GAMEPAD_Y,
        4: vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER, 5: vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER,
        6: vg.XUSB_BUTTON.XUSB_GAMEPAD_BACK, 7: vg.XUSB_BUTTON.XUSB_GAMEPAD_START,
        8: vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_THUMB, 9: vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_THUMB,
    }

    pygame.init(); pygame.joystick.init(); pygame.display.init()
    if pygame.joystick.get_count() == 0:
        print("No joystick found."); sys.exit(1)
    js = pygame.joystick.Joystick(DEVICE_INDEX); js.init()
    print(f"Using Controller [{DEVICE_INDEX}]: {js.get_name()}")
    print(f"Axes: {js.get_numaxes()} Buttons: {js.get_numbuttons()} Hats: {js.get_numhats()}")
    print("Mode: WiiVC inverse compensation for Dolphin (emulate a GC controller, deadzone 0).")

    source = RIGHT_STICK_SOURCE.upper()
    if source not in ("RX_RY", "Z_RZ", "AUTO"): source = "Z_RZ"
    axes_map = dict(DEFAULT_AXES_BY_SOURCE["Z_RZ"] if source != "RX_RY" else DEFAULT_AXES_BY_SOURCE["RX_RY"])
    invert_map = dict(INVERT_DEFAULT)
    axes_map['LX'] = 0; axes_map['LY'] = 1
    if source == "AUTO":
        axes_map, invert_map = autodetect_right_stick(pygame, js, axes_map, invert_map)

    gamepad = vg.VX360Gamepad(); clock = pygame.time.Clock(); last_debug = 0.0
    while True:
        for _ in pygame.event.get(): pass

        lx = get_axis(js, axes_map, "LX", invert_map)
        ly = get_axis(js, axes_map, "LY", invert_map)
        gcx, gcy = stick_to_gc(lx, ly)

        rx = get_axis(js, axes_map, "RX", invert_map)
        ry = get_axis(js, axes_map, "RY", invert_map)

        lt_idx = axes_map.get("LT"); rt_idx = axes_map.get("RT")
        lt_raw = axis_safe(js, lt_idx, 0.0) if lt_idx is not None else 0.0
        rt_raw = axis_safe(js, rt_idx, 0.0) if rt_idx is not None else 0.0
        if lt_idx is not None and rt_idx is not None and lt_idx == rt_idx:
            lt_val = max(0.0, -lt_raw); rt_val = max(0.0, lt_raw)
        else:
            lt_val = (lt_raw + 1.0) / 2.0; rt_val = (rt_raw + 1.0) / 2.0
        lt_b = int(clamp(lt_val, 0.0, 1.0) * 255); rt_b = int(clamp(rt_val, 0.0, 1.0) * 255)

        btn_states = {button_map[i]: bool(js.get_button(i)) for i in range(js.get_numbuttons()) if button_map.get(i)}
        if js.get_numhats() > 0:
            hx, hy = js.get_hat(0)
            du, dd, dl, dr = (hy > 0), (hy < 0), (hx < 0), (hx > 0)
        else:
            du = dd = dl = dr = False

        gamepad.left_joystick(gc_to_i16(gcx), gc_to_i16(gcy))
        gamepad.right_joystick(int(clamp(rx, -1, 1) * 32767), int(clamp(ry, -1, 1) * 32767))
        gamepad.left_trigger(lt_b); gamepad.right_trigger(rt_b)
        for bconst, pressed in btn_states.items():
            (gamepad.press_button if pressed else gamepad.release_button)(bconst)
        (gamepad.press_button if du else gamepad.release_button)(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP)
        (gamepad.press_button if dd else gamepad.release_button)(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN)
        (gamepad.press_button if dl else gamepad.release_button)(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT)
        (gamepad.press_button if dr else gamepad.release_button)(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT)
        gamepad.update(); clock.tick(HZ)

        if DEBUG_PRINT_AXES and (time.time() - last_debug) >= DEBUG_PRINT_INTERVAL_S:
            last_debug = time.time()
            print(f" L({lx:+.2f},{ly:+.2f}) -> GC({gcx},{gcy})")

if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        try:
            main()
        except KeyboardInterrupt:
            print("\nExiting.")
        except Exception as e:
            print(f"\nerror occurred: {e}")
