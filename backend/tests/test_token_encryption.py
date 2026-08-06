import pytest
from cryptography.fernet import Fernet

import config
import token_encryption


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())


def test_encrypt_then_decrypt_roundtrips():
    ciphertext = token_encryption.encrypt("my-oauth-token-secret")
    assert token_encryption.decrypt(ciphertext) == "my-oauth-token-secret"


def test_ciphertext_is_not_the_plaintext():
    ciphertext = token_encryption.encrypt("my-oauth-token-secret")
    assert b"my-oauth-token-secret" not in ciphertext


def test_decrypt_with_wrong_key_fails_loudly():
    ciphertext = token_encryption.encrypt("my-oauth-token-secret")
    import config as config_module
    from cryptography.fernet import Fernet as _Fernet, InvalidToken

    config_module.TOKEN_ENCRYPTION_KEY = _Fernet.generate_key().decode()
    with pytest.raises(InvalidToken):
        token_encryption.decrypt(ciphertext)
