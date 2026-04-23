from cryptography.fernet import Fernet

from config.settings import OAUTH_ENCRYPTION_KEY

_fernet = Fernet(OAUTH_ENCRYPTION_KEY.encode())


def encrypt_token(plaintext: str) -> bytes:
    return _fernet.encrypt(plaintext.encode())


def decrypt_token(ciphertext: bytes) -> str:
    return _fernet.decrypt(ciphertext).decode()
