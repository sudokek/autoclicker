"""
Auto Clicker - a small Windows utility.

Toggle clicking on/off with a customizable global hotkey, adjust the click
rate, mouse button, click type, and an optional click limit.

Standard library only: tkinter for the UI, ctypes for SendInput /
GetAsyncKeyState. Self-contained in this one file - just double-click it, or
run it with:  python autoclicker.py
"""

import ctypes
import json
import math
import os
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from tkinter import ttk, messagebox

if sys.platform != "win32":
    raise SystemExit("This auto clicker uses the Windows API and only runs on Windows.")

user32 = ctypes.WinDLL("user32", use_last_error=True)
winmm = ctypes.WinDLL("winmm")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

kernel32.GetConsoleWindow.argtypes = ()
kernel32.GetConsoleWindow.restype = wintypes.HWND
kernel32.GetConsoleProcessList.argtypes = (ctypes.POINTER(wintypes.DWORD), wintypes.DWORD)
kernel32.GetConsoleProcessList.restype = wintypes.DWORD
kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = (
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
)
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
kernel32.CreateMutexW.restype = wintypes.HANDLE
user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
user32.ShowWindow.restype = wintypes.BOOL
user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
user32.FindWindowW.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.IsIconic.argtypes = (wintypes.HWND,)
user32.IsIconic.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = (
    wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

WINDOW_TITLE = "Auto Clicker"

# Fastest rate the UI will accept. The slider stops at SLIDER_MAX_CPS; the
# typed box goes all the way to MAX_CPS for anyone who wants to push it.
MAX_CPS = 10000.0
SLIDER_MAX_CPS = 1000.0

SETTINGS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "autoclicker_settings.json"
)

# --------------------------------------------------------------------------
# Win32 input plumbing
# --------------------------------------------------------------------------

INPUT_MOUSE = 0
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _INPUTunion(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTunion)]


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT
user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
user32.GetAsyncKeyState.restype = ctypes.c_short

# button name -> (down flag, up flag)
BUTTON_FLAGS = {
    "Left": (0x0002, 0x0004),
    "Right": (0x0008, 0x0010),
    "Middle": (0x0020, 0x0040),
}

MOUSE_VK_TO_BUTTON = {0x01: "Left", 0x02: "Right", 0x04: "Middle"}

# Low-level mouse hook, used to notice a physical click by the user.
WH_MOUSE_LL = 14
WM_LBUTTONDOWN = 0x0201
WM_QUIT = 0x0012
LLMHF_INJECTED = 0x00000001
LLMHF_LOWER_IL_INJECTED = 0x00000002


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


INJECTED_MASK = LLMHF_INJECTED | LLMHF_LOWER_IL_INJECTED

HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)

user32.SetWindowsHookExW.argtypes = (
    ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = (
    wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.CallNextHookEx.restype = ctypes.c_long
user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.GetMessageW.argtypes = (
    ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
user32.GetMessageW.restype = ctypes.c_int
user32.PostThreadMessageW.argtypes = (
    wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.PostThreadMessageW.restype = wintypes.BOOL
kernel32.GetCurrentThreadId.restype = wintypes.DWORD


# Stamped into dwExtraInfo on every click we inject, so the mouse hook can tell
# our own clicks apart from the ones the user physically makes.
INJECT_SIGNATURE = 0x0AC1C0DE


def send_click(button):
    """Send one full press+release of `button` at the current cursor position."""
    down, up = BUTTON_FLAGS[button]
    events = (INPUT * 2)()
    events[0].type = INPUT_MOUSE
    events[0].mi = MOUSEINPUT(0, 0, 0, down, 0, INJECT_SIGNATURE)
    events[1].type = INPUT_MOUSE
    events[1].mi = MOUSEINPUT(0, 0, 0, up, 0, INJECT_SIGNATURE)
    user32.SendInput(2, events, ctypes.sizeof(INPUT))


# --------------------------------------------------------------------------
# Virtual-key names, for the hotkey picker
# --------------------------------------------------------------------------

VK_NAMES = {
    0x01: "Mouse Left",
    0x02: "Mouse Right",
    0x04: "Mouse Middle",
    0x05: "Mouse X1",
    0x06: "Mouse X2",
    0x08: "Backspace",
    0x09: "Tab",
    0x0D: "Enter",
    0x13: "Pause",
    0x14: "CapsLock",
    0x1B: "Esc",
    0x20: "Space",
    0x21: "PageUp",
    0x22: "PageDown",
    0x23: "End",
    0x24: "Home",
    0x25: "Left",
    0x26: "Up",
    0x27: "Right",
    0x28: "Down",
    0x2C: "PrintScreen",
    0x2D: "Insert",
    0x2E: "Delete",
    0x5B: "LWin",
    0x5C: "RWin",
    0x5D: "Menu",
    0x90: "NumLock",
    0x91: "ScrollLock",
    0xA0: "LShift",
    0xA1: "RShift",
    0xA2: "LCtrl",
    0xA3: "RCtrl",
    0xA4: "LAlt",
    0xA5: "RAlt",
    0xBA: "Semicolon",
    0xBB: "Equals",
    0xBC: "Comma",
    0xBD: "Minus",
    0xBE: "Period",
    0xBF: "Slash",
    0xC0: "Backtick",
    0xDB: "LBracket",
    0xDC: "Backslash",
    0xDD: "RBracket",
    0xDE: "Quote",
    0x6A: "Num*",
    0x6B: "Num+",
    0x6D: "Num-",
    0x6E: "Num.",
    0x6F: "Num/",
}
for _i in range(10):
    VK_NAMES[0x30 + _i] = str(_i)
    VK_NAMES[0x60 + _i] = "Num" + str(_i)
for _i in range(26):
    VK_NAMES[0x41 + _i] = chr(ord("A") + _i)
for _i in range(24):
    VK_NAMES[0x70 + _i] = "F" + str(_i + 1)

# Keys we refuse to bind: reserving them would make the machine hard to use.
UNBINDABLE = {0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}


def vk_name(vk):
    return VK_NAMES.get(vk, "VK 0x%02X" % vk)


def key_down(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------

DEFAULTS = {
    "cps": 10.0,
    "button": "Left",
    "click_type": "Single",
    "hotkey_vk": 0x75,  # F6
    "limit_enabled": False,
    "limit": 100,
    "stop_on_real_click": True,
}


class AutoClicker:
    def __init__(self, root):
        self.root = root
        self.settings = self.load_settings()

        self.clicking = threading.Event()
        self.shutdown = threading.Event()
        self.capturing_hotkey = False
        self.click_count = 0
        self.interval = 1.0 / self.settings["cps"]
        self.lock = threading.Lock()
        self.syncing = False  # guards the entry <-> slider round trip

        self.hook_handle = None
        self.hook_thread_id = 0
        self.hook_proc = None  # must outlive the hook; ctypes will not hold it
        self.stopped_by_click = False
        # Plain attribute, not a dict lookup: the hook callback reads it on
        # every mouse event and has a hard deadline.
        self.stop_on_real_click = bool(self.settings["stop_on_real_click"])

        self.build_ui()
        self.apply_settings()

        # 1 ms timer resolution, so sleeps between clicks stay accurate.
        winmm.timeBeginPeriod(1)

        threading.Thread(target=self.click_loop, daemon=True).start()
        threading.Thread(target=self.hotkey_loop, daemon=True).start()
        threading.Thread(target=self.mouse_hook_loop, daemon=True).start()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.refresh_status()

    # -- persistence -------------------------------------------------------

    def load_settings(self):
        data = dict(DEFAULTS)
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as fh:
                saved = json.load(fh)
            for key in DEFAULTS:
                if key in saved:
                    data[key] = saved[key]
        except (OSError, ValueError):
            pass
        return data

    def save_settings(self):
        try:
            with open(SETTINGS_PATH, "w", encoding="utf-8") as fh:
                json.dump(self.settings, fh, indent=2)
        except OSError:
            pass

    # -- UI ----------------------------------------------------------------

    def build_ui(self):
        self.root.title(WINDOW_TITLE)
        self.root.resizable(False, False)

        frame = ttk.Frame(self.root, padding=12)
        frame.grid(sticky="nsew")

        # Speed
        speed = ttk.LabelFrame(frame, text="Speed", padding=10)
        speed.grid(row=0, column=0, sticky="ew")
        speed.columnconfigure(2, weight=1)

        ttk.Label(speed, text="Clicks/sec:").grid(row=0, column=0, sticky="w")
        self.cps_var = tk.StringVar()
        cps_entry = ttk.Entry(speed, textvariable=self.cps_var, width=8)
        cps_entry.grid(row=0, column=1, sticky="w", padx=(6, 0))
        cps_entry.bind("<Return>", lambda _e: self.cps_from_entry())
        cps_entry.bind("<FocusOut>", lambda _e: self.cps_from_entry())

        self.interval_label = ttk.Label(speed, text="", foreground="#555555")
        self.interval_label.grid(row=0, column=2, sticky="e", padx=(10, 0))

        self.cps_scale = ttk.Scale(
            speed, from_=0, to=100, orient="horizontal", command=self.cps_from_scale
        )
        self.cps_scale.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(8, 2))
        ttk.Label(
            speed,
            text="slider 1-%g   |   type up to %g" % (SLIDER_MAX_CPS, MAX_CPS),
            foreground="#777777",
        ).grid(row=2, column=0, columnspan=3, sticky="w")

        # Click options
        opts = ttk.LabelFrame(frame, text="Click", padding=10)
        opts.grid(row=1, column=0, sticky="ew", pady=(10, 0))

        ttk.Label(opts, text="Button:").grid(row=0, column=0, sticky="w")
        self.button_var = tk.StringVar()
        ttk.Combobox(
            opts,
            textvariable=self.button_var,
            state="readonly",
            width=8,
            values=["Left", "Right", "Middle"],
        ).grid(row=0, column=1, padx=(6, 16))

        ttk.Label(opts, text="Type:").grid(row=0, column=2, sticky="w")
        self.type_var = tk.StringVar()
        ttk.Combobox(
            opts,
            textvariable=self.type_var,
            state="readonly",
            width=8,
            values=["Single", "Double"],
        ).grid(row=0, column=3, padx=(6, 0))

        self.limit_var = tk.BooleanVar()
        ttk.Checkbutton(
            opts, text="Stop after", variable=self.limit_var, command=self.sync_limit_state
        ).grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.limit_entry_var = tk.StringVar()
        self.limit_entry = ttk.Entry(opts, textvariable=self.limit_entry_var, width=8)
        self.limit_entry.grid(row=1, column=1, sticky="w", padx=(6, 0), pady=(8, 0))
        ttk.Label(opts, text="clicks").grid(row=1, column=2, sticky="w", pady=(8, 0))

        self.real_click_var = tk.BooleanVar()
        ttk.Checkbutton(
            opts,
            text="Stop when I really click",
            variable=self.real_click_var,
            command=self.sync_real_click,
        ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(8, 0))

        # Hotkey
        hk = ttk.LabelFrame(frame, text="Toggle hotkey", padding=10)
        hk.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        hk.columnconfigure(0, weight=1)

        self.hotkey_label = ttk.Label(hk, text="", font=("Segoe UI", 11, "bold"))
        self.hotkey_label.grid(row=0, column=0, sticky="w")
        self.hotkey_btn = ttk.Button(hk, text="Change...", command=self.begin_capture)
        self.hotkey_btn.grid(row=0, column=1, sticky="e")

        # Status + controls
        status_row = ttk.Frame(frame)
        status_row.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        status_row.columnconfigure(0, weight=1)
        self.status = ttk.Label(
            status_row, text="Stopped", font=("Segoe UI", 12, "bold"), foreground="#aa0000"
        )
        self.status.grid(row=0, column=0, sticky="w")
        self.counter_label = ttk.Label(status_row, text="0 clicks", foreground="#555555")
        self.counter_label.grid(row=0, column=1, sticky="e")

        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        ttk.Button(buttons, text="Start", command=self.start).grid(
            row=0, column=0, sticky="ew", padx=(0, 4)
        )
        ttk.Button(buttons, text="Stop", command=self.stop).grid(
            row=0, column=1, sticky="ew", padx=(4, 0)
        )

    def apply_settings(self):
        self.button_var.set(self.settings["button"])
        self.type_var.set(self.settings["click_type"])
        self.limit_var.set(self.settings["limit_enabled"])
        self.limit_entry_var.set(str(self.settings["limit"]))
        self.real_click_var.set(self.settings["stop_on_real_click"])
        self.sync_real_click()
        self.set_cps(self.settings["cps"])
        self.hotkey_label.config(text=vk_name(self.settings["hotkey_vk"]))
        self.sync_limit_state()

    def sync_limit_state(self):
        self.limit_entry.state(["!disabled"] if self.limit_var.get() else ["disabled"])

    def sync_real_click(self):
        self.stop_on_real_click = bool(self.real_click_var.get())
        self.settings["stop_on_real_click"] = self.stop_on_real_click

    # -- speed handling ----------------------------------------------------

    @staticmethod
    def pos_to_cps(pos):
        """Slider position (0-100) -> clicks/sec, log-scaled so the low end
        stays adjustable when the top end reaches four figures."""
        return 10.0 ** (pos / 100.0 * math.log10(SLIDER_MAX_CPS))

    @staticmethod
    def cps_to_pos(cps):
        cps = max(1.0, min(SLIDER_MAX_CPS, cps))
        return 100.0 * math.log10(cps) / math.log10(SLIDER_MAX_CPS)

    def set_cps(self, cps, from_scale=False):
        cps = max(0.1, min(MAX_CPS, float(cps)))
        self.settings["cps"] = cps
        with self.lock:
            self.interval = 1.0 / cps
        self.cps_var.set("%g" % cps)
        ms = 1000.0 / cps
        self.interval_label.config(
            text="every %s ms" % ("%.2f" % ms if ms < 10 else "%.1f" % ms)
        )
        if not from_scale:
            # Moving the slider fires cps_from_scale; suppress that round trip.
            self.syncing = True
            try:
                self.cps_scale.set(self.cps_to_pos(cps))
            finally:
                self.syncing = False

    def cps_from_entry(self):
        try:
            cps = float(self.cps_var.get())
        except ValueError:
            self.cps_var.set("%g" % self.settings["cps"])
            return
        self.set_cps(cps)

    def cps_from_scale(self, value):
        if self.syncing:
            return
        cps = self.pos_to_cps(float(value))
        cps = round(cps, 1) if cps < 10 else float(round(cps))
        self.set_cps(cps, from_scale=True)

    # -- start / stop ------------------------------------------------------

    def start(self):
        if self.clicking.is_set():
            return
        self.cps_from_entry()
        if self.limit_var.get():
            try:
                limit = int(self.limit_entry_var.get())
                if limit < 1:
                    raise ValueError
            except ValueError:
                messagebox.showerror(
                    "Auto Clicker", "Click limit must be a whole number of 1 or more."
                )
                return
            self.settings["limit"] = limit
        self.settings["limit_enabled"] = self.limit_var.get()
        self.settings["button"] = self.button_var.get()
        self.settings["click_type"] = self.type_var.get()
        self.click_count = 0
        self.stopped_by_click = False
        self.clicking.set()

    def stop(self):
        self.clicking.clear()

    def toggle(self):
        if self.clicking.is_set():
            self.stop()
        else:
            self.start()

    # -- worker threads ----------------------------------------------------

    def click_loop(self):
        while not self.shutdown.is_set():
            if not self.clicking.wait(timeout=0.1):
                continue

            button = self.settings["button"]
            double = self.settings["click_type"] == "Double"
            limit = self.settings["limit"] if self.settings["limit_enabled"] else None
            next_click = time.perf_counter()

            while self.clicking.is_set() and not self.shutdown.is_set():
                send_click(button)
                if double:
                    send_click(button)
                self.click_count += 1

                if limit is not None and self.click_count >= limit:
                    self.clicking.clear()
                    break

                with self.lock:
                    interval = self.interval
                next_click += interval
                now = time.perf_counter()
                if next_click < now:  # fell behind; don't burst to catch up
                    next_click = now + interval
                self.sleep_until(next_click)

    @staticmethod
    def sleep_until(deadline):
        """Sleep until `deadline`, spinning over the last ~1 ms for accuracy."""
        while True:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return
            if remaining > 0.0015:
                time.sleep(remaining - 0.001)

    def mouse_hook_loop(self):
        """Watch for a physical left click and stop clicking when one arrives.

        Runs on its own thread with its own message pump: a low-level hook is
        delivered while the installing thread retrieves messages, and Windows
        silently drops a hook whose callback overruns its deadline - so this
        must not share the Tk thread, and the callback below stays minimal.
        """
        self.hook_thread_id = kernel32.GetCurrentThreadId()

        # Bound locally: every lookup here is on the per-event hot path.
        call_next = user32.CallNextHookEx
        clicking = self.clicking

        def on_mouse(ncode, wparam, lparam):
            # Ordered cheapest-first: most events are moves, and while we are
            # clicking, nearly every button event is one of our own. The work
            # below is small but not free - at full tilt this thread contends
            # with the click loop for the GIL, which is why running with this
            # feature on costs roughly 3x the top-end click rate.
            if (
                wparam == WM_LBUTTONDOWN
                and ncode >= 0
                and self.stop_on_real_click
                and clicking.is_set()
            ):
                info = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                # Injected input is ours or some other tool's - never a person.
                if not info.flags & INJECTED_MASK:
                    if info.dwExtraInfo != INJECT_SIGNATURE:
                        clicking.clear()
                        self.stopped_by_click = True
            return call_next(None, ncode, wparam, lparam)

        self.hook_proc = HOOKPROC(on_mouse)
        self.hook_handle = user32.SetWindowsHookExW(
            WH_MOUSE_LL, self.hook_proc, None, 0
        )
        if not self.hook_handle:
            return  # no hook: the feature is simply unavailable, app still runs

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if self.shutdown.is_set():
                break
        user32.UnhookWindowsHookEx(self.hook_handle)
        self.hook_handle = None

    def hotkey_loop(self):
        """Poll the bound key globally, firing on the press edge."""
        was_down = True  # start True so a key held at launch does not fire
        while not self.shutdown.is_set():
            if self.capturing_hotkey:
                was_down = True
                time.sleep(0.03)
                continue
            down = key_down(self.settings["hotkey_vk"])
            if down and not was_down:
                self.root.after(0, self.toggle)
            was_down = down
            time.sleep(0.02)

    # -- hotkey capture ----------------------------------------------------

    def begin_capture(self):
        if self.capturing_hotkey:
            return
        self.clicking.clear()
        self.capturing_hotkey = True
        self.hotkey_btn.state(["disabled"])
        self.hotkey_label.config(text="Press any key...")
        threading.Thread(target=self.capture_worker, daemon=True).start()

    def capture_worker(self):
        # Wait for everything to be released first, so the mouse click on
        # "Change..." (and any key still held) is not what gets captured.
        deadline = time.perf_counter() + 5.0
        while time.perf_counter() < deadline:
            if not any(key_down(vk) for vk in VK_NAMES):
                break
            time.sleep(0.02)

        captured = None
        while self.capturing_hotkey and not self.shutdown.is_set():
            for vk in VK_NAMES:
                if vk not in UNBINDABLE and key_down(vk):
                    captured = vk
                    break
            if captured is not None:
                break
            time.sleep(0.02)

        self.root.after(0, self.finish_capture, captured)

    def finish_capture(self, vk):
        self.capturing_hotkey = False
        self.hotkey_btn.state(["!disabled"])
        if vk is not None and vk != 0x1B:  # Esc cancels
            if MOUSE_VK_TO_BUTTON.get(vk) == self.button_var.get():
                messagebox.showwarning(
                    "Auto Clicker",
                    "That is the same mouse button the clicker sends, so binding it "
                    "would make the clicker retrigger itself. Pick a different key.",
                )
            else:
                self.settings["hotkey_vk"] = vk
                self.save_settings()
        self.hotkey_label.config(text=vk_name(self.settings["hotkey_vk"]))

    # -- status ------------------------------------------------------------

    def refresh_status(self):
        if self.clicking.is_set():
            self.status.config(text="RUNNING", foreground="#008800")
        elif self.capturing_hotkey:
            self.status.config(text="Waiting for key...", foreground="#aa6600")
        elif self.stopped_by_click:
            self.status.config(text="Stopped (you clicked)", foreground="#aa0000")
        else:
            self.status.config(text="Stopped", foreground="#aa0000")
        self.counter_label.config(text="%d clicks" % self.click_count)
        self.root.after(100, self.refresh_status)

    def on_close(self):
        self.clicking.clear()
        self.shutdown.set()
        if self.hook_thread_id:
            # Wake the hook thread's GetMessage so it can unhook and exit.
            user32.PostThreadMessageW(self.hook_thread_id, WM_QUIT, 0, 0)
        try:
            self.settings["button"] = self.button_var.get()
            self.settings["click_type"] = self.type_var.get()
            self.settings["limit_enabled"] = self.limit_var.get()
            self.settings["stop_on_real_click"] = bool(self.real_click_var.get())
            self.settings["limit"] = int(self.limit_entry_var.get())
        except ValueError:
            pass
        self.save_settings()
        winmm.timeEndPeriod(1)
        self.root.destroy()


# --------------------------------------------------------------------------
# Hiding the console window
# --------------------------------------------------------------------------

SW_HIDE = 0
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PYTHON_EXE_NAMES = {"python.exe", "pythonw.exe", "py.exe", "pyw.exe"}


def process_name(pid):
    """Base name of the executable behind `pid`, or None if we cannot tell."""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        return os.path.basename(buf.value)
    finally:
        kernel32.CloseHandle(handle)


def console_is_ours():
    """True when this console exists only because Explorer launched us - that
    is, every process attached to it is a Python interpreter.

    Counting processes is not enough: double-clicking a .py attaches two of
    them (the Python launcher shim plus the real interpreter). A shell always
    leaves its own cmd.exe / powershell.exe / bash.exe attached, and that is
    the window we must never touch, so anything we cannot positively identify
    as Python counts as "not ours".
    """
    if not kernel32.GetConsoleWindow():
        return False  # no console at all (pythonw, or a terminal like mintty)
    buf = (wintypes.DWORD * 16)()
    count = kernel32.GetConsoleProcessList(buf, 16)
    if count == 0 or count > 16:
        return False
    for i in range(count):
        name = process_name(buf[i])
        if name is None or name.lower() not in PYTHON_EXE_NAMES:
            return False
    return True


def hide_console():
    """Hide the console Explorer opened for us, leaving only the GUI window."""
    hwnd = kernel32.GetConsoleWindow()
    if hwnd:
        user32.ShowWindow(hwnd, SW_HIDE)


# --------------------------------------------------------------------------
# One instance at a time
# --------------------------------------------------------------------------

MUTEX_NAME = "Local\\AutoClicker-single-instance-8f3c1d2e"
ERROR_ALREADY_EXISTS = 183
SW_RESTORE = 9

# Module level so the handle lives as long as the process: Windows releases
# the mutex when the last handle to it closes.
instance_mutex = None


def focus_existing_window():
    """Bring the instance that is already running to the front."""
    hwnd = user32.FindWindowW(None, WINDOW_TITLE)
    if not hwnd:
        return
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    name = process_name(pid.value)
    if name is None or name.lower() not in PYTHON_EXE_NAMES:
        return  # somebody else's window that happens to share our title
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)


def claim_single_instance():
    """True if we are the only instance. Otherwise raise the window that is
    already open and return False so this copy can bow out.

    A named mutex is the authority here: it is owned by the kernel, so it
    disappears the moment the first instance exits, however it exits. Finding
    the window is only used to focus it, never to decide.
    """
    global instance_mutex
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if handle and ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        focus_existing_window()
        return False
    instance_mutex = handle
    return True


def main():
    if console_is_ours():
        hide_console()
    if not claim_single_instance():
        return  # already running - that window now has focus
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    AutoClicker(root)
    root.mainloop()


if __name__ == "__main__":
    main()
