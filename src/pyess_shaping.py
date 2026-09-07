"""Shared stick-shaping curve: deadzone -> ESS magnitude remap -> octagon gate.

Used by both output targets so they stay mirrored. Normalized units [-1..1]
throughout; `cfg` comes from pyess_config.load_zones().
"""
import math

# Derived from the decomp (padutils.c / z_player.c): per-axis deadzone 7, magnitude
# < 20 = ESS. Gives a diagonal floor of 8*sqrt(2) = 11.31 and a cardinal ceiling of 26.
GAME_DEADZONE = 7
GAME_WALK_CUR = 27          # first cur value that walks (= walk magnitude 20 + 7)


def ess_output_band(max_axis_range=85.0):
    """Return (start, end) normalised output magnitudes for the ESS band.

    Spans the band rather than pinning to one value: mechanically identical in game,
    but keeps stick movement visible in the viewer. The extremes sit on the edges, so
    inset lo/hi by ~1.5 if you want margin.
    """
    lo = (GAME_DEADZONE + 1) * math.sqrt(2.0)      # diagonal survives  = 11.31
    hi = float(GAME_WALK_CUR - 1)                  # cardinal still ESS = 26
    return lo / max_axis_range, hi / max_axis_range


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def axis_deadzone(v, dz):
    """Square per-axis deadzone; the remaining range is rescaled to preserve reach."""
    a = abs(v)
    if a <= dz:
        return 0.0
    if dz >= 1.0:
        return 0.0
    return math.copysign((a - dz) / (1.0 - dz), v)


def ess_remap_magnitude(m, cfg):
    """Compress the ESS input window into the derived output band.

    The window always starts at the deadzone edge. A gap would emit magnitudes below
    the 11.31 diagonal floor, measured at 15-28% of the window going NEUTRAL.
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
    """Physical stick [-1,1] -> normalized shaped output [-1,1]."""
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
