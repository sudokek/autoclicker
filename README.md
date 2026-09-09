# Auto Clicker

A small Windows auto clicker with a Tk interface, a customizable global
hotkey, and adjustable speed. The whole program is one file, `autoclicker.py`,
and it uses nothing outside the Python standard library.

## Requirements

- Windows
- Python 3 with `tkinter` (included in the standard python.org installer)

Nothing to install — no `pynput`, no `pyautogui`, no `pip install` step.

## Running it

Double-click `autoclicker.py`, or:

```
python autoclicker.py
```

Double-clicking normally leaves a black console window sitting behind the UI.
This script hides it, so you get one window. Launched from a terminal instead,
it leaves your terminal alone and stays attached to it.

Only one copy runs at a time. Launching it again brings the window you already
have to the front instead of opening a second one.

## Controls

| Setting | What it does |
| --- | --- |
| **Clicks/sec** | Typed box accepts `0.1` to `10000`. The slider covers `1` to `1000` and is log-scaled, so the low end stays precise. The label shows the resulting interval in milliseconds. |
| **Button** | Left, Right, or Middle. |
| **Type** | Single or Double click. |
| **Stop after N clicks** | Optional limit. Unticked, it runs until you stop it. |
| **Stop when I really click** | When on, a physical left click stops the clicker. On by default. |
| **Toggle hotkey** | Starts and stops from any application. Defaults to `F6`. |

Changing the speed while it is running takes effect immediately.

### Changing the hotkey

Press **Change...**, then press the key you want. `Esc` cancels.

Almost any key works, including F-keys and the extra mouse buttons (X1/X2).
Two restrictions: bare modifiers (Shift, Ctrl, Alt, Win) are rejected because
reserving them would make the machine unpleasant to use, and you cannot bind
the same mouse button the clicker is sending, since that would make it
retrigger itself.

### Stopping it

Any of:

- the hotkey (`F6` by default), which works from any window
- a real left click, if "Stop when I really click" is on
- the **Stop** button
- reaching the click limit, if one is set

The hotkey is the most reliable at very high speeds — see the note below.

## Settings

Saved to `autoclicker_settings.json` next to the script when you close it, and
reloaded next time. Delete that file to go back to defaults (10 clicks/sec,
left button, single click, `F6`, no limit, stop-on-real-click on).

## How it tells your clicks from its own

Every click the program injects is stamped with a private value in the event's
`dwExtraInfo` field, and Windows separately marks all synthetic input with
`LLMHF_INJECTED`. A low-level mouse hook watches for a left button press that
carries *neither* mark, which means a human pressed the physical button.

Because it checks both signals, another automation tool's clicks will not stop
it either — only genuine physical input does.

## Speed, honestly

Measured on the machine it was built on, clicking into a real window:

| | Flat out | Asking for 200/sec |
| --- | --- | --- |
| "Stop when I really click" **on** | ~2,600 clicks/sec | exactly 200 |
| "Stop when I really click" **off** | ~8,800 clicks/sec | exactly 200 |

At ordinary rates the feature is free. At full tilt it costs roughly 3x, because
the mouse hook and the clicking loop contend for Python's global interpreter
lock. Turn the checkbox off if you want the ceiling.

Two things worth knowing:

- **Very high rates are mostly theoretical.** Applications and games coalesce
  mouse input, so a few hundred clicks per second is usually the point past
  which nothing further registers.
- **At full tilt, the bail-out click is best-effort.** When input is flooding
  in, Windows drops a large share of events before the hook sees them — in
  testing only about a third arrived. The hotkey is unaffected, so use `F6` as
  your reliable stop when running at extreme speeds.

## Notes and limits

- Clicks land wherever the cursor happens to be; there is no fixed-position or
  click-pattern mode.
- The click rate is paced against an absolute deadline rather than by sleeping
  between clicks, so it does not drift over long runs.
- The hotkey is a single key, not a combination.
- Windows only. It calls `SendInput`, `GetAsyncKeyState`, and
  `SetWindowsHookEx` directly, and exits early on other platforms.
