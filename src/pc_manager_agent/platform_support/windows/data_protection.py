"""Current-user Windows DPAPI protection for exact startup backup material."""

from __future__ import annotations

import os
from typing import Any, cast

import win32crypt

_CRYPTPROTECT_UI_FORBIDDEN = 0x1
_ENTROPY = b"WindowsPCManagerAgent.Stage4B.StartupBackup.v1"


class WindowsCurrentUserDataProtector:
    """Encrypt backup bytes for the current Windows user without UI or machine scope."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Windows DPAPI is available only on Windows")

    def protect(self, plaintext: bytes) -> bytes:
        """Return a DPAPI blob tied to the current user profile."""
        protect_data = cast(Any, win32crypt.CryptProtectData)
        return bytes(
            protect_data(
                plaintext,
                "PC Manager startup backup",
                _ENTROPY,
                None,
                None,
                _CRYPTPROTECT_UI_FORBIDDEN,
            )
        )

    def unprotect(self, ciphertext: bytes) -> bytes:
        """Decrypt a valid current-user DPAPI blob or propagate the Windows failure."""
        unprotect_data = cast(Any, win32crypt.CryptUnprotectData)
        _description, plaintext = unprotect_data(
            ciphertext,
            _ENTROPY,
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
        )
        return bytes(plaintext)
