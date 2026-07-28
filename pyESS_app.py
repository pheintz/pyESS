# pyESS_app.py
# Single combined application: live-editable zone GUI + switchable output target.
#
#   PC / Ship of Harkinian : shaped -> int16 -> virtual X360 pad -> SoH
#   Dolphin / WiiVC        : shaped -> raw cur -> INVERT VC -> GC byte -> X360 -> Dolphin
#
# Both targets share the SAME shaping curve (pyess_shaping), so switching output does
# not change the feel - only how the value is delivered. Zone edits apply instantly to
# the running remap loop; "Save" persists them to pyESS_zones.json.
#
# Contains GPLv3 code by way of pyess_vc (Skuzee ESS-Adapter port) -> this app is GPLv3.
#
# Run:  python pyESS_app.py

__version__ = "1.0.0"

import math
import os
import sys
import threading
import time
import tkinter as tk
from collections import deque
from tkinter import ttk, messagebox

import pyess_shaping as shaping
import pyess_vc as vc
from pyess_config import load_zones, save_zones, TARGET_KEYS

HZ = 1000                # poll/emit rate. The loop period is the DOMINANT latency
                         # term: compute is ~5us (SoH) / ~10us (Dolphin), so at 250Hz
                         # we were adding up to 4ms of staleness for ~0.01ms of work.
TELEMETRY_HZ = 30        # GUI reads at 25Hz; building it every frame was 10x waste
MAX_LAG_S = 0.15         # keep ~150ms of output history (max lag 100ms + margin)
MAP_PX = 160             # zone-map display size in pixels
MAP_SAMPLES = 80         # grid actually evaluated (each cell drawn MAP_PX/MAP_SAMPLES px)
                         # 40 gave visibly stepped 4px edges; 160 (per-pixel) cost ~630ms
# Zone palette: ink / amber / steel-blue.
#
# Built on the warm-cool axis rather than red-green, so it survives all three dichromacy
# types, and scored against Machado et al. (2009) simulation rather than assumed safe:
# Two CATEGORICAL anchors (ink, amber) plus a 3-step SEQUENTIAL ramp for the movement
# states. The movement states are ordered, not unrelated, so the ramp is judged on
# monotonic lightness (0.697 > 0.302 > 0.055 = "faster is deeper") rather than on
# categorical deltaE - forcing 5 mutually-distinct hues would have hurt both looks and
# accessibility. ESS keeps a hue contrast (warm amber) against every cool movement tone.
# It beats plain Okabe-Ito on every axis, most of all tritan (38.6 -> 49.4), while the
# desaturated ink and softened amber read as a deliberate palette rather than warning
# colours.
# NOTE: an earlier version outlined zone boundaries in near-black. It looked like a
# NEUTRAL ring that does not exist and did not appear in the legend, so every colour on
# the map is now a legend entry and nothing else.
MAP_NEUTRAL, MAP_ESS = "#1b1f27", "#f0a92e"
MAP_WALK, MAP_RUN, MAP_FULLRUN = "#a8e6d4", "#4a9fc4", "#15456b"

# Zone sliders: (key, label, min, max, tooltip-ish description)
ZONE_SPECS = [
    ("deadzone",         "Deadzone",          0.0,  0.50, "Square per-axis dead box; range rescaled after it"),
    ("ess_zone_size",    "ESS zone size",     0.0,  1.00, "How much stick past the deadzone holds ESS"),
]
# ESS always starts where the deadzone ends - a gap would reintroduce dead corners.
# ess_output_* is DERIVED (pyess_shaping.ess_output_band) - the in-game ESS band is
# fixed by the game, so there is nothing to tune.
# octagon_cardinal / octagon_diagonal are intentionally NOT sliders: on a round-gate
# pad the clamp barely fires, so they are set-once values that live in pyESS_zones.json.

TARGETS = [("pc", "PC / Ship of Harkinian"), ("dolphin", "Dolphin / WiiVC")]

# Passthrough mapping (matches the standalone scripts).
# Only the LEFT stick is shaped; everything else is forwarded untouched.
AXES = {"LX": 0, "LY": 1, "RX": 2, "RY": 3, "LT": 4, "RT": 5}
INVERT = {"LY": True, "RY": True}
BUTTON_NAMES = {
    0: "A", 1: "B", 2: "X", 3: "Y",
    4: "LB", 5: "RB", 6: "BACK", 7: "START",
    8: "LTHUMB", 9: "RTHUMB",
}


def _axis_safe(js, idx, default=0.0):
    if idx is None:
        return default
    try:
        return js.get_axis(idx)
    except Exception:
        return default


# --------------------------------------------------------------------------
# Engine: runs the remap loop on a background thread.
# GUI publishes config by swapping a dict reference (atomic), so no locking is
# needed on the hot path; a torn read would at worst mean one mixed frame.
# --------------------------------------------------------------------------
class Engine(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.cfg = load_zones("dolphin", verbose=False)
        self.target = "dolphin"
        self.device_index = 0
        self._stop = threading.Event()
        self._restart = threading.Event()
        self.telemetry = {}
        self.status = "stopped"
        self.error = None
        self.enabled = True           # actually drive the virtual pad?
        self.devices = []             # names, refreshed each session

    # ---- output stages -------------------------------------------------
    @staticmethod
    def soh_process(ix, iy, cfg):
        """Faithful port of libultraship ControllerStick::Process().

        Verified against live SoH readings on both cardinal and diagonal inputs.
        Returns the PRE-TRUNCATION floats; SoH then assigns them to int8_t, which
        truncates toward zero.

        The step that matters most is the octagon transform: it is NOT a clamp. Any
        time both axes are nonzero it rescales, mapping the circular input range onto
        SoH's own octagon (cardinal 85 / diagonal 69). That means it SHRINKS
        near-cardinal angles (~x0.975) and EXPANDS 45-degree diagonals (~x1.148).
        """
        mar = cfg.get("max_axis_range", 85.0)
        dz = cfg.get("soh_deadzone", 0.0)          # SoH's own Deadzone setting
        sens = cfg.get("soh_sensitivity", 1.0)     # SoH's own Sensitivity (1.0 = 100%)

        sx, sy = ix * mar / 32767.0, iy * mar / 32767.0
        ux, uy = abs(sx) * sens, abs(sy) * sens

        ln = math.hypot(ux, uy)                    # scaled circular dead-zone
        if ln > 0.0:
            if ln < dz:
                f = 0.0
            elif ln > mar:
                f = mar / ln
            else:
                f = (ln - dz) * mar / (mar - dz) / ln
            ux *= f
            uy *= f

        if ux != 0.0 and uy != 0.0:                # octagon transform (rescale!)
            slope = uy / ux
            edgex = mar / (abs(slope) + 16.0 / 69.0)
            edgey = min(abs(edgex * slope), mar / (1.0 / abs(slope) + 16.0 / 69.0))
            edgex = edgey / slope
            ratio = math.hypot(edgex, edgey) / mar
            ux *= ratio
            uy *= ratio

        return math.copysign(ux, sx), math.copysign(uy, sy)

    @staticmethod
    def soh_octagon_ratio(ux, uy):
        """The angle-dependent factor SoH's octagon transform will apply.
        Depends only on direction, so dividing by it beforehand cancels it exactly."""
        ux, uy = abs(ux), abs(uy)
        if ux == 0.0 or uy == 0.0:
            return 1.0
        slope = uy / ux
        edgex = 1.0 / (slope + 16.0 / 69.0)
        edgey = min(abs(edgex * slope), 1.0 / (1.0 / slope + 16.0 / 69.0))
        edgex = edgey / slope
        return math.hypot(edgex, edgey)

    def _out_pc(self, ox, oy, cfg):
        """SoH target: emit the raw i16; SoH applies its own Process() on top."""
        # Cancel SoH's octagon transform. It rescales diagonals by up to x1.148 toward
        # its own 85/69 octagon; Dolphin has no equivalent, so leaving it in place makes
        # the two targets disagree by ~10 magnitude units at 45 degrees. Pre-divide so
        # the gate the GAME sees is the one in pyESS_zones.json, on BOTH targets.
        r = self.soh_octagon_ratio(ox, oy)
        if r > 1e-9:
            ox, oy = ox / r, oy / r

        ix = int(shaping.clamp(ox, -1.0, 1.0) * 32767)
        iy = int(shaping.clamp(oy, -1.0, 1.0) * 32767)

        # Cancel SoH's truncate-toward-zero. It ends Process() with
        # `x = copysign(ux, sx)` into an int8_t, so every axis lands up to a full unit
        # low - a systematic -0.5 bias with no upside. Boost by half an in-game unit so
        # that truncation behaves like rounding. The i16 -> in-game map is a pure
        # scaling along the ray, so recover the per-axis factor k from one pass.
        fx, fy = self.soh_process(ix, iy, cfg)
        for _ in range(2):                         # one refinement: the boost tilts
            kx = abs(fx) / abs(ix) if ix else 0.0  # the angle very slightly
            ky = abs(fy) / abs(iy) if iy else 0.0
            nx = ix + math.copysign(0.5 / kx, ix) if kx > 1e-9 else ix
            ny = iy + math.copysign(0.5 / ky, iy) if ky > 1e-9 else iy
            nx = int(shaping.clamp(nx, -32767, 32767))
            ny = int(shaping.clamp(ny, -32767, 32767))
            fx, fy = self.soh_process(nx, ny, cfg)
        ix, iy = nx, ny

        fx, fy = self.soh_process(ix, iy, cfg)
        cur = (int(fx), int(fy))                   # int8_t truncation
        return (ix, iy), cur, None, cur

    def _out_dolphin(self, ox, oy, cfg):
        """Pre-invert VC so the WAD receives the cur we intended."""
        mar = cfg.get("max_axis_range", 85.0)
        gate = cfg.get("gate_compensation", 1.0) or 1.0
        fx, fy = ox * mar, oy * mar          # keep the FRACTIONAL target
        cur = (int(round(fx)), int(round(fy)))
        # Select on the unrounded target: VC has unreachable values (13, 17, 22, 25...)
        # and rounding first can land on the wrong side of a gap, costing 2 in-game units.
        gc = vc.best_gc(fx, fy)
        def to_i16(g):
            # Aim a QUARTER of a byte-step above the target byte's lower edge.
            # Dolphin must convert our i16 back into a GC byte; if it truncates,
            # sitting exactly on the edge loses 1 (71/72 bytes shift). A +0.25 step
            # lands inside the window for BOTH truncation and rounding:
            #   trunc(v)=n needs v in [n, n+1);  round(v)=n needs v in [n-0.5, n+0.5)
            #   -> intersection [n, n+0.5)
            step = (g - 128) + (0.25 if g > 128 else (-0.25 if g < 128 else 0.0))
            off = step / 128.0
            if gate != 1.0:
                off /= gate
            return int(shaping.clamp(off, -1.0, 1.0) * 32767)
        # what VC will actually deliver in game (accounts for unreachable values)
        achieved = vc.vc_map(gc[0] - 128, gc[1] - 128)
        return (to_i16(gc[0]), to_i16(gc[1])), cur, gc, achieved

    def compute(self, lx, ly, cfg=None, target=None):
        """Pure function: physical stick -> everything. Used by the loop AND tests."""
        cfg = cfg or self.cfg
        target = target or self.target
        ox, oy = shaping.shape(lx, ly, cfg)
        if target == "dolphin":
            out, cur, gc, achieved = self._out_dolphin(ox, oy, cfg)
        else:
            out, cur, gc, achieved = self._out_pc(ox, oy, cfg)
        mag = vc.game_magnitude(achieved[0], achieved[1])
        return {
            "stick": (lx, ly), "shaped": (ox, oy), "out": out,
            "cur": cur, "gc": gc, "achieved": achieved,
            "mag": mag, "state": vc.game_state(mag),
        }

    # ---- lifecycle -----------------------------------------------------
    def list_devices(self):
        return list(self.devices)

    def request_restart(self):
        self._restart.set()

    def stop(self):
        self._stop.set()

    def run(self):
        while not self._stop.is_set():
            try:
                self._session()
            except Exception as e:                      # keep GUI alive on any failure
                self.error = str(e)
                self.status = "error"
                time.sleep(1.0)
            if not self._restart.is_set():
                time.sleep(0.2)
            self._restart.clear()

    def _session(self):
        # Suppress pygame's stdout banner: noise in a console build, and with a
        # --windowed build there is no stdout at all.
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        import pygame
        import vgamepad as vg

        pygame.init()
        pygame.joystick.init()
        try:
            pygame.display.init()
        except Exception:
            pass
        if pygame.joystick.get_count() == 0:
            self.status = "no controller"
            self.error = "No joystick found"
            time.sleep(1.0)
            return

        self.devices = []
        for i in range(pygame.joystick.get_count()):
            try:
                j = pygame.joystick.Joystick(i)
                j.init()
                self.devices.append(j.get_name())
            except Exception:
                self.devices.append(f"device {i}")
        idx = min(self.device_index, pygame.joystick.get_count() - 1)
        js = pygame.joystick.Joystick(idx)
        js.init()
        try:
            pad = vg.VX360Gamepad()
        except Exception as ex:
            # By far the most common first-run failure. The raw exception says
            # "Cannot find ViGEm bus driver", which is true but not actionable.
            raise RuntimeError(
                f"Could not create the virtual gamepad ({ex}). "
                "ViGEmBus is required - install it from "
                "https://github.com/nefarius/ViGEmBus/releases and restart.") from None
        self.status = f"running: {js.get_name()}"
        self.error = None

        B = vg.XUSB_BUTTON
        vg_buttons = {
            "A": B.XUSB_GAMEPAD_A, "B": B.XUSB_GAMEPAD_B,
            "X": B.XUSB_GAMEPAD_X, "Y": B.XUSB_GAMEPAD_Y,
            "LB": B.XUSB_GAMEPAD_LEFT_SHOULDER, "RB": B.XUSB_GAMEPAD_RIGHT_SHOULDER,
            "BACK": B.XUSB_GAMEPAD_BACK, "START": B.XUSB_GAMEPAD_START,
            "LTHUMB": B.XUSB_GAMEPAD_LEFT_THUMB, "RTHUMB": B.XUSB_GAMEPAD_RIGHT_THUMB,
        }
        button_map = {i: vg_buttons[n] for i, n in BUTTON_NAMES.items() if n in vg_buttons}
        dpad_consts = (B.XUSB_GAMEPAD_DPAD_UP, B.XUSB_GAMEPAD_DPAD_DOWN,
                       B.XUSB_GAMEPAD_DPAD_LEFT, B.XUSB_GAMEPAD_DPAD_RIGHT)
        nbuttons = js.get_numbuttons()
        nhats = js.get_numhats()
        naxes = js.get_numaxes()
        dev_name = js.get_name()

        period = 1.0 / HZ
        next_t = time.perf_counter()
        tele_period = 1.0 / TELEMETRY_HZ
        next_tele = 0.0

        delay_buf = deque()          # (timestamp, payload) for artificial input lag
        NEUTRAL = {"ls": (0, 0), "rs": (0, 0), "lt": 0, "rt": 0,
                   "buttons": {}, "dpad": (False, False, False, False)}

        while not self._stop.is_set() and not self._restart.is_set():
            for _ in pygame.event.get():
                pass
            cfg = self.cfg               # atomic snapshot
            target = self.target

            # ---- left stick: the only thing we shape ----
            lx = _axis_safe(js, AXES["LX"])
            ly = _axis_safe(js, AXES["LY"])
            if INVERT.get("LY"):
                ly = -ly                 # up = positive
            t = self.compute(lx, ly, cfg, target)

            # ---- everything else: straight passthrough ----
            rx = _axis_safe(js, AXES["RX"])
            ry = _axis_safe(js, AXES["RY"])
            if INVERT.get("RY"):
                ry = -ry

            lt_i, rt_i = AXES.get("LT"), AXES.get("RT")
            lt_raw = _axis_safe(js, lt_i)
            rt_raw = _axis_safe(js, rt_i)
            if lt_i is not None and lt_i == rt_i:      # single combined trigger axis
                lt_val, rt_val = max(0.0, -lt_raw), max(0.0, lt_raw)
            else:                                       # separate axes rest at -1
                lt_val, rt_val = (lt_raw + 1.0) / 2.0, (rt_raw + 1.0) / 2.0
            lt_b = int(shaping.clamp(lt_val, 0.0, 1.0) * 255)
            rt_b = int(shaping.clamp(rt_val, 0.0, 1.0) * 255)

            pressed = {}
            for i in range(nbuttons):
                if i in button_map:
                    try:
                        pressed[i] = bool(js.get_button(i))
                    except Exception:
                        pressed[i] = False
            if nhats > 0:
                try:
                    hx, hy = js.get_hat(0)
                except Exception:
                    hx = hy = 0
            else:
                hx = hy = 0
            dpad = (hy > 0, hy < 0, hx < 0, hx > 0)     # up, down, left, right

            # Full output frame for this instant (before any artificial delay).
            payload = {
                "ls": t["out"],
                "rs": (int(shaping.clamp(rx, -1.0, 1.0) * 32767),
                       int(shaping.clamp(ry, -1.0, 1.0) * 32767)),
                "lt": lt_b, "rt": rt_b,
                "buttons": dict(pressed),
                "dpad": dpad,
            }

            # ---- artificial input lag (SoH only) ----
            # Dolphin never takes this path, and neither does SoH at 0ms: the buffer is
            # only touched when lag is actually configured, so the zero-lag path is a
            # straight passthrough with no queueing, no allocation and no scan.
            now = time.perf_counter()
            lag_s = 0.0
            if target == "pc":
                lag_s = max(0.0, min(100.0, float(cfg.get("input_lag_ms", 0.0)))) / 1000.0
            if lag_s <= 0.0:
                emit = payload
                if delay_buf:
                    delay_buf.clear()
            else:
                delay_buf.append((now, payload))
                while len(delay_buf) > 1 and delay_buf[0][0] < now - MAX_LAG_S:
                    delay_buf.popleft()
                emit = NEUTRAL          # window not filled yet -> hold neutral
                target_t = now - lag_s
                for ts, pl in reversed(delay_buf):
                    if ts <= target_t:
                        emit = pl
                        break

            if self.enabled:
                pad.left_joystick(emit["ls"][0], emit["ls"][1])
                pad.right_joystick(emit["rs"][0], emit["rs"][1])
                pad.left_trigger(emit["lt"])
                pad.right_trigger(emit["rt"])
                eb = emit["buttons"]
                for i, const in button_map.items():
                    (pad.press_button if eb.get(i) else pad.release_button)(const)
                for const, down in zip(dpad_consts, emit["dpad"]):
                    (pad.press_button if down else pad.release_button)(const)
                pad.update()

            # Telemetry is display-only, so build it at TELEMETRY_HZ rather than HZ.
            # The output above has already been sent; nothing here affects latency.
            if now >= next_tele:
                next_tele = now + tele_period
                t["lag_ms"] = lag_s * 1000.0
                t["lag_holding"] = lag_s > 0.0 and emit is NEUTRAL
                t["buttons"] = [BUTTON_NAMES[i] for i, d in pressed.items() if d]
                t["dpad"] = "".join(c for c, d in zip("UDLR", dpad) if d)
                t["triggers"] = (lt_b, rt_b)
                t["rstick"] = (rx, ry)
                # Raw, unmapped device view - used to find real button/axis indices.
                t["raw_axes"] = [_axis_safe(js, i) for i in range(naxes)]
                t["raw_buttons"] = [i for i in range(nbuttons) if js.get_button(i)]
                t["raw_hat"] = (hx, hy)
                t["dev_name"] = dev_name
                self.telemetry = t

            # Precise pacing. pygame Clock.tick uses SDL_Delay, whose granularity is too
            # coarse at 1kHz; time.sleep on Python 3.11+/Windows uses a high-resolution
            # waitable timer. Re-baselining on overrun stops the loop chasing lost time.
            next_t += period
            slack = next_t - time.perf_counter()
            if slack > 0:
                time.sleep(slack)
            elif slack < -period:
                next_t = time.perf_counter()

        try:
            pad.reset(); pad.update()
        except Exception:
            pass
        pygame.joystick.quit()
        self.status = "stopped"


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------
class App:
    def __init__(self, root):
        self.root = root
        self.engine = Engine()
        self.vars = {}
        self._known_devices = None
        self._building = True
        root.title(f"pyESS {__version__} - OoT stick shaper")
        root.minsize(560, 0)

        self._build_target(root)
        self._build_zones(root)
        self._build_map(root)
        self._build_live(root)
        self._building = False

        self.engine.start()
        self._tick()
        root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---- widgets -------------------------------------------------------
    def _build_target(self, root):
        f = ttk.LabelFrame(root, text="Output target", padding=8)
        f.pack(fill="x", padx=8, pady=(8, 4))
        self.target_var = tk.StringVar(value=self.engine.target)
        for key, label in TARGETS:
            ttk.Radiobutton(f, text=label, value=key, variable=self.target_var,
                            command=self.on_target).pack(anchor="w")

        row = ttk.Frame(f)
        row.pack(fill="x", pady=(6, 0))
        # (4) real device NAMES, not a bare index you have to guess
        ttk.Label(row, text="Controller:").pack(side="left")
        self.dev_var = tk.StringVar(value="")
        self.dev_combo = ttk.Combobox(row, textvariable=self.dev_var, width=30,
                                      state="readonly", values=[])
        self.dev_combo.pack(side="left", padx=(4, 6))
        self.dev_combo.bind("<<ComboboxSelected>>", lambda _e: self.on_device())
        ttk.Button(row, text="Rescan", width=8,
                   command=self.refresh_devices).pack(side="left")

        row2 = ttk.Frame(f)
        row2.pack(fill="x", pady=(6, 0))
        # (3) ON by default. Silently not sending cost a whole debugging session once:
        # readings looked plausible because they were the RAW stick passing through.
        self.enable_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row2, text="Send to virtual pad", variable=self.enable_var,
                        command=self.on_enable).pack(side="left")
        self.lbl_notsending = ttk.Label(row2, text="", foreground="#c00",
                                        font=("", 9, "bold"))
        self.lbl_notsending.pack(side="left", padx=(10, 0))

        # Artificial input lag - SoH only (a testing / self-handicap aid).
        lagrow = ttk.Frame(f)
        lagrow.pack(fill="x", pady=(6, 0))
        self.lag_label = ttk.Label(lagrow, text="Input lag (ms) [SoH]:")
        self.lag_label.pack(side="left")
        from pyess_config import load_zones as _lz
        self.lag_var = tk.DoubleVar(value=float(_lz("soh", verbose=False).get("input_lag_ms", 0.0)))
        self.lag_scale = ttk.Scale(lagrow, from_=0, to=100, variable=self.lag_var,
                                   orient="horizontal", command=lambda _v: self.on_lag())
        self.lag_scale.pack(side="left", fill="x", expand=True, padx=6)
        self.lag_val = ttk.Label(lagrow, width=7, text="0 ms")
        self.lag_val.pack(side="left")

        self._sync_lag_enabled()

    def _sync_lag_enabled(self):
        pc = self.target_var.get() == "pc"
        state = "normal" if pc else "disabled"
        self.lag_scale.configure(state=state)
        self.lag_label.configure(foreground="" if pc else "#aaa")
        self.lag_val.configure(text=f"{self.lag_var.get():.0f} ms" if pc else "SoH only")

    def on_lag(self):
        if self._building:
            return
        self.lag_val.configure(text=f"{self.lag_var.get():.0f} ms")
        self._push()

    def _build_zones(self, root):
        f = ttk.LabelFrame(root, text="Zones (live)", padding=8)
        f.pack(fill="both", expand=True, padx=8, pady=4)
        cfg = self.engine.cfg
        for i, (key, label, lo, hi, desc) in enumerate(ZONE_SPECS):
            ttk.Label(f, text=label, width=17).grid(row=i, column=0, sticky="w", pady=1)
            var = tk.DoubleVar(value=float(cfg.get(key, 0.0)))
            self.vars[key] = var
            s = ttk.Scale(f, from_=lo, to=hi, variable=var, orient="horizontal",
                          command=lambda _v, k=key: self.on_zone(k))
            s.grid(row=i, column=1, sticky="ew", padx=6)
            ent = ttk.Entry(f, width=8)
            ent.grid(row=i, column=2)
            ent.insert(0, f"{var.get():.3f}")
            ent.bind("<Return>", lambda _e, k=key: self.on_entry(k))
            var._entry = ent
            ttk.Label(f, text=desc, foreground="#888").grid(row=i, column=3, sticky="w", padx=(6, 0))
        f.columnconfigure(1, weight=1)

        r = len(ZONE_SPECS)
        self.ess_var = tk.BooleanVar(value=bool(cfg.get("ess_enable", True)))
        ttk.Checkbutton(f, text="ESS band enabled", variable=self.ess_var,
                        command=lambda: self.on_zone("ess_enable")).grid(
            row=r, column=0, columnspan=2, sticky="w", pady=(6, 0))

        btns = ttk.Frame(f)
        btns.grid(row=r + 1, column=0, columnspan=4, sticky="w", pady=(8, 0))
        self.btn_save = ttk.Button(btns, text="Save", command=self.on_save)
        self.btn_save.pack(side="left")
        ttk.Button(btns, text="Reload from file", command=self.on_reload).pack(side="left", padx=6)
        self.lbl_dirty = ttk.Label(btns, text="", foreground="#b8860b")
        self.lbl_dirty.pack(side="left", padx=(10, 0))
        self._dirty = False

    # ---- zone map ------------------------------------------------------
    def _build_map(self, root):
        """2D map of PHYSICAL stick space, coloured by the resulting in-game state.
        Answers the question the sliders can't: 'where do I hold the stick for ESS?'"""
        f = ttk.LabelFrame(root, text="Zone map (physical stick -> in-game state)",
                           padding=8)
        f.pack(fill="x", padx=8, pady=4)
        row = ttk.Frame(f)
        row.pack(fill="x")
        self.map_canvas = tk.Canvas(row, width=MAP_PX, height=MAP_PX,
                                    highlightthickness=1, highlightbackground="#999")
        self.map_canvas.pack(side="left")
        self.map_img = tk.PhotoImage(width=MAP_PX, height=MAP_PX)
        self.map_canvas.create_image(0, 0, anchor="nw", image=self.map_img)
        # crosshair + live stick dot, drawn over the image
        c = MAP_PX / 2
        # Deflection rings so a zone boundary can be read off as a percentage of throw,
        # rather than only compared to its neighbours.
        for frac in (0.25, 0.5, 0.75):
            r = frac * c
            self.map_canvas.create_oval(c - r, c - r, c + r, c + r,
                                        outline="#ffffff", dash=(2, 4))
        self.map_canvas.create_line(c, 0, c, MAP_PX, fill="#8a8a8a")
        self.map_canvas.create_line(0, c, MAP_PX, c, fill="#8a8a8a")
        # Two-tone marker so it reads against all three zone colours. Recreated (not
        # moved) each tick - see _move_dot.
        self.map_dot = ()
        self._dot_at = None
        self._move_dot(c, c)

        legend = ttk.Frame(row)
        legend.pack(side="left", padx=12, anchor="n")
        for colour, label in ((MAP_NEUTRAL, "NEUTRAL  (deadzone)"),
                              (MAP_ESS, "ESS  (pivot in place)"),
                              (MAP_WALK, "WALK  (speed ramps)"),
                              (MAP_RUN, "RUN  (still ramping)"),
                              (MAP_FULLRUN, "FULL RUN  (max speed)")):
            r = ttk.Frame(legend)
            r.pack(anchor="w", pady=1)
            sw = tk.Canvas(r, width=12, height=12, highlightthickness=0)
            sw.create_rectangle(0, 0, 12, 12, fill=colour, outline=colour)
            sw.pack(side="left", padx=(0, 5))
            ttk.Label(r, text=label).pack(side="left")

        ttk.Label(legend, text="dashed rings = 25 / 50 / 75% deflection",
                  foreground="#888").pack(anchor="w", pady=(8, 0))

        self._map_job = None
        self.schedule_map()

    def _move_dot(self, px, py):
        """Redraw the live stick marker.

        Deleting and recreating rather than coords()-ing it: moving a canvas item that
        sits on top of a create_image leaves the old pixels behind (the canvas does not
        always invalidate the vacated rect). Delete forces that region to repaint.
        Outer dark ring + inner white ring keeps it legible on dark grey, orange and blue.
        """
        for item in self.map_dot:
            self.map_canvas.delete(item)
        self.map_dot = (
            self.map_canvas.create_oval(px - 5, py - 5, px + 5, py + 5,
                                        outline="#000", width=3),
            self.map_canvas.create_oval(px - 5, py - 5, px + 5, py + 5,
                                        outline="#fff", width=1),
        )

    def schedule_map(self, delay=180):
        """Debounced redraw - sampling the grid is far too slow to do per slider tick."""
        if self._map_job is not None:
            self.root.after_cancel(self._map_job)
        self._map_job = self.root.after(delay, self._redraw_map)

    def _redraw_map(self):
        self._map_job = None
        cfg, target = self.engine.cfg, self.engine.target
        n = MAP_SAMPLES // 2                  # evaluate one quadrant, mirror it (4x fewer)
        scale = MAP_PX // MAP_SAMPLES
        compute = self.engine.compute
        walk, run, full = vc.GAME_WALK_MAG, vc.GAME_RUN_MAG, vc.GAME_FULL_RUN_MAG
        # 1. evaluate one quadrant as zone ids (0 neutral / 1 ess / 2 walk)
        quad = []
        for iy in range(n):
            ly = (iy + 0.5) / n
            quad.append([0 if (m := compute((ix + 0.5) / n, ly, cfg, target)["mag"]) == 0
                         else (1 if m < walk else
                               (2 if m <= run else (3 if m < full else 4)))
                         for ix in range(n)])
        # 2. mirror to the full grid (ids, so edges can be found across the seam)
        grid = [r[::-1] + r for r in
                ([quad[i] for i in range(n - 1, -1, -1)] + quad)]
        # 3. colourise - only legend colours, nothing else
        palette = (MAP_NEUTRAL, MAP_ESS, MAP_WALK, MAP_RUN, MAP_FULLRUN)
        rows = []
        for row in grid:
            full = [palette[v] for v in row for _ in range(scale)]
            rows.extend(["{" + " ".join(full) + "}"] * scale)
        self.map_img.put(" ".join(rows))

    def _build_live(self, root):
        f = ttk.LabelFrame(root, text="Live", padding=8)
        f.pack(fill="x", padx=8, pady=(4, 8))
        self.lbl_status = ttk.Label(f, text="starting...", foreground="#888")
        self.lbl_status.pack(anchor="w")
        self.lbl_stick = ttk.Label(f, font=("Consolas", 9), text="")
        self.lbl_stick.pack(anchor="w")
        self.lbl_out = ttk.Label(f, font=("Consolas", 9), text="")
        self.lbl_out.pack(anchor="w")
        self.lbl_state = ttk.Label(f, font=("Consolas", 12, "bold"), text="")
        self.lbl_state.pack(anchor="w", pady=(2, 0))
        self.lbl_pass = ttk.Label(f, font=("Consolas", 9), foreground="#555", text="")
        self.lbl_pass.pack(anchor="w")
        self.bar = ttk.Progressbar(f, maximum=60.0, length=380)
        self.bar.pack(fill="x", pady=(4, 0))
        ttk.Label(f, text="in-game magnitude   0 ......... 20 (walk) ......... 60",
                  foreground="#888").pack(anchor="w")

        # Collapsed by default: essential when diagnosing a pad whose buttons do not
        # match the standard layout, pure clutter the rest of the time.
        self.raw_open = tk.BooleanVar(value=False)
        rf = ttk.Frame(root)
        rf.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Checkbutton(rf, text="Show raw input (find button/axis indices)",
                        variable=self.raw_open, command=self._toggle_raw).pack(anchor="w")
        self.raw_body = ttk.Frame(rf)
        self.lbl_dev = ttk.Label(self.raw_body, font=("Consolas", 9), foreground="#555", text="")
        self.lbl_dev.pack(anchor="w")
        self.lbl_raw_ax = ttk.Label(self.raw_body, font=("Consolas", 9), text="")
        self.lbl_raw_ax.pack(anchor="w")
        self.lbl_raw_btn = ttk.Label(self.raw_body, font=("Consolas", 10, "bold"), text="")
        self.lbl_raw_btn.pack(anchor="w")

    def _toggle_raw(self):
        if self.raw_open.get():
            self.raw_body.pack(fill="x", pady=(4, 0))
        else:
            self.raw_body.forget()

    # ---- events --------------------------------------------------------
    def _current_cfg(self):
        cfg = dict(self.engine.cfg)
        for key, var in self.vars.items():
            cfg[key] = float(var.get())
        cfg["ess_enable"] = bool(self.ess_var.get())
        if self.target_var.get() == "pc":          # these three are SoH-only
            cfg["input_lag_ms"] = float(self.lag_var.get())
        else:
            cfg.pop("input_lag_ms", None)
        return cfg

    def _push(self):
        self.engine.cfg = self._current_cfg()      # atomic swap
        self.schedule_map()
        self._set_dirty(True)

    def _set_dirty(self, dirty):
        if dirty == getattr(self, "_dirty", None):
            return
        self._dirty = dirty
        self.btn_save.configure(text="Save *" if dirty else "Save")
        self.lbl_dirty.configure(text="unsaved changes" if dirty else "")

    def on_zone(self, key):
        if self._building:
            return
        var = self.vars.get(key)
        if var is not None and hasattr(var, "_entry"):
            var._entry.delete(0, "end")
            var._entry.insert(0, f"{var.get():.3f}")
        self._push()

    def on_entry(self, key):
        var = self.vars[key]
        try:
            var.set(float(var._entry.get()))
        except ValueError:
            var._entry.delete(0, "end")
            var._entry.insert(0, f"{var.get():.3f}")
            return
        self._push()

    def on_target(self):
        self.engine.target = self.target_var.get()
        # reload target-specific keys (max_axis_range, gate_compensation, input_lag_ms)
        tgt = "dolphin" if self.engine.target == "dolphin" else "soh"
        fresh = load_zones(tgt, verbose=False)
        if "input_lag_ms" in fresh:                # keep slider in step with the target
            self._building = True
            self.lag_var.set(float(fresh["input_lag_ms"]))
            self._building = False
        self._sync_lag_enabled()
        cfg = self._current_cfg()
        # Drive this off TARGET_KEYS, never a hand-written list: every per-target key
        # added since (round_compensation, cancel_soh_octagon, soh_*) was silently
        # dropped on a target switch when this was maintained by hand.
        for k in TARGET_KEYS:
            if k in fresh:
                cfg[k] = fresh[k]
            else:
                cfg.pop(k, None)
        self.engine.cfg = cfg

    def refresh_devices(self):
        """List real controller names. Enumerating needs pygame, which the engine owns,
        so ask it for the snapshot it took when it last opened the joystick subsystem."""
        names = self.engine.list_devices()
        self.dev_combo["values"] = names or ["(no controller found)"]
        if names:
            idx = min(self.engine.device_index, len(names) - 1)
            self.dev_var.set(names[idx])
        else:
            self.dev_var.set("(no controller found)")

    def on_device(self):
        names = list(self.dev_combo["values"])
        try:
            self.engine.device_index = names.index(self.dev_var.get())
        except ValueError:
            return
        self.engine.request_restart()

    def on_enable(self):
        self.engine.enabled = bool(self.enable_var.get())

    def on_save(self):
        # No success dialog: saving is routine and a modal on every save is friction.
        # The "Save *" / "unsaved changes" indicator already reports the state.
        try:
            tgt = "dolphin" if self.engine.target == "dolphin" else "soh"
            save_zones(self._current_cfg(), tgt)
            self._set_dirty(False)
        except Exception as e:
            messagebox.showerror("Save failed", str(e))

    def on_reload(self):
        tgt = "dolphin" if self.engine.target == "dolphin" else "soh"
        cfg = load_zones(tgt, verbose=False)
        self._building = True
        for key, var in self.vars.items():
            if key in cfg:
                var.set(float(cfg[key]))
                var._entry.delete(0, "end")
                var._entry.insert(0, f"{var.get():.3f}")
        self.ess_var.set(bool(cfg.get("ess_enable", True)))
        if "input_lag_ms" in cfg:
            self.lag_var.set(float(cfg["input_lag_ms"]))
        self._building = False
        self._sync_lag_enabled()
        self.engine.cfg = cfg
        self._set_dirty(False)
        self.schedule_map()

    # ---- live refresh --------------------------------------------------
    def _tick(self):
        e = self.engine
        self.lbl_status.config(
            text=(e.error or e.status),
            foreground="#c00" if e.error else "#888")
        self.lbl_notsending.config(
            text="" if self.enable_var.get() else "NOT SENDING - game sees your raw stick")
        # The engine enumerates devices when it opens a session, which happens after the
        # GUI is built, so sync the dropdown whenever the list actually changes.
        devs = e.list_devices()
        if devs != self._known_devices:
            self._known_devices = devs
            self.refresh_devices()
        t = e.telemetry
        if t:
            lx, ly = t["stick"]; ox, oy = t["shaped"]
            # live stick position on the zone map (canvas y grows downward)
            px = (shaping.clamp(lx, -1.0, 1.0) + 1.0) / 2.0 * MAP_PX
            py = (1.0 - shaping.clamp(ly, -1.0, 1.0)) / 2.0 * MAP_PX
            if (round(px), round(py)) != self._dot_at:      # skip no-op repaints
                self._dot_at = (round(px), round(py))
                self._move_dot(px, py)
            self.lbl_stick.config(
                text=f"stick {lx:+.3f},{ly:+.3f}   shaped {ox:+.3f},{oy:+.3f}")
            gc = t["gc"]
            gctxt = f"   GC {gc[0]:3d},{gc[1]:3d}" if gc else ""
            self.lbl_out.config(
                text=f"target cur {t['cur'][0]:+4d},{t['cur'][1]:+4d}{gctxt}"
                     f"   -> in-game {t['achieved'][0]:+4d},{t['achieved'][1]:+4d}")
            state = t["state"]
            colour = {"NEUTRAL": "#888", "ESS": "#b8860b", "WALK": "#2a8f7f",
                      "RUN": "#2f7fa8", "FULL RUN": "#15456b"}[state]
            lag = t.get("lag_ms", 0.0)
            lag_txt = ""
            if lag > 0:
                lag_txt = f"   +{lag:.0f}ms lag" + ("  (filling)" if t.get("lag_holding") else "")
            self.lbl_state.config(text=f"{state}   mag {t['mag']:.1f}{lag_txt}", foreground=colour)
            self.bar["value"] = t["mag"]
            rs = t.get("rstick", (0.0, 0.0))
            tr = t.get("triggers", (0, 0))
            btns = " ".join(t.get("buttons", [])) or "-"
            dp = t.get("dpad", "") or "-"
            self.lbl_pass.config(
                text=f"passthrough  R{rs[0]:+.2f},{rs[1]:+.2f}  LT{tr[0]:3d} RT{tr[1]:3d}"
                     f"  dpad {dp:4}  btn {btns}")
            ax = t.get("raw_axes", [])
            self.lbl_dev.config(text=f"device: {t.get('dev_name','?')}   hat {t.get('raw_hat',(0,0))}")
            self.lbl_raw_ax.config(
                text="axes  " + "  ".join(f"{i}:{v:+.2f}" for i, v in enumerate(ax)))
            rb = t.get("raw_buttons", [])
            self.lbl_raw_btn.config(
                text="buttons down: " + (", ".join(str(i) for i in rb) if rb else "-"),
                foreground="#0a0" if rb else "#888")
        self.root.after(40, self._tick)

    def on_close(self):
        if getattr(self, "_dirty", False):
            if not messagebox.askokcancel(
                    "Unsaved changes",
                    "Your zone changes have not been saved.\n\nClose anyway?"):
                return
        self.engine.stop()
        self.root.after(150, self.root.destroy)


def selftest():
    """Headless check: engine math works for both targets without any hardware."""
    e = Engine()
    ok = True
    for target in ("pc", "dolphin"):
        e.target = target
        e.cfg = load_zones("dolphin" if target == "dolphin" else "soh", verbose=False)
        print(f"--- {target} (deadzone={e.cfg['deadzone']}, "
              f"ess {e.cfg['ess_output_start']}..{e.cfg['ess_output_end']})")
        for p in (0.05, 0.10, 0.20, 0.35, 0.50, 1.00):
            t = e.compute(p, 0.0)
            gc = f" GC {t['gc'][0]:3d}" if t["gc"] else "        "
            print(f"   stick {p:4.2f} -> shaped {t['shaped'][0]:.3f} "
                  f"cur {t['cur'][0]:3d}{gc} -> in-game {t['achieved'][0]:3d} "
                  f"mag {t['mag']:5.1f}  {t['state']}")
    # Both targets must agree on in-game magnitude - that is the whole mirror premise.
    # Sweep the full ANGLE range, not just cardinal: SoH's octagon transform is skipped
    # when either axis is zero, so a cardinal-only sweep reports 1.0 while the real
    # worst case (45 degrees) is 7.1.
    soh_cfg = load_zones("soh", verbose=False)
    dol_cfg = load_zones("dolphin", verbose=False)
    worst, worst_at = 0.0, None
    for deg in range(0, 91, 3):
        th = math.radians(deg)
        for i in range(1, 51):
            p = i / 50.0
            lx, ly = p * math.cos(th), p * math.sin(th)
            a = e.compute(lx, ly, soh_cfg, "pc")["mag"]
            b = e.compute(lx, ly, dol_cfg, "dolphin")["mag"]
            d = abs(a - b)
            if d > worst:
                worst, worst_at = d, (deg, round(p, 2), a, b)
    print(f"\nmirror check (full angle sweep): worst |pc-dolphin| = {worst:.1f}")
    if worst_at:
        print(f"  worst at {worst_at[0]} deg, deflection {worst_at[1]}: "
              f"SoH mag {worst_at[2]:.1f} vs Dolphin {worst_at[3]:.1f}")
    if worst > 1.501:
        print("  WARNING: targets disagree by more than quantisation")
        if not soh_cfg.get("cancel_soh_octagon", False):
            print("  HINT: set cancel_soh_octagon=true in the soh target - SoH applies")
            print("        its own octagon transform (x1.148 at 45 deg) that Dolphin has not.")
        ok = False
    return ok


if __name__ == "__main__":
    if "--version" in sys.argv:
        print(f"pyESS {__version__}")
        sys.exit(0)
    if "--selftest" in sys.argv:
        sys.exit(0 if selftest() else 1)
    root = tk.Tk()
    App(root)
    root.mainloop()
