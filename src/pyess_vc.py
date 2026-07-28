# pyess_vc.py
# WiiVC stick-map inversion + a decomp-accurate model of OoT's own stick handling.
#
# The inversion tables and algorithm are ported from Skuzee's ESS-Adapter
# (generate-map.py / ESS.cpp), which is GPLv3. This file is therefore GPLv3.
#
# VC distortion model: extra deadzone 15, length clamp 56, per-axis 1-sqrt(1-x) curve.
# Feed invert_vc_n64() the raw `cur` you WANT in game; it returns the GC byte that,
# after VC mangles it, produces that value.

import math

# ---------------- VC inversion (GPLv3, from ESS-Adapter) ----------------
OOT_MAX = 80
BOUNDARY = 39
ONE_DIMENSIONAL_MAP = b'\x00\x00\x10\x10\x11\x11\x12\x12\x13\x13\x14\x14\x15\x15\x16\x16\x16\x17\x17\x17\x18\x18\x19\x19\x1a\x1a\x1a\x1b\x1b\x1b\x1c\x1c\x1d\x1d\x1d\x1e\x1e\x1e\x1f\x1f  !!!"""###$$$%%%&&&\'\'\'((()))***+++,,,,---...///00001111222333344445555666677778888899999::::;;;;;<<<<<=====>>>>>??????@@@'
TRIANGULAR_MAP = b',,-,.,.,/,0,0,1,1,2,2,3,3,4,4,5,5,6,6,7,7,8,8,9,9,9,:,:,;,;,<,<,<,=,=,>,>,>,?,?,?,@,--.-.-/-0-0-1-1-2-2-3-3-4-4-5-5-6-6-7-7-8-8-9-9-9-:-:-;-;-<-<-<-=-=->->->-?-?-?-@,..../.0.0.1.1.2.2.3.3.4.4.5.5.6.6.7.7.8.8.9.9.9.:.:.;.;.<.<.<.=.=.>.>.>.?-?-?-?-../.0.0.1.1.2.2.3.3.4.4.5.5.6.6.7.7.8.8.9.9.9.:.:.;.;.<.<.<.=.=.>.>.>.?-?-?-?-//0/0/1/1/2/2/3/3/4/4/5/5/6/6/7/7/8/8/9/9/9/:/:/;/;/</</</=/=/>/>/>/>/>/?-?-000010102020303040405050606070708080909090:0:0;0;0<0<0<0=0=0>/>/>/>/>/>/>/0010102020303040405050606070708080909090:0:0;0;0<0<0<0=0=0=0>/>/>/>/>/>/11112121313141415151616171718181919191:1:1;1;1<1<1<1=0=0=0>/>/>/>/>/>/112121313141415151616171718181919191:1:1;1;1<1<1<1<1<1=0=0>/>/>/>/>/2222323242425252626272728282929292:2:2;2;2<1<1<1<1<1<1=0=0>/>/>/>/22323242425252626272728282929292:2:2;2;2;2<1<1<1<1<1<1<1=0=0>/>/333343435353636373738383939393:3:3;3;3;3;3<1<1<1<1<1<1<1=0=0>/3343435353636373738383939393:3:3;3;3;3;3;3<1<1<1<1<1<1<1<1=044445454646474748484949494:4:4:4;3;3;3;3;3<1<1<1<1<1<1<1<1445454646474748484949494:4:4:4:4;3;3;3;3;3;3<1<1<1<1<1<1555565657575858595959595:4:4:4:4;3;3;3;3;3;3<1<1<1<1<1556565757585859595959595:4:4:4:4;3;3;3;3;3;3<1<1<1<1666676768686869595959595:4:4:4:4;3;3;3;3;3;3;3<1<1667676868686959595959595:4:4:4:4:4;3;3;3;3;3;3<1777777868686959595959595:4:4:4:4:4;3;3;3;3;3;3777777868686869595959595:4:4:4:4:4;3;3;3;3;377777786868686959595959595:4:4:4:4:4;3;3;377777786868686959595959595:4:4:4:4:4;3;377777786868686959595959595:4:4:4:4:4;377777786868686959595959595:4:4:4:4:477777786868686959595959595:4:4:4:47777778686868695959595959595:4:47777778686868695959595959595:4777777868686869595959595959577777786868686959595959595777777868686869595959595777777868686869595959577777786868686869595777777868686868695777777868686868677777786868686777777868686777777868677777786777777777777'


def _tri_index(row, col, size):
    return (size * (size - 1) // 2) - (size - row) * ((size - row) - 1) // 2 + col


def _invert_vc(c0, c1):
    """Doubled-resolution unsigned inputs (0..2*OOT_MAX)."""
    if c0 > 2 * OOT_MAX:
        c0 = 2 * OOT_MAX
    if c1 > 2 * OOT_MAX:
        c1 = 2 * OOT_MAX
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
        swap = 1
        ux, uy = uy, ux
    ux, uy = _invert_vc(ux, uy)
    if swap:
        ux, uy = uy, ux
    ux = ux + 128 if xp else 128 - ux
    uy = uy + 128 if yp else 128 - uy
    return ux, uy


# Precomputed full N64->GC inverse, indexed by (nx+80, ny+80).
_INV = [[invert_vc_n64(nx, ny) for ny in range(-OOT_MAX, OOT_MAX + 1)]
        for nx in range(-OOT_MAX, OOT_MAX + 1)]


def lookup_gc(nx, ny):
    nx = max(-OOT_MAX, min(OOT_MAX, int(nx)))
    ny = max(-OOT_MAX, min(OOT_MAX, int(ny)))
    return _INV[nx + OOT_MAX][ny + OOT_MAX]


def best_gc(fx, fy, radius=1):
    """Pick the GC byte pair whose ACHIEVED in-game value is nearest the FRACTIONAL
    target (fx, fy), rather than rounding the target to an integer first.

    Why: VC cannot produce every in-game value (13, 17, 22, 25, ... are gaps). Rounding
    first can name an unreachable value and land on the wrong side of a gap - a 1-byte
    difference there shifts the in-game result by 2. Searching on the unrounded target
    keeps X and Y independent when they genuinely differ.

    Falls back to the plain table lookup as the search seed, so it can only improve.

    radius=1 is not a shortcut: the seed comes from the exact integer inverse table, so
    the true best is always within one byte. Verified identical to radius=3 across all
    25921 half-unit targets in range (0 differences) at 5x the speed.

    Deliberately NOT cached. A memo keyed on quantised (fx, fy) measured a 0% hit rate
    under real stick motion - the values vary continuously, so every frame was a miss
    while the dict grew unboundedly. At ~10us a call the search is cheaper than the memo.
    """
    gx, gy = lookup_gc(int(round(fx)), int(round(fy)))
    best_key = None
    best = (gx, gy)
    for dx in range(-radius, radius + 1):
        bx = gx + dx
        if not 0 <= bx <= 255:
            continue
        for dy in range(-radius, radius + 1):
            by = gy + dy
            if not 0 <= by <= 255:
                continue
            ax, ay = vc_map(bx - 128, by - 128)
            err = (ax - fx) ** 2 + (ay - fy) ** 2
            # Tie-break toward the centre: many bytes map to the same in-game value
            # (VC's deadzone swallows the small ones), and of those we want the least
            # deflection - otherwise a centred stick can emit an off-centre byte.
            # Matches ESS-Adapter's "if equally close, bias towards origin".
            rank = (err, (bx - 128) ** 2 + (by - 128) ** 2)
            if best_key is None or rank < best_key:
                best_key = rank
                best = (bx, by)
    return best


# ---------------- Forward VC map (what VC actually delivers) ----------------
_DZ, _MAXLEN = 15, 56


def _sub_dz(c):
    if c > _DZ:
        return c - _DZ
    if c < -_DZ:
        return c + _DZ
    return 0


def _map_coord(c):
    c = math.trunc(c / _MAXLEN * 127)
    sign = 1 if c >= 0 else -1
    c /= 127
    c = 1 - math.sqrt(1 - abs(c))
    return int(math.trunc(c * sign * 127))


def vc_map(x, y):
    """Signed GC offset (-128..127) -> in-game raw cur, as VC would produce it."""
    x = _sub_dz(int(x))
    y = _sub_dz(int(y))
    L = math.sqrt(x * x + y * y)
    if L > _MAXLEN:
        x = x * _MAXLEN / math.trunc(L)
        y = y * _MAXLEN / math.trunc(L)
    return _map_coord(math.trunc(x)), _map_coord(math.trunc(y))


# ---------------- OoT's own stick handling (decomp-verified) ----------------
#   padutils.c PadUtils_UpdateRelXY : rel = |cur| - 7 per axis (square), cur clamped 0x43=67
#   z_lib.c    func_80077D10        : magnitude = hypot(relX,relY), clamped to 60
#   z_player.c SPEED_MODE_CURVED    : magnitude < 20 => speed 0 (pivot in place = ESS)
#   => ESS is raw cur 8..26; cur 27 walks.
GAME_DEADZONE = 7
GAME_RAW_MAX = 67
GAME_WALK_MAG = 20
GAME_MAG_MAX = 60

# Walk -> run. z_player.c (idle action, ~line 8244):
#     Player_GetMovementSpeedAndYaw(..., SPEED_MODE_LINEAR, ...)
#     if (speedTarget > 4.9f) { ...run anim... }
#     if (speedTarget != 0.0f) { ...walk anim... }
# SPEED_MODE_LINEAR is  speed = magnitude * 0.8 * 0.14,  so the switch is a fixed
# magnitude: 4.9 / 0.112 = 43.75  (cardinal raw cur ~51). Flat ground only - a floor
# pitch subtracts 8*sin(pitch)^2 and pushes the boundary outward.
GAME_RUN_SPEED = 4.9
GAME_RUN_MAG = GAME_RUN_SPEED / (0.8 * 0.14)          # 43.75

# Run -> FULL RUN. The two states use DIFFERENT formulas:
#   Player_Action_80840DE4 (walk) uses SPEED_MODE_LINEAR  -> speed = mag * 0.8 * 0.14
#   Player_Action_80842180 (run)  uses SPEED_MODE_CURVED  -> the cosine curve below,
#                                                            CLAMPed to speedCap.
# So RUN is still progressive (speed 1.95 at mag 43.75, climbing); only past the cap is
# speed actually constant. speedCap = R_RUN_SPEED_LIMIT/100 = sBootData[boots][9]/100.
GAME_SPEED_CAP = 600 / 100.0                          # normal boots


def speed_curved(mag, cap=GAME_SPEED_CAP):
    """z_player.c Player_GetMovementSpeedAndYaw, SPEED_MODE_CURVED (flat ground)."""
    t = mag - GAME_WALK_MAG
    if t < 0.0:
        return 0.0
    temp = 1.0 - math.cos(t * 450.0 * math.pi / 32768.0)   # Math_CosS(t * 450)
    return min((temp * temp * 30.0 + 7.0) * 0.14, cap)


def _full_run_mag():
    lo, hi = GAME_RUN_MAG, float(GAME_MAG_MAX)
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if speed_curved(mid) >= GAME_SPEED_CAP - 1e-12:
            hi = mid
        else:
            lo = mid
    return hi


GAME_FULL_RUN_MAG = None   # set below, once GAME_MAG_MAX exists


def game_magnitude(cur_x, cur_y):
    def rel(c):
        c = int(c)
        if c > GAME_DEADZONE:
            return (c if c < GAME_RAW_MAX else GAME_RAW_MAX) - GAME_DEADZONE
        if c < -GAME_DEADZONE:
            return (c if c > -GAME_RAW_MAX else -GAME_RAW_MAX) + GAME_DEADZONE
        return 0
    m = math.hypot(rel(cur_x), rel(cur_y))
    return GAME_MAG_MAX if m > GAME_MAG_MAX else m


GAME_FULL_RUN_MAG = _full_run_mag()                    # ~58.57


def game_state(mag):
    if mag == 0:
        return "NEUTRAL"
    if mag < GAME_WALK_MAG:
        return "ESS"
    if mag <= GAME_RUN_MAG:
        return "WALK"
    return "RUN" if mag < GAME_FULL_RUN_MAG else "FULL RUN"
