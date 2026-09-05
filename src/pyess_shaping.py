# pyess_shaping.py
# The shared stick-shaping curve used by BOTH output targets (PC/SoH and Dolphin/VC).
#
#   square per-axis deadzone -> ESS magnitude remap -> octagon gate
#
# Everything here works in NORMALIZED units [-1..1]. Only the output stage differs
# between targets, which is why both can share this and stay mirrored.
#
# All functions take a `cfg` dict as resolved by pyess_config.load_zones().

import math

# ---------------------------------------------------------------------------
# The ESS OUTPUT band is not a preference - it is fixed by the game, so we derive
# it instead of exposing sliders for it. Decomp constants (padutils.c / z_player.c):
#   square deadzone 7 per axis, cur clamped 67, magnitude < 20 = ESS, >= 20 walks.
#
#   lower bound: on a DIAGONAL the magnitude splits across two axes, and each must
#                survive the per-axis deadzone as an integer -> per-axis cur >= 8,
#                so magnitude >= 8*sqrt(2) = 11.31. (Below this the diagonals go
#                dead while the cardinals still work - the "corner" artefact.)
#   upper bound: on a CARDINAL the whole magnitude lands on one axis, and cur 27
#                walks -> cur <= 26.
#
# Verified empirically over 4036 samples per magnitude across all angles: fails at
# 10, ESS everywhere from 11 to 26.5, fails at 27.
GAME_DEADZONE = 7
GAME_WALK_CUR = 27          # first cur value that walks (= walk magnitude 20 + 7)


def ess_output_band(max_axis_range=85.0):
    """Return (start, end) normalised output magnitudes for the ESS band.

    SPANNING (start < end): the input window ramps linearly across the whole in-game
    ESS band rather than pinning to a single value. Every magnitude inside the band is
    functionally identical in game, so this changes NOTHING mechanically - but it keeps
    stick movement visible in the input viewer and telemetry instead of flatlining on
    one number (cur ~18.7) across the first third of stick travel.

    This replaced a FLAT band (start == end == the midpoint, 18.655/85). Flat maximised
    margin at both ends - you could not drift out of ESS anywhere inside the window -
    but it also collapsed 20 of the ~64 reachable stick levels onto a single output,
    which is what made the viewer unrepresentative.

    The trade is margin for representativeness: at the extremes of the input window the
    output now sits ON the band edges (the 11.31 diagonal floor and the 26 walk
    threshold) instead of centred between them. To buy margin back, inset lo/hi - e.g.
    lo + 1.5 and hi - 1.5 keeps a visible ramp while staying clear of both edges.
    """
    lo = (GAME_DEADZONE + 1) * math.sqrt(2.0)      # diagonal survives  = 11.31
    hi = float(GAME_WALK_CUR - 1)                  # cardinal still ESS = 26
    return lo / max_axis_range, hi / max_axis_range


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def axis_deadzone(v, dz):
    """Square (per-axis) deadzone. |v|<=dz reads neutral; the remaining range
    [dz,1] is rescaled back to [0,1] so full reach is preserved.
    Applied independently to X and Y, this produces the SQUARE dead box."""
    a = abs(v)
    if a <= dz:
        return 0.0
    if dz >= 1.0:
        return 0.0
    return math.copysign((a - dz) / (1.0 - dz), v)


def ess_remap_magnitude(m, cfg):
    """Compress the ESS input window into the (derived, flat) ESS output band.

    The window ALWAYS begins where the deadzone ends - there is no separate start.
    A gap between the two would be a 1:1 passthrough ramp emitting magnitudes below
    the 11.31 diagonal floor, which puts the dead-corner artefact straight back:
    measured 15-28% of the window going NEUTRAL for starts of 0.05-0.25. Zero is the
    only always-correct value, so it is derived rather than exposed.

    `m` is the post-deadzone magnitude in [0,1]; returns a fraction of the range.
    """
    size = cfg["ess_zone_size"]
    os_ = cfg["ess_output_start"]
    oe = cfg["ess_output_end"]
    if size <= 0.0:                 # no plateau -> straight passthrough
        return m
    if m <= size:
        return os_ + (m / size) * (oe - os_)
    if size >= 1.0:
        return oe
    return oe + ((m - size) / (1.0 - size)) * (1.0 - oe)


def clamp_octagon(x, y, card, diag):
    """Clamp to the octagon: |x|<=card, |y|<=card, |x|+|y|<=2*diag."""
    x = clamp(x, -card, card)
    y = clamp(y, -card, card)
    dl = 2.0 * diag
    l1 = abs(x) + abs(y)
    if l1 > dl and l1 > 0:
        s = dl / l1
        x *= s
        y *= s
    return x, y


def shape(lx, ly, cfg):
    """Physical stick [-1,1] -> normalized shaped output [-1,1].
    This is the curve both targets share."""
    dz = cfg["deadzone"]
    dx = axis_deadzone(lx, dz)
    dy = axis_deadzone(ly, dz)
    if dx == 0.0 and dy == 0.0:
        return 0.0, 0.0
    dmag = math.hypot(dx, dy)
    if dmag <= 0.0:
        return 0.0, 0.0
    m = min(dmag, 1.0)
    new_mag = ess_remap_magnitude(m, cfg)
    ux, uy = dx / dmag, dy / dmag
    return clamp_octagon(ux * new_mag, uy * new_mag,
                         cfg["octagon_cardinal"], cfg["octagon_diagonal"])
