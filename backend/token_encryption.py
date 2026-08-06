from cryptography.fernet import Fernet

import config


def encrypt(plaintext: str) -> bytes:
    return Fernet(config.TOKEN_ENCRYPTION_KEY.encode()).encrypt(plaintext.encode())


def decrypt(ciphertext: bytes) -> str:
    return Fernet(config.TOKEN_ENCRYPTION_KEY.encode()).decrypt(ciphertext).decode()
