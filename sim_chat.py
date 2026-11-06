"""Bounded text delivery to iRacing's own window; never global keyboard input."""
from __future__ import annotations

import ctypes
import math
import os
import re
import time
from ctypes import wintypes


class SimChat:
    def __init__(self, ir):
        self.ir = ir

    @staticmethod
    def private_text(car_number, engineer, summary):
        if not isinstance(car_number, str) or not re.fullmatch(r'\d{1,4}', car_number):
            raise ValueError('The driver car number is unavailable for private confirmation.')
        # The recipient always comes from the SDK, never from a remote request.
        name = re.sub(r'[\x00-\x1f\x7f$]', '', str(engineer)).strip()[:60]
        details = re.sub(r'[\x00-\x1f\x7f$]', '', str(summary)).strip()
        return f'/{car_number} (Stint) Pit selections applied by {name}: {details}'[:240]

    def private_confirmation(self, car_number, engineer, summary):
        return self._write(self.private_text(car_number, engineer, summary))

    def auto_fuel(self, enabled, margin):
        if type(enabled) is not bool or (margin is not None and (type(margin) not in (int, float) or not math.isfinite(margin) or not -10 <= margin <= 10)):
            raise ValueError('Invalid auto fuel selection.')
        text = ('#autofuel' + (f' {margin:.1f}' if margin is not None else '')) if enabled else '#-autofuel'
        return self._write(text)

    def zero_fuel_amount(self, current):
        if type(current) not in (int, float) or not math.isfinite(current) or not 0 < current <= 1000:
            raise ValueError('Invalid current fuel selection.')
        # SDK fuel=0 means reuse the saved amount. The documented relative
        # macro can actually reduce it to zero without changing other options.
        return self._write(f'#fuel -{current:.9g}l')

    def _write(self, text):
        if os.name != 'nt' or not self.ir or not self.ir.is_connected:
            return False
        import irsdk
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user32.FindWindowW.restype = wintypes.HWND
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.PostMessageW.restype = wintypes.BOOL
        hwnd = user32.FindWindowW('SimWinClass', 'iRacing.com Simulator')
        if not hwnd:
            return False
        # SDK chat commands address the simulator; WM_CHAR goes only to this
        # verified window. No focus change, clipboard, SendInput or key hooks.
        if not self.ir.chat_command(irsdk.ChatCommandMode.cancel):
            return False
        if not self.ir.chat_command(irsdk.ChatCommandMode.begin_chat):
            return False
        time.sleep(.03)
        encoded = text.encode('utf-16-le')
        for offset in range(0, len(encoded), 2):
            char = int.from_bytes(encoded[offset:offset+2], 'little')
            if not user32.PostMessageW(hwnd, 0x0102, char, 1):
                self.ir.chat_command(irsdk.ChatCommandMode.cancel)
                return False
        # Chat submission handles the Enter key event, rather than WM_CHAR.
        return bool(user32.PostMessageW(hwnd, 0x0100, 13, 0x001c0001) and
                    user32.PostMessageW(hwnd, 0x0101, 13, 0xc01c0001))
