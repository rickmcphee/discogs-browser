import hashlib
import secrets

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHash

_ph = PasswordHasher()

ISSUER = "Discogs Browser"


def hash_password(password):
    return _ph.hash(password)


def verify_password(stored_hash, password):
    try:
        return _ph.verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHash):
        return False


def generate_totp_secret():
    return pyotp.random_base32()


def totp_provisioning_uri(secret, account="owner"):
    return pyotp.TOTP(secret).provisioning_uri(name=account, issuer_name=ISSUER)


def verify_totp(secret, code):
    if not code:
        return False
    return pyotp.TOTP(secret).verify(code.strip(), valid_window=1)


def generate_recovery_codes(n=10):
    return [secrets.token_hex(10) for _ in range(n)]


def new_session_token():
    return secrets.token_urlsafe(32)


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()
