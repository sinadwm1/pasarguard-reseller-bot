from __future__ import annotations


class SecretBox:
    def __init__(self, key: str) -> None:
        from cryptography.fernet import Fernet

        self._fernet = Fernet(key.encode())

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, token: str) -> str:
        return self._fernet.decrypt(token.encode()).decode()
