# Implements a virtual X11 mouse and keyboard
# TODO: Rename to reflect added mouse capabilities.
# FIXME: Window position and dimensions need to actually
# come from the window.
# TODO: Move failsafe and keysym/keycode functions to a separate class.
#       This will allow this file to be py-xlib free.

import _thread
import re
import subprocess
import threading
import time
from enum import Enum
from typing import Union

import numpy as np
import Xlib.display
import Xlib.protocol
import Xlib.X
import Xlib.XK
import Xlib.xobject
from Xlib.ext import xinput

from src.keyboard import lib_mpx_input


def keysym_for_key_name(key_name):
    keysym = Xlib.XK.string_to_keysym(key_name)
    assert keysym is not None
    return keysym


class KeyboardEventMode(Enum):
    SEND_EVENT = 1
    FAKE_INPUT = 2


# Numbers match X11 button numbers.
class MouseButton(Enum):
    LEFT = 1
    MIDDLE = 2
    RIGHT = 3


def _setup_mpx_devices(display, window, lib_mpx_input):
    """Sets up new XI2 pointer and keyboards for this logical keyboard."""
    lib_mpx_input.make_cursor(display, window.id, f"devices_for_{window}".encode())
    lib_mpx_input.xflush(display)


class Keyboard(object):
    PRESS = 1
    RELEASE = -1
    SHIFT_MOD = 1
    CTRL_MOD = 4
    ALT_MOD = 8

    global_mutex = threading.Lock()

    def __init__(
        self,
        display: Xlib.display.Display,
        window: Xlib.xobject.drawable.Window,
        window_x: int = 0,
        window_y: int = 0,
        keyboard_config: dict = {},
    ):
        self.last_keymap = np.zeros(84)
        self.window = window
        print("Keyboard win:", window)
        # TODO: Get these values from Xlib window properties.
        self.window_x = window_x
        self.window_y = window_y
        self.window_w = 640
        self.window_h = 360
        self.py_xlib_display = display
        self.lib_mpx_input, self.lib_mpx_input_ffi = lib_mpx_input.make_lib_mpx_input()
        self.display = self.lib_mpx_input.open_display("".encode())
        _setup_mpx_devices(self.display, self.window, self.lib_mpx_input)

        self.sequence_keydown_time = keyboard_config.get("sequence_keydown_time", 0.25)

        self.held_keys: set[str] = set()
        self.held_mouse_buttons: set[MouseButton] = set()

        run_failsafe = threading.Thread(target=self._run_failsafe, args=(), kwargs={})
        run_failsafe.start()

    # Allows the user to press ctrl-shift-9 to kill the program.
    def _run_failsafe(self) -> None:
        # Get all non-xtest keyboard device ids from the CLI, since python-xlib doesn't
        # implement list devices.
        list_result = subprocess.run(
            "xinput list | awk '/keyboard/ && /slave/ && !/XTEST/'",
            shell=True,
            capture_output=True,
            text=True,
        )
        keyboard_ids = []
        for line in list_result.stdout.split("\n"):
            match = re.match(r".*id=(\d*).*", line)
            if match:
                keyboard_ids.append(int(match.group(1)))
        print(keyboard_ids)

        event_masks = [
            (keyboard_id, xinput.KeyPressMask | xinput.KeyReleaseMask)
            for keyboard_id in keyboard_ids
        ]
        root = self.py_xlib_display.screen().root
        root.xinput_select_events(event_masks)

        while True:
            event = self.py_xlib_display.next_event()
            if event.type == self.py_xlib_display.extension_event.GenericEvent:
                if (
                    event.data["detail"]
                    == self.py_xlib_display.keysym_to_keycode(keysym_for_key_name("9"))
                    and event.data["mods"]["effective_mods"] & Xlib.X.ShiftMask
                    and event.data["mods"]["effective_mods"] & Xlib.X.ControlMask
                ):
                    print("Exiting due to failsafe keypress")
                    _thread.interrupt_main()
                    return

    def _mask_keymap(keymap):
        keymap[0] = 0  # reserved
        keymap[58] = 0  # caps lock
        keymap[69] = 0  # num lock
        keymap[70] = 0  # scroll lock
        return keymap

    def _assert_keymap_is_masked(keymap):
        assert keymap[0] == 0
        assert keymap[58] == 0
        assert keymap[69] == 0
        assert keymap[70] == 0

    def set_keymap(self, keymap):
        keymap = Keyboard._mask_keymap(keymap)
        # keymap is an 84 element long 0-1 array corresponding to linux keycodes 0-83
        # Sets a scancode bitmap encoding
        toggled_keys = keymap - self.last_keymap
        modifier = Keyboard.modifier_state(keymap)
        for i in range(84):
            if toggled_keys[i] == 1:
                self.press_key(i, modifier)
            elif toggled_keys[i] == -1:
                self.release_key(i, modifier)
        self.last_keymap = keymap

    # Takes a set of key names to be held down and performs the key presses and releases
    # that get the keyboard to that state.
    # Key set is provdided in the form of a set of x key names with the XK_ prefix
    # removed.
    # Note: This function is oblivious to key presses and releases that occur from
    # any other method of this class and so calling this and other methods may be
    # error prone.
    def set_held_keys(self, key_set):
        # Implements re-press key mode.
        for key in self.held_keys:
            self.release_key(key)
        time.sleep(0.01)
        for key in key_set:
            self.press_key(key)
        self.held_keys = key_set

        # Implements delta key mode.
        # pressed = set()
        # released = set()

        # for key in key_set:
        #     if key not in self.held_keys:
        #         pressed.add(key)
        # for key in self.held_keys:
        #     if key not in key_set:
        #         released.add(key)
        # for key in pressed:
        #     self.press_key(key)
        # for key in released:
        #     self.release_key(key)
        # self.held_keys = key_set

    def modifier_state(keymap):
        # LShift keycode: 50 state: 1
        # RShift keycode: 62 state: 1
        # LCtrl  keycode: 37 state: 4
        # RCtrl  keycode:105 state: 4 # Not in our keymap
        # LAlt   keycode: 64 state: 8
        # RAlt   keycode:108 state: 8 # Not in our keymap
        return (
            (keymap[50] or keymap[62]) * Keyboard.SHIFT_MOD
            + (keymap[37]) * Keyboard.CTRL_MOD
            + (keymap[64]) * Keyboard.ALT_MOD
        )

    def press_key(self, key_name, modifier=0):
        self._press_key(key_name, modifier)

    def _press_key(self, key_name, modifier=0):
        self._change_key(key_name, Keyboard.PRESS, modifier)

    def release_key(self, key_name, modifier=0):
        self._release_key(key_name, modifier)

    def _release_key(self, key_name, modifier=0):
        self._change_key(key_name, Keyboard.RELEASE, modifier)

    def key_sequence(self, keys: set[str]):
        for key in keys:
            time.sleep(0.1)
            self.press_key(key)
            time.sleep(self.sequence_keydown_time)
            self.release_key(key)
            time.sleep(0.25)

    def key_name_to_keycode(self, display, key_name):
        keysym = keysym_for_key_name(key_name)
        keycode = display.keysym_to_keycode(keysym)
        return keycode

    def _change_key(self, key_name: str, direction: int, modifier=0):
        keycode = self.key_name_to_keycode(self.py_xlib_display, key_name)
        self.lib_mpx_input.key_event(self.display, keycode, direction)
        self.lib_mpx_input.xflush(self.display)

    def move_mouse(self, x: Union[int, float], y: Union[int, float]) -> None:
        x = min(max(int(x), 1), self.window_w - 1)
        y = min(max(int(y), 1), self.window_h - 1)
        x = x + self.window_x
        y = y + self.window_y
        self.lib_mpx_input.move_mouse(self.display, x, y)
        self.lib_mpx_input.xflush(self.display)

    def set_mouse_button(self, button: MouseButton, direction: int) -> None:
        self.lib_mpx_input.button_event(self.display, button.value, direction)
        self.lib_mpx_input.xflush(self.display)

    def set_held_mouse_buttons(self, mouse_buttons: set[MouseButton]):
        self.assert_fake_input_mode()
        # Implements re-press mouse mode.
        for button in self.held_mouse_buttons:
            self.set_mouse_button(button, Keyboard.RELEASE)
        time.sleep(0.01)
        for button in mouse_buttons:
            self.set_mouse_button(button, Keyboard.PRESS)
        self.held_mouse_buttons = mouse_buttons
