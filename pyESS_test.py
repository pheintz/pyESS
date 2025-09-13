"""
winpad_remap.py — Windows controller → (virtual) Xbox 360 controller remapper
STRICT mode (close to ESS-Adapter behavior) + Headless + Live Web UI
--------------------------------------------------------------------
- Reads a physical Windows gamepad (pygame), outputs to a virtual Xbox 360 pad (vgamepad / ViGEmBus).
- Optional N64 octagon mapping via pyESS_test.GCN64Map (if provided next to this file).
- Optional VC inverse via pyESS_test.VCInverseMap/VCInverse (if provided).
- Headless loop (no window), local web UI for live edits (repo-aligned toggles only).

Setup
1) Install ViGEmBus from https://vigembus.org/ (required for vgamepad)
2) pip install pygame vgamepad Flask Werkzeug numpy
3) Place pyESS_test.py next to this file for N64/VC mappings (optional).
4) Run: python winpad_remap.py  → then open http://127.0.0.1:8765

Notes
- STRICT_MODE=True: disables custom "ESS zone shaping" and sets smoothing=0.0 to stay faithful to ESS-Adapter.
- Exposed controls in UI: ESS mapping on/off, mapper mode, VC inverse, Trigger Fix + threshold.
"""

# ---------- Imports ----------
import math
import time
import sys
import os
import json
import traceback
import threading
from dataclasses import dataclass
from typing import Tuple, Optional

import pygame
from vgamepad import VX360Gamepad, XUSB_BUTTON
from flask import Flask, request, jsonify
from werkzeug.serving import make_server

# Follow ESS-Adapter behavior as closely as possible
STRICT_MODE = True

# ---------- Optional mapping imports from your local file ----------
HAVE_GCN64 = False
HAVE_VCINV = False
VCInverse = None
try:
    from pyESS_test import GCN64Map  # N64 octagon mapper (your file)
    HAVE_GCN64 = True
except Exception:
    pass

try:
    # Try two common names the user might have
    from pyESS_test import VCInverseMap as VCInverse  # class with .map(x,y)
    HAVE_VCINV = True
except Exception:
    try:
        from pyESS_test import VCInverse as VCInverse
        HAVE_VCINV = True
    except Exception:
        pass

# ---------- Config ----------
@dataclass
class Config:
    # Physical joystick index (0 = first detected)
    joystick_index: int = 0

    # Axis indices (typical XInput via pygame):
    # 0=LS X, 1=LS Y, 2=RS X, 3=RS Y, 4=LT, 5=RT
    axis_left_x: int = 0
    axis_left_y: int = 1
    axis_right_x: int = 2
    axis_right_y: int = 3

    # Triggers handling
    axis_lt: Optional[int] = 4
    axis_rt: Optional[int] = 5
    # Some DirectInput devices expose a SINGLE combined trigger axis in [-1..1]
    # where negative = LT, positive = RT. If you have that, set this and set axis_lt/rt=None
    axis_combined_triggers: Optional[int] = None

CFG = Config()

# ---------- Runtime Settings (ESS-Adapter-aligned) ----------
@dataclass
class Settings:
    mapper_mode: str = "n64_octagon" if HAVE_GCN64 else "none"  # ESS default ON if available
    mapping_enabled: bool = True if HAVE_GCN64 else False
    vc_inverse_enabled: bool = False  # use only if VCInverse is available

    trigger_fix_enabled: bool = True
    trigger_threshold: float = 0.35   # 0..0.95 (normalized float)

    input_deadzone: float = 0.08      # applied on input floats [-1..1]
    smoothing_alpha: float = 0.0 if STRICT_MODE else 0.25  # EMA on left stick output

    # Keep fields (not used in STRICT) for compatibility with prior JSON files
    ess_zone_inner: int = 36
    ess_zone_outer: int = 85
    ess_compress: float = 0.35
    ess_shaping_enabled: bool = False if STRICT_MODE else True

    # Headless: overlay disabled (no window)
    input_display_enabled: bool = False

SET = Settings()

SETTINGS_FILE = "winpad_settings.json"
LIVE_SETTINGS_FILE = "winpad_live.json"
_live_mtime = None

# Global mapper and lock so the UI thread can rebuild safely
mapper = None
_mapper_lock = threading.Lock()

# ---------- Utilities ----------
def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v

def faxis_to_signed127(v: float) -> int:
    v = clamp(v, -1.0, 1.0)
    return int(round(v * 127))

def signed127_to_xinput(v: int) -> int:
    return int(clamp(v * 256, -32768, 32767))

def trigger_faxis_to_255(v: float) -> int:
    v = clamp(v, -1.0, 1.0)
    return int(round((v + 1.0) * 0.5 * 255))

# ---------- Mappers ----------
class BaseMapper:
    def process(self, sx: int, sy: int) -> Tuple[int, int]:
        return sx, sy

class N64OctagonMapper(BaseMapper):
    def __init__(self):
        if not HAVE_GCN64:
            raise RuntimeError("N64OctagonMapper requires pyESS_test.GCN64Map.")
        self.gc_to_n64 = GCN64Map()
    def process(self, sx: int, sy: int) -> Tuple[int, int]:
        mx, my = self.gc_to_n64.map(sx, sy)
        return int(round(mx)), int(round(my))

class VCInverseMapper(BaseMapper):
    def __init__(self):
        if not HAVE_VCINV or VCInverse is None:
            raise RuntimeError("VC inverse requested but not found in pyESS_test.py")
        self._inv = VCInverse()
    def process(self, sx: int, sy: int) -> Tuple[int, int]:
        mx, my = self._inv.map(sx, sy) if hasattr(self._inv, "map") else self._inv(sx, sy)
        return int(round(mx)), int(round(my))

class ChainMapper(BaseMapper):
    def __init__(self, *mappers):
        self._chain = [m for m in mappers if m is not None]
    def process(self, sx: int, sy: int) -> Tuple[int, int]:
        x, y = sx, sy
        for m in self._chain:
            x, y = m.process(x, y)
        return x, y

# (Kept for compatibility; disabled in STRICT mode)
def apply_ess_zones(x: int, y: int) -> Tuple[int, int]:
    r = math.hypot(x, y)
    if r <= 1e-6:
        return x, y
    inner = max(0, min(127, SET.ess_zone_inner))
    outer = max(inner, min(127, SET.ess_zone_outer))
    comp = max(0.0, min(1.0, SET.ess_compress))
    if r < inner or r > outer or comp >= 0.999:
        return x, y
    new_r = inner + (r - inner) * comp
    scale = new_r / r
    return int(round(x * scale)), int(round(y * scale))

# ---------- Settings load/save & live apply ----------
def _apply_settings_dict(d: dict):
    for k in [
        "mapper_mode", "mapping_enabled", "vc_inverse_enabled",
        "trigger_fix_enabled", "trigger_threshold",
        "input_deadzone", "smoothing_alpha",
        "ess_zone_inner", "ess_zone_outer", "ess_compress", "ess_shaping_enabled",
    ]:
        if k in d:
            setattr(SET, k, d[k])
    # clamps
    SET.input_deadzone = float(max(0.0, min(0.5, SET.input_deadzone)))
    SET.smoothing_alpha = float(max(0.0, min(0.95, SET.smoothing_alpha)))
    SET.trigger_threshold = float(max(0.0, min(0.95, SET.trigger_threshold)))
    SET.ess_zone_inner = int(max(0, min(127, SET.ess_zone_inner)))
    SET.ess_zone_outer = int(max(SET.ess_zone_inner, min(127, SET.ess_zone_outer)))
    SET.ess_compress = float(max(0.0, min(1.0, SET.ess_compress)))
    if STRICT_MODE:
        # lock down extras for closer parity with ESS-Adapter
        SET.ess_shaping_enabled = False
        SET.smoothing_alpha = 0.0

def load_settings():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        _apply_settings_dict(data)
        return True
    except Exception:
        return False

def save_settings():
    data = {
        "mapper_mode": SET.mapper_mode,
        "mapping_enabled": SET.mapping_enabled,
        "vc_inverse_enabled": getattr(SET, "vc_inverse_enabled", False),
        "trigger_fix_enabled": SET.trigger_fix_enabled,
        "trigger_threshold": SET.trigger_threshold,
        "input_deadzone": SET.input_deadzone,
        "smoothing_alpha": SET.smoothing_alpha,
        "ess_zone_inner": SET.ess_zone_inner,
        "ess_zone_outer": SET.ess_zone_outer,
        "ess_compress": SET.ess_compress,
        "ess_shaping_enabled": SET.ess_shaping_enabled,
    }
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return True
    except Exception:
        return False

# ---------- Web UI (Flask) ----------
app = Flask(__name__)

def _settings_dict():
    # Only expose the repo-aligned controls
    return {
        "mapper_mode": SET.mapper_mode,
        "mapping_enabled": SET.mapping_enabled,
        "vc_inverse_enabled": getattr(SET, "vc_inverse_enabled", False),
        "trigger_fix_enabled": SET.trigger_fix_enabled,
        "trigger_threshold": SET.trigger_threshold,
    }

@app.get("/api/state")
def api_get_state():
    return jsonify(_settings_dict())

@app.post("/api/state")
def api_set_state():
    data = request.get_json(force=True, silent=True) or {}
    _apply_settings_dict(data)
    global mapper
    with _mapper_lock:
        mapper = active_mapper()
    return jsonify({"ok": True, "applied": _settings_dict()})

def _build_index_html():
    # UI: only mapping toggle, mode, VC inverse, trigger fix & threshold (ESS-Adapter-aligned)
    return """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>winpad_remap — Live Controls (Strict)</title>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<style>
 body{font-family:system-ui,Segoe UI,Arial,sans-serif;max-width:720px;margin:24px auto;padding:0 12px}
 h1{font-size:20px} section{border:1px solid #ddd;border-radius:12px;padding:14px;margin:12px 0}
 label{display:block;margin:8px 0 4px} .pill{display:inline-block;padding:6px 10px;border:1px solid #ccc;border-radius:999px;cursor:pointer;margin-right:8px}
 input[type=range]{width:100%} .fine{opacity:.8;font-size:12px} .grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
 .btn{padding:8px 12px;border:1px solid #444;border-radius:8px;background:#111;color:#fff;cursor:pointer}
</style>
<script>
async function loadState(){
  const r = await fetch('/api/state'); const j = await r.json();
  for (const k in j){
    const el = document.querySelector('[data-key=\"'+k+'\"]');
    if(!el) continue;
    if(el.type==='checkbox'){ el.checked = !!j[k]; }
    else if(el.type==='radio'){ document.querySelectorAll('[data-key=\"'+k+'\"]').forEach(rb=>rb.checked = (rb.value===j[k])); }
    else { el.value = j[k]; }
    const out = el.parentElement.querySelector('.out'); if(out) out.textContent = el.value;
  }
}
async function applyChanges(){
  const data = {};
  document.querySelectorAll('[data-key]').forEach(el=>{
    let v = (el.type==='checkbox') ? el.checked : (el.type==='number' ? parseFloat(el.value) : (el.type==='range'? parseFloat(el.value) : el.value));
    if(el.type==='radio' && !el.checked) return;
    data[el.dataset.key] = v;
  });
  const r = await fetch('/api/state', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
  if(!r.ok){ alert('Apply failed'); }
}
function hookup(){
  document.querySelectorAll('input').forEach(el=>{
    if(el.type==='range'){ el.addEventListener('input', e=>{ const out = el.parentElement.querySelector('.out'); if(out) out.textContent = el.value; }); el.addEventListener('change', applyChanges); }
    else if(el.type==='checkbox' || el.type==='number' || el.type==='radio'){ el.addEventListener('change', applyChanges); }
    else{ el.addEventListener('blur', applyChanges); }
  });
}
window.addEventListener('DOMContentLoaded', async()=>{ await loadState(); hookup(); });
</script>
</head>
<body>
  <h1>winpad_remap — Live Controls (Strict)</h1>
  <p class="fine">This panel mirrors the features exposed by the ESS-Adapter repo: ESS mapping, VC inverse, and Trigger Fix.</p>

  <section>
    <label class="pill"><input type="checkbox" data-key="mapping_enabled"> ESS Mapping</label>
    <div style="margin-top:6px">
      <label><input type="radio" name="mode" value="none" data-key="mapper_mode"> Mode: none</label>
      <label><input type="radio" name="mode" value="n64_octagon" data-key="mapper_mode"> Mode: n64_octagon</label>
    </div>
    <label style="margin-top:8px" class="pill"><input type="checkbox" data-key="vc_inverse_enabled"> VC Inverse (if available)</label>
  </section>

  <section>
    <label class="pill"><input type="checkbox" data-key="trigger_fix_enabled"> Trigger Fix Enabled</label>
    <label>Trigger Threshold: <span class="out"></span></label>
    <input type="range" min="0" max="0.95" step="0.01" data-key="trigger_threshold">
  </section>

  <p><button class="btn" onclick="applyChanges()">Apply</button></p>
  <p class="fine">Open <code>http://localhost:8765</code> while your game is focused.</p>
</body>
</html>"""
@app.get("/")
def index():
    return _build_index_html()

def start_ui_server(host="127.0.0.1", port=8765):
    server = make_server(host, port, app)
    th = threading.Thread(target=server.serve_forever, daemon=True)
    th.start()
    print(f"[UI] Open http://{host}:{port}")
    return server

# ---------- Active mapper factory ----------
def active_mapper() -> BaseMapper:
    if SET.mapping_enabled and SET.mapper_mode == "n64_octagon" and HAVE_GCN64:
        vc = VCInverseMapper() if (SET.vc_inverse_enabled and HAVE_VCINV) else None
        return ChainMapper(vc, N64OctagonMapper())
    return BaseMapper()

# ---------- Main remapper ----------
def main():
    pygame.init()
    pygame.joystick.init()

    if pygame.joystick.get_count() == 0:
        print("No joystick detected. Connect a controller and try again.", file=sys.stderr)
        return

    js = pygame.joystick.Joystick(CFG.joystick_index)
    js.init()
    print(f"Using joystick #{CFG.joystick_index}: {js.get_name()}")
    print(f"Axes: {js.get_numaxes()}, Buttons: {js.get_numbuttons()}, Hats: {js.get_numhats()}")

    # Start web UI for live control
    try:
        start_ui_server(host="127.0.0.1", port=8765)
    except Exception as _e:
        print("[UI] Failed to start web UI:", _e)

    gamepad = VX360Gamepad()

    global mapper
    with _mapper_lock:
        mapper = active_mapper()

    # EMA smoothing state
    out_lx_prev = 0
    out_ly_prev = 0

    clock = pygame.time.Clock()

    while True:
        # Keep SDL/joystick state fresh (no window)
        pygame.event.pump()

        # Live settings reload (winpad_live.json)
        try:
            m = os.path.getmtime(LIVE_SETTINGS_FILE)
            global _live_mtime
            if _live_mtime is None or m > _live_mtime:
                _live_mtime = m
                with open(LIVE_SETTINGS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                _apply_settings_dict(data)
                with _mapper_lock:
                    mapper = active_mapper()
        except FileNotFoundError:
            pass
        except Exception:
            # ignore malformed file to avoid breaking gameplay; try again next tick
            pass

        # --------- READ INPUT ---------
        def read_axis(idx: int, invert: bool = False) -> float:
            if idx is None:
                return 0.0
            try:
                v = js.get_axis(idx)
            except Exception:
                v = 0.0
            if invert:
                v = -v
            if abs(v) < SET.input_deadzone:
                return 0.0
            return clamp(v, -1.0, 1.0)

        # Left/Right sticks (pygame Y is up positive; XInput expects inverted Y)
        lx = read_axis(CFG.axis_left_x)
        ly = read_axis(CFG.axis_left_y)
        rx = read_axis(CFG.axis_right_x)
        ry = read_axis(CFG.axis_right_y)

        # Triggers
        lt = 0
        rt = 0
        if CFG.axis_combined_triggers is not None and CFG.axis_lt is None and CFG.axis_rt is None:
            c = read_axis(CFG.axis_combined_triggers)
            if c >= 0:
                rt = trigger_faxis_to_255(c)
                lt = 0
            else:
                lt = trigger_faxis_to_255(-c)
                rt = 0
        else:
            if CFG.axis_lt is not None:
                lt = trigger_faxis_to_255(read_axis(CFG.axis_lt))
            if CFG.axis_rt is not None:
                rt = trigger_faxis_to_255(read_axis(CFG.axis_rt))

        # Buttons mapping
        def btn(i: int) -> bool:
            try:
                return bool(js.get_button(i))
            except Exception:
                return False

        btn_map = {
            0: XUSB_BUTTON.XUSB_GAMEPAD_A,
            1: XUSB_BUTTON.XUSB_GAMEPAD_B,
            2: XUSB_BUTTON.XUSB_GAMEPAD_X,
            3: XUSB_BUTTON.XUSB_GAMEPAD_Y,
            4: XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER,
            5: XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER,
            6: XUSB_BUTTON.XUSB_GAMEPAD_BACK,
            7: XUSB_BUTTON.XUSB_GAMEPAD_START,
            8: XUSB_BUTTON.XUSB_GAMEPAD_LEFT_THUMB,
            9: XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_THUMB,
        }

        for idx, xusb in btn_map.items():
            if btn(idx):
                gamepad.press_button(button=xusb)
            else:
                gamepad.release_button(button=xusb)

        # D-Pad via hat(0)
        dpad_up = dpad_down = dpad_left = dpad_right = False
        if js.get_numhats() > 0:
            hx, hy = js.get_hat(0)
            dpad_left  = (hx < 0)
            dpad_right = (hx > 0)
            dpad_up    = (hy > 0)
            dpad_down  = (hy < 0)

        gamepad.press_button(XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP)    if dpad_up    else gamepad.release_button(XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP)
        gamepad.press_button(XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN)  if dpad_down  else gamepad.release_button(XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN)
        gamepad.press_button(XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT)  if dpad_left  else gamepad.release_button(XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT)
        gamepad.press_button(XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT) if dpad_right else gamepad.release_button(XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT)

        # --------- MAP LEFT STICK ---------
        sx = faxis_to_signed127(lx)
        sy = faxis_to_signed127(ly)

        with _mapper_lock:
            local_mapper = mapper
        if SET.mapping_enabled and local_mapper is not None:
            mx, my = local_mapper.process(sx, sy)
        else:
            mx, my = sx, sy

        # ESS zone shaping disabled in STRICT_MODE (kept for compatibility)
        if (not STRICT_MODE) and getattr(SET, "ess_shaping_enabled", False):
            mx, my = apply_ess_zones(mx, my)

        # EMA smoothing and conversion to XInput (invert Y)
        new_lx = signed127_to_xinput(mx)
        new_ly = signed127_to_xinput(-my)
        # With smoothing_alpha=0 in STRICT_MODE, this is effectively passthrough
        out_lx = int(round((1.0 - SET.smoothing_alpha) * new_lx + SET.smoothing_alpha * 0))
        out_ly = int(round((1.0 - SET.smoothing_alpha) * new_ly + SET.smoothing_alpha * 0))

        # Right stick pass-through (invert Y)
        out_rx = signed127_to_xinput(faxis_to_signed127(rx))
        out_ry = signed127_to_xinput(-faxis_to_signed127(ry))

        # Send to virtual pad
        gamepad.left_joystick(x_value=out_lx, y_value=out_ly)
        gamepad.right_joystick(x_value=out_rx, y_value=out_ry)
        gamepad.left_trigger(value=lt)
        gamepad.right_trigger(value=rt)

        # Trigger Fix: analog triggers past threshold also press LB/RB
        if SET.trigger_fix_enabled:
            thr_255 = int(round(clamp((SET.trigger_threshold + 1.0) * 0.5 * 255, 0, 255)))
            if lt >= thr_255:
                gamepad.press_button(button=XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER)
            if rt >= thr_255:
                gamepad.press_button(button=XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER)

        gamepad.update()

        # Limit loop to ~250 Hz
        clock.tick(250)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception:
        print("\n[ERROR] The remapper crashed:\n")
        traceback.print_exc()
        try:
            input("\nPress Enter to close...")
        except Exception:
            pass
