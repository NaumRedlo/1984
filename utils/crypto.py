from cryptography.fernet import Fernet

from config.settings import OAUTH_ENCRYPTION_KEY

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        if not OAUTH_ENCRYPTION_KEY:
            raise RuntimeError(
                "OAUTH_ENCRYPTION_KEY is not set. "
                "Generate one via: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        _fernet = Fernet(OAUTH_ENCRYPTION_KEY.encode())
    return _fernet


def encrypt_token(plaintext: str) -> bytes:
    return _get_fernet().encrypt(plaintext.encode())


def decrypt_token(ciphertext: bytes) -> str:
    return _get_fernet().decrypt(ciphertext).decode()
