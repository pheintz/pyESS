"""Pop-out input display: renders a controller skin from live telemetry.

Skins are EM Skin Editor folders chosen by the user; none ship with pyESS. The
skin refers to inputs by its own names ("a", "z", "stick_x"), so BUTTON_MAP
translates from what pyESS actually reads off the pad. It lives in
pyESS_prefs.json so a different pad or binding scheme needs no code change.

Source vocabulary:
    btn:NAME        Xbox button by pyESS name (A, B, X, Y, LB, RB, BACK, START)
    rawbtn:N        raw pygame button index, for pads pyESS has no name for
    dpad:U|D|L|R    d-pad direction
    trig:lt|rt      analog trigger, 0..1
    rstick:up|...   right stick pushed past RSTICK_ON, as a boolean
    axis:rx|ry      right stick axis, -1..1
    stick:x|y       SHAPED stick - what the game actually receives
    raw:x|y         physical stick, before shaping
"""
import os
import tkinter as tk
import webbrowser
from tkinter import colorchooser, filedialog, messagebox, ttk

from PIL import ImageTk

from pyess_skin import Renderer, Skin, SkinError

RSTICK_ON = 0.5          # deflection at which a C-direction counts as pressed
DEFAULT_BG = "#00b140"   # chroma green; shows wherever the skin is transparent
SKIN_EDITOR_URL = "https://electromodder.co.uk/skinedit/skinedit"
PLACEHOLDER = ("Input viewer supports XML skin format."
               "\n\nMake your own or download a template from the EM Spy tool:")

# Defaults for an Xbox-style pad, chosen PER SKIN TYPE. A single shared map cannot
# work: skin families reuse names for different hardware, and several skins expose one
# physical trigger twice (an analog bar plus a digital click). A map that bound both
# made one trigger pull light two elements at once. Every map below is asserted
# collision-free at import - one pad input drives at most one element.
#
# Where a skin models hardware an Xbox pad does not have (Wii ZL/ZR, the Home button),
# the default is blank rather than a guess. The Mapping dialog shows those in amber.
_FACE = {
    "a": "btn:A", "b": "btn:B", "x": "btn:X", "y": "btn:Y",
    "up": "dpad:U", "down": "dpad:D", "left": "dpad:L", "right": "dpad:R",
}
# start/select only where the family uses those names - the Classic Controller calls
# the same two buttons plus/minus, and binding both spellings collides.
_SHARED = {**_FACE, "start": "btn:START", "select": "btn:BACK"}
DEFAULT_MAPS = {
    "n64": {**_SHARED,
            "z": "trig:lt", "l": "btn:LB", "r": "btn:RB",
            "stick_x": "stick:x", "stick_y": "stick:y",
            "cup": "rstick:up", "cdown": "rstick:down",
            "cleft": "rstick:left", "cright": "rstick:right"},
    # trig_* are the analog bars; l/r are the full-press clicks on the SAME triggers,
    # so only the bars take lt/rt. Z gets RB, the usual OoT-on-a-modern-pad binding.
    "gamecube": {**_SHARED,
                 "trig_l": "trig:lt", "trig_r": "trig:rt",
                 "l": "btn:LB", "r": "", "z": "btn:RB",
                 "lstick_x": "stick:x", "lstick_y": "stick:y",
                 "cstick_x": "axis:rx", "cstick_y": "axis:ry"},
    # zl/zr sit alongside the analog bars on the same physical triggers, so they stay
    # unbound - binding both is what lit two elements from one pull.
    "classiccontroller": {**_FACE,
                          "trig_l": "trig:lt", "trig_r": "trig:rt",
                          "l": "btn:LB", "r": "btn:RB", "zl": "", "zr": "",
                          "minus": "btn:BACK", "plus": "btn:START", "home": "",
                          "lstick_x": "stick:x", "lstick_y": "stick:y",
                          "rstick_x": "axis:rx", "rstick_y": "axis:ry"},
    "nes": dict(_SHARED),
    "snes": {**_SHARED, "l": "btn:LB", "r": "btn:RB"},
}
DEFAULT_BUTTON_MAP = DEFAULT_MAPS["n64"]      # this is an OoT tool


def default_map_for(skin):
    """Defaults for a skin's declared type, adjusted to what the skin actually draws."""
    m = DEFAULT_MAPS.get((skin.type or "").lower())
    if m is None:
        m = {}
        for known in DEFAULT_MAPS.values():
            m.update(known)
    m = dict(m)
    # The shoulder click is left unbound when the skin also draws the analog bar for
    # that trigger, so one pull cannot light both. Skins with no bar have only the
    # click, so it takes the trigger instead - otherwise that shoulder never shows.
    names = set(skin.source_names())
    for bar, click, src in (("trig_l", "l", "trig:lt"), ("trig_r", "r", "trig:rt")):
        if bar not in names and click in names and not m.get(click):
            m[click] = src

    # Prune to one element per pad input, against the names THIS skin actually uses.
    # The per-type maps are collision-free on their own, but the unknown-type fallback
    # merges them and an unrecognised skin could use two spellings of one input
    # (start and plus, stick_x and lstick_x). Without this an unknown skin could
    # double-light exactly the way the Classic Controller did. Deterministic by sort
    # order; the Mapping dialog shows what was dropped and lets you choose otherwise.
    claimed = {}
    for name in sorted(names):
        src = m.get(name)
        if not src:
            continue
        if src in claimed:
            m[name] = ""
        else:
            claimed[src] = name
    return m


def collisions(skin, mapping):
    """Skin elements that would light from the same pad input. Empty is correct."""
    by = {}
    for name in skin.source_names():
        src = mapping.get(name)
        if src:
            by.setdefault(src, []).append(name)
    return {src: names for src, names in by.items() if len(names) > 1}


for _t, _m in DEFAULT_MAPS.items():
    _seen = [v for v in _m.values() if v]
    assert len(_seen) == len(set(_seen)), f"{_t} defaults double-bind {_seen}"


def resolve(source, telem, stick_mode="shaped", delayed=False):
    """Turn a mapping string into a bool (buttons) or float (axes) from telemetry.

    `delayed` reads the post-input-lag stream - what the game actually received -
    instead of what was physically pressed. The two are identical at 0ms lag.
    """
    if not source or ":" not in source:
        return False
    k = (lambda base: f"out_{base}") if delayed else (lambda base: base)
    kind, _, arg = source.partition(":")
    if kind == "btn":
        return arg in telem.get(k("buttons"), [])
    if kind == "rawbtn":
        try:
            return int(arg) in telem.get("raw_buttons", [])
        except ValueError:
            return False
    if kind == "dpad":
        return arg in (telem.get(k("dpad")) or "")
    if kind == "trig":
        lt, rt = telem.get(k("triggers"), (0, 0))
        return (lt if arg == "lt" else rt) / 255.0
    rx, ry = telem.get(k("rstick"), (0.0, 0.0))
    if kind == "rstick":
        return {"up": ry > RSTICK_ON, "down": ry < -RSTICK_ON,
                "left": rx < -RSTICK_ON, "right": rx > RSTICK_ON}.get(arg, False)
    if kind == "axis":
        return {"rx": rx, "ry": ry}.get(arg, 0.0)
    if kind in ("stick", "raw"):
        base = "shaped" if (kind == "stick" and stick_mode == "shaped") else "stick"
        vx, vy = telem.get(k(base), (0.0, 0.0))
        return vx if arg == "x" else vy
    return False


def build_state(skin, telem, button_map, stick_mode="shaped", delayed=False):
    """Map telemetry onto the source names this skin asks for."""
    return {n: resolve(button_map.get(n), telem, stick_mode, delayed)
            for n in skin.source_names()}


SOURCE_CHOICES = [""] + (
    [f"btn:{n}" for n in ("A", "B", "X", "Y", "LB", "RB", "BACK", "START",
                          "LTHUMB", "RTHUMB")]
    + [f"rawbtn:{i}" for i in range(16)]
    + [f"dpad:{d}" for d in "UDLR"]
    + ["trig:lt", "trig:rt"]
    + [f"rstick:{d}" for d in ("up", "down", "left", "right")]
    + ["axis:rx", "axis:ry", "stick:x", "stick:y", "raw:x", "raw:y"])


def skin_key(skin):
    """Stable per-skin identity for stored bindings."""
    return os.path.basename(os.path.normpath(skin.root)) or skin.name


def _per_skin(stored):
    """Read the stored overrides, tolerating the old flat one-map-for-everything form.

    The flat form is dropped rather than migrated: it was applied to every skin, so its
    entries are exactly the bindings that were wrong on all but one of them.
    """
    if not isinstance(stored, dict) or not stored:
        return {}
    if all(isinstance(v, dict) for v in stored.values()):
        return {k: dict(v) for k, v in stored.items()}
    return {}


class MappingDialog(tk.Toplevel):
    """Bind each source name a skin asks for to something on the pad.

    Shows every name from the skin, so a skin family this build has no defaults for
    (the reason a Classic Controller skin looked wrong) is visibly fixable.
    """

    def __init__(self, master, skin, mapping, on_save, defaults=None):
        super().__init__(master)
        self.title(f"Input mapping - {skin.name}")
        self.on_save = on_save
        self.skin = skin
        self.defaults = defaults or {}
        self.vars = {}

        ttk.Label(self, text="Each row is an input this skin draws. Blank = never lit.",
                  padding=(8, 8, 8, 4)).pack(anchor="w")
        self.warn = ttk.Label(self, foreground="#c00", padding=(8, 0, 8, 4))
        self.warn.pack(anchor="w")
        body = ttk.Frame(self, padding=8)
        body.pack(fill="both", expand=True)
        for i, name in enumerate(skin.source_names()):
            ttk.Label(body, text=name, width=14).grid(row=i, column=0, sticky="w", pady=1)
            var = tk.StringVar(value=mapping.get(name, ""))
            self.vars[name] = var
            box = ttk.Combobox(body, textvariable=var, values=SOURCE_CHOICES, width=16)
            box.grid(row=i, column=1, sticky="ew", padx=4)
            if not var.get():
                ttk.Label(body, text="unmapped", foreground="#b8860b").grid(
                    row=i, column=2, sticky="w")
        body.columnconfigure(1, weight=1)
        for v in self.vars.values():
            v.trace_add("write", lambda *_a: self._check())
        self._check()

        btns = ttk.Frame(self, padding=8)
        btns.pack(fill="x")
        ttk.Button(btns, text="Apply", command=self._apply).pack(side="left")
        ttk.Button(btns, text="Reset to defaults",
                   command=self._reset).pack(side="left", padx=6)
        ttk.Button(btns, text="Close", command=self.destroy).pack(side="left", padx=6)

    def _check(self):
        """Warn while editing if two elements would light from one input."""
        dup = collisions(self.skin, {n: v.get().strip()
                                     for n, v in self.vars.items()})
        self.warn.configure(text="; ".join(
            f"{src} drives {', '.join(names)}" for src, names in dup.items()))

    def _reset(self):
        for name, var in self.vars.items():
            var.set(self.defaults.get(name, ""))

    def _apply(self):
        self.on_save({n: v.get().strip() for n, v in self.vars.items()})
        self.destroy()


class InputDisplay(tk.Toplevel):
    """Resizable skin view. Controls hide for a clean capture; right-click restores."""

    def __init__(self, master, prefs, on_change):
        super().__init__(master)
        self.title("pyESS input display")
        self.minsize(200, 120)
        self.on_change = on_change
        self.skin = None
        self.renderer = None
        self._photo = None
        self._last = None

        self.bg_colour = prefs.get("display_bg_colour", DEFAULT_BG)
        # Five of the sample skins ship several background variants. The picker
        # covers the common case, so the variant is a prefs key rather than more
        # toolbar: set display_background_index to use one other than the first.
        self.background_index = int(prefs.get("display_background_index", 0) or 0)
        # Overrides are keyed BY SKIN. A single flat map applied to every skin meant a
        # binding made for one controller silently carried into another, where the
        # same names mean different hardware.
        self.all_overrides = _per_skin(prefs.get("display_button_map"))
        self.button_map = dict(DEFAULT_BUTTON_MAP)
        self.stick_mode = prefs.get("display_stick_mode", "shaped")
        self.delayed = bool(prefs.get("display_after_delay", False))

        self.bar = ttk.Frame(self, padding=4)
        self.bar.pack(fill="x", side="top")
        ttk.Button(self.bar, text="Skin folder...", command=self.choose_skin,
                   width=14).pack(side="left")
        ttk.Button(self.bar, text="Background color", width=17,
                   command=self.choose_colour).pack(side="left", padx=4)
        ttk.Button(self.bar, text="Mapping...", width=11,
                   command=self.edit_mapping).pack(side="left", padx=4)
        self.stick_var = tk.StringVar(value=self.stick_mode)
        ttk.Checkbutton(self.bar, text="shaped", variable=self.stick_var,
                        onvalue="shaped", offvalue="raw",
                        command=self._pick_stick).pack(side="left", padx=(6, 0))
        # Off = what you pressed; on = what the game received after input_lag_ms.
        # Identical at 0ms lag, so the label says which stream is on screen.
        self.delay_var = tk.BooleanVar(value=self.delayed)
        ttk.Checkbutton(self.bar, text="after delay", variable=self.delay_var,
                        command=self._pick_delay).pack(side="left", padx=(6, 0))
        ttk.Button(self.bar, text="Hide", width=6,
                   command=lambda: self.bar.pack_forget()).pack(side="right")

        self.canvas = tk.Canvas(self, highlightthickness=0, bg=self.bg_colour)
        self.canvas.pack(fill="both", expand=True)
        self.item = self.canvas.create_image(0, 0, anchor="nw")
        # Placeholder shown until a skin is loaded. Drawn on its own dark panel because
        # the background colour is user-set - plain text would be unreadable on some.
        self.hint_bg = self.canvas.create_rectangle(0, 0, 0, 0, fill="#1b1f27",
                                                    outline="")
        self.hint = self.canvas.create_text(0, 0, fill="#e8e8e8", justify="center",
                                            text=PLACEHOLDER)
        self.hint_link = self.canvas.create_text(0, 0, fill="#7fc4ff", justify="center",
                                                 text=SKIN_EDITOR_URL)
        self.canvas.tag_bind(self.hint_link, "<Button-1>",
                             lambda _e: webbrowser.open(SKIN_EDITOR_URL))
        self.canvas.tag_bind(self.hint_link, "<Enter>",
                             lambda _e: self.canvas.configure(cursor="hand2"))
        self.canvas.tag_bind(self.hint_link, "<Leave>",
                             lambda _e: self.canvas.configure(cursor=""))
        self.bind("<Button-3>", lambda _e: self.bar.pack(fill="x", side="top",
                                                         before=self.canvas))
        self.canvas.bind("<Configure>", lambda _e: self._on_resize())

        folder = prefs.get("display_skin_dir")
        if folder and os.path.isdir(folder):
            self.load_skin(folder, announce=False)
        if prefs.get("display_geometry"):
            try:
                self.geometry(prefs["display_geometry"])
            except tk.TclError:
                pass

    # ---- settings ------------------------------------------------------
    def choose_skin(self):
        folder = filedialog.askdirectory(parent=self, title="Choose a skin folder")
        if folder:
            self.load_skin(folder)

    def load_skin(self, folder, announce=True):
        try:
            skin = Skin.load(folder)
            renderer = Renderer(skin)
        except (SkinError, OSError) as e:
            if announce:
                messagebox.showerror("Skin not loaded", str(e), parent=self)
            return
        self.skin, self.renderer = skin, renderer
        self._place_hint()
        self.button_map = default_map_for(skin)
        self.button_map.update(self.all_overrides.get(skin_key(skin), {}))
        renderer.set_background(self.background_index)
        self.title(f"pyESS input display - {skin.name}")
        if announce:                      # first load: match the skin's own aspect
            w, h = renderer.native
            scale = min(1.0, 640 / max(1, w))
            self.geometry(f"{int(w*scale)}x{int(h*scale) + 34}")
        self._invalidate()
        self.on_change(display_skin_dir=folder)

    def choose_colour(self):
        rgb, name = colorchooser.askcolor(self.bg_colour, parent=self,
                                          title="Display background")
        if name:
            self.bg_colour = name
            self.canvas.configure(bg=name)
            self._invalidate()
            self.on_change(display_bg_colour=name)

    def edit_mapping(self):
        if not self.skin:
            messagebox.showinfo("No skin", "Load a skin folder first.", parent=self)
            return
        MappingDialog(self, self.skin, self.button_map, self._save_mapping,
                      default_map_for(self.skin))

    def _save_mapping(self, mapping):
        self.button_map.update(mapping)
        self._last = None
        # Persist only what differs from the defaults, so a later build shipping better
        # defaults is not permanently overridden by a saved copy of the old ones.
        base = default_map_for(self.skin) if self.skin else DEFAULT_BUTTON_MAP
        diff = {k: v for k, v in self.button_map.items() if base.get(k, "") != v}
        self.all_overrides[skin_key(self.skin)] = diff
        self.all_overrides = {k: v for k, v in self.all_overrides.items() if v}
        self.on_change(display_button_map=self.all_overrides)

    def _pick_delay(self):
        self.delayed = bool(self.delay_var.get())
        self._last = None
        self.on_change(display_after_delay=self.delayed)

    def _pick_stick(self):
        self.stick_mode = self.stick_var.get()
        self._last = None
        self.on_change(display_stick_mode=self.stick_mode)

    def _on_resize(self):
        self._place_hint()
        self._invalidate()

    def _place_hint(self):
        """Centre the placeholder, wrapped to the window, and hide it once loaded."""
        show = self.skin is None
        for item in (self.hint_bg, self.hint, self.hint_link):
            self.canvas.itemconfigure(item, state="normal" if show else "hidden")
        if not show:
            return
        w = max(1, self.canvas.winfo_width())
        h = max(1, self.canvas.winfo_height())
        wrap = max(120, w - 60)
        self.canvas.itemconfigure(self.hint, width=wrap)
        self.canvas.itemconfigure(self.hint_link, width=wrap)
        self.canvas.coords(self.hint, w / 2, h / 2 - 14)
        tb = self.canvas.bbox(self.hint)
        link_y = (tb[3] + 12) if tb else (h / 2 + 20)
        self.canvas.coords(self.hint_link, w / 2, link_y)
        box = self.canvas.bbox(self.hint) or (0, 0, 0, 0)
        lbox = self.canvas.bbox(self.hint_link) or box
        pad = 16
        self.canvas.coords(self.hint_bg,
                           min(box[0], lbox[0]) - pad, box[1] - pad,
                           max(box[2], lbox[2]) + pad, lbox[3] + pad)
        self.canvas.tag_raise(self.hint)
        self.canvas.tag_raise(self.hint_link)

    def _invalidate(self):
        self._last = None
        if self.renderer:
            self.renderer._base = None      # colour or size changed

    # ---- per-tick ------------------------------------------------------
    def update_from(self, telem):
        if not self.renderer or not telem:
            return
        w = max(1, self.canvas.winfo_width())
        h = max(1, self.canvas.winfo_height())
        state = build_state(self.skin, telem, self.button_map, self.stick_mode,
                            self.delayed)
        key = (w, h, tuple(sorted((k, round(v, 3) if isinstance(v, float) else v)
                                  for k, v in state.items())))
        if key == self._last:
            return                          # nothing moved; skip the repaint
        self._last = key
        nw, nh = self.renderer.native
        scale = min(w / nw, h / nh)         # letterbox rather than distort
        img = self.renderer.render(state, (nw * scale, nh * scale), self.bg_colour)
        self._photo = ImageTk.PhotoImage(img)
        self.canvas.coords(self.item, (w - img.width) // 2, (h - img.height) // 2)
        self.canvas.itemconfigure(self.item, image=self._photo)
