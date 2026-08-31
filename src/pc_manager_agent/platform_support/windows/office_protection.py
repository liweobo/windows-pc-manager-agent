"""Office-specific current-user DPAPI namespace; no credential retrieval or machine scope."""

from typing import Any, cast

import win32crypt


class WindowsOfficeDataProtector:
    """Protect only Agent-owned document recovery bytes for the current user."""

    def protect(self, plaintext: bytes) -> bytes:
        """Encrypt a bounded backup using an Office-specific application purpose."""
        protect = cast(Any, win32crypt.CryptProtectData)
        return bytes(
            protect(
                plaintext,
                "PC Manager Office recovery",
                b"PCManager.Office.Backup.v1",
                None,
                None,
                1,
            )
        )

    def unprotect(self, ciphertext: bytes) -> bytes:
        """Decrypt only this application's own backup envelope."""
        unprotect = cast(Any, win32crypt.CryptUnprotectData)
        _description, plaintext = unprotect(
            ciphertext, b"PCManager.Office.Backup.v1", None, None, 1
        )
        return bytes(plaintext)
