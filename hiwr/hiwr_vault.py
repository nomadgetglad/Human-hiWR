"""Encryption at rest for the few things that must stay secret even if someone copies human.db:
authenticator secrets and employee ID details.

The key lives in its own file next to the database (human.key), never in the repo (*.key is git-ignored).
Keep a copy of that file somewhere safe (a USB stick in the office safe). Without it, ID records cannot be
read and everyone has to set up their authenticator again. On the Windows desktop, turn on BitLocker too.

Set HUMAN_KEY to use a key from the environment instead of the file.
Needs the `cryptography` package (Fernet: AES-128-CBC + HMAC-SHA256).
"""
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class Vault:
    def __init__(self, key: bytes):
        self._f = Fernet(key)

    @classmethod
    def ephemeral(cls):
        """A key that only lives in memory: the sample restaurant and tests."""
        return cls(Fernet.generate_key())

    @classmethod
    def from_file(cls, path):
        env = os.environ.get("HUMAN_KEY")
        if env:
            return cls(env.encode())
        p = Path(path)
        if not p.exists():
            key = Fernet.generate_key()
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(key)
        return cls(p.read_bytes().strip())

    def seal(self, data) -> str:
        if isinstance(data, str):
            data = data.encode()
        return self._f.encrypt(data).decode()

    def open(self, token: str) -> bytes:
        try:
            return self._f.decrypt(token.encode())
        except InvalidToken:
            raise PermissionError("This record was sealed with a different key (human.key). Restore the original key file.")
