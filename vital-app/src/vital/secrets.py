"""Application-layer encryption for stored OAuth refresh tokens.

WHY, GIVEN CLOUD SQL IS ALREADY ENCRYPTED AT REST
-------------------------------------------------
At-rest encryption protects the disk. It does nothing about a leaked
backup, an over-broad read grant, a SQL injection, or a support engineer
running a SELECT. A refresh token is not a password hash — it is a live
bearer credential that unlocks months of somebody's sleep, heart rate and
location history from a third party, and it stays valid until revoked.

The CASA assessment for Google's restricted scopes checks exactly this
class of control (OWASP ASVS "protect sensitive data at rest"), so this is
also groundwork rather than ceremony.

KEY MANAGEMENT
--------------
The key lives in Secret Manager and is read once per process. It is never
written to the database, so a database compromise alone yields nothing.
Rotating it invalidates every stored token, which forces users to
reconnect — annoying but safe, and the disconnect path already handles
exactly that state.

Generate one with:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    gcloud secrets create TOKEN_ENCRYPTION_KEY --data-file=- --project vital-agent-dev
"""
from functools import lru_cache

from vital.config import settings


class EncryptionUnavailable(RuntimeError):
    """Raised rather than falling back to plaintext.

    A silent downgrade would write live third-party credentials to the
    database in the clear, and nothing downstream would look any
    different — the exact shape of failure this codebase keeps getting
    bitten by. Refusing to store a token is recoverable; storing it
    unprotected is not.
    """


@lru_cache
def _cipher():
    from cryptography.fernet import Fernet

    key = settings().token_encryption_key
    if not key:
        raise EncryptionUnavailable(
            "TOKEN_ENCRYPTION_KEY is not set — refusing to store OAuth "
            "refresh tokens. Generate one with Fernet.generate_key().")
    # Strip whitespace. `... | gcloud secrets create --data-file=-` stores
    # the trailing newline, and Fernet rejects the key with an error that
    # says nothing about newlines — it looks like a corrupt key, and the
    # obvious response is to generate another one that fails the same way.
    if isinstance(key, str):
        key = key.strip()
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as exc:
        raise EncryptionUnavailable(f"TOKEN_ENCRYPTION_KEY is not a valid "
                                    f"Fernet key: {type(exc).__name__}") from exc


def encrypt(plaintext: str) -> str:
    if not plaintext:
        raise ValueError("refusing to encrypt an empty token")
    return _cipher().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Returns the token, or raises.

    Callers treat a failure here as "the connection is broken, ask the user
    to reconnect" — which is correct for both a rotated key and a corrupted
    row, and avoids guessing which one it was.
    """
    from cryptography.fernet import InvalidToken

    try:
        return _cipher().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise EncryptionUnavailable(
            "stored token could not be decrypted — the encryption key has "
            "probably rotated; the user must reconnect") from exc


def available() -> bool:
    """Whether encryption is usable, for startup checks and the UI.

    Lets the connect route refuse up front with a clear message instead of
    sending the user through a full OAuth consent flow that cannot possibly
    persist its result.
    """
    try:
        _cipher()
        return True
    except EncryptionUnavailable:
        return False
