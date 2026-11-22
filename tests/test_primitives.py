"""Tests for core primitives: ULID, secret redaction, and bilingual token/FTS utilities."""

from fos.redact import redact_secrets
from fos.tokens import estimate_tokens, tokenize_for_fts, truncate_to_tokens
from fos.ulid import new_atom_id, new_ulid


def test_ulid_monotonic_and_format() -> None:
    ids = [new_ulid() for _ in range(50)]
    assert all(len(u) == 26 for u in ids)
    assert len(set(ids)) == 50
    assert ids == sorted(ids)
    assert new_atom_id().startswith("atm_")


def test_secret_redaction_covers_all_common_credentials() -> None:
    sample = (
        "OPENAI=sk-proj-abcdefghijklmnop1234567890 "
        "ANTH=sk-ant-api03-1234567890abcdefghij "
        "GH=ghp_1234567890abcdefghijklmnopqrstuvwxyz "
        "AWS=AKIAIOSFODNN7EXAMPLE "
        "Authorization: Bearer supersecretbearertoken123456 "
        "db=postgres://admin:myS3cretPass@db.internal:5432/app "
        "password=verysecretpassword99 "
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----"
    )
    res = redact_secrets(sample)
    assert res.had_secrets
    for secret_frag in (
        "sk-proj-",
        "sk-ant-",
        "ghp_123456",
        "AKIAIOSFODNN7EXAMPLE",
        "supersecretbearertoken123456",
        "myS3cretPass",
        "verysecretpassword99",
        "MIIEowIBAAKCAQEA",
    ):
        assert secret_frag not in res.text
    assert "[REDACTED:" in res.text


def test_tokens_and_bilingual_fts() -> None:
    text = "macOS openrsync 不支持 --mkpath，必须先 ssh mkdir -p"
    est = estimate_tokens(text)
    assert 10 <= est <= 40
    fts = tokenize_for_fts(text)
    assert "openrsync" in fts
    assert "mkpath" in fts
    assert "不支" in fts and "支持" in fts
    short = truncate_to_tokens(text * 5, 20)
    assert estimate_tokens(short) <= 20
