import session_tokens


def test_session_token_and_hash():
    tok = session_tokens.new_session_token()
    assert len(tok) >= 32
    assert session_tokens.hash_token(tok) != tok
    assert session_tokens.hash_token(tok) == session_tokens.hash_token(tok)
