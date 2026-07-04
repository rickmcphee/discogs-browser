import pyotp
import auth_core


def test_password_hash_roundtrip():
    h = auth_core.hash_password("hunter2")
    assert h != "hunter2"
    assert auth_core.verify_password(h, "hunter2") is True
    assert auth_core.verify_password(h, "wrong") is False


def test_verify_password_bad_hash_returns_false():
    assert auth_core.verify_password("not-a-hash", "x") is False


def test_totp_verify_accepts_current_code():
    secret = auth_core.generate_totp_secret()
    code = pyotp.TOTP(secret).now()
    assert auth_core.verify_totp(secret, code) is True


def test_totp_verify_rejects_wrong_code():
    secret = auth_core.generate_totp_secret()
    assert auth_core.verify_totp(secret, "000000") is False


def test_provisioning_uri_contains_issuer():
    secret = auth_core.generate_totp_secret()
    uri = auth_core.totp_provisioning_uri(secret)
    assert uri.startswith("otpauth://totp/")
    assert "Discogs%20Browser" in uri or "Discogs Browser" in uri


def test_recovery_codes_generate_and_hash():
    codes = auth_core.generate_recovery_codes(10)
    assert len(codes) == 10
    assert len(set(codes)) == 10
    hashes = [auth_core.hash_token(c) for c in codes]
    assert auth_core.hash_token(codes[0]) in hashes


def test_session_token_and_hash():
    tok = auth_core.new_session_token()
    assert len(tok) >= 32
    assert auth_core.hash_token(tok) != tok
    assert auth_core.hash_token(tok) == auth_core.hash_token(tok)
