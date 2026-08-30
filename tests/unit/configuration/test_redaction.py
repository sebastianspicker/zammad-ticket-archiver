"""Verify configuration redaction preserves shape without leaking secrets."""

from chronikwerk.configuration.redaction import (
    REDACTED_VALUE,
    redact_settings_dict,
    scrub_secrets_in_text,
)


def test_redaction_preserves_shape_without_disclosing_tokens() -> None:
    secret = "credential-that-must-not-leak"
    value = redact_settings_dict(
        {"api_token": secret, "nested": {"url": f"https://user:{secret}@example"}}
    )

    assert value["api_token"] == REDACTED_VALUE
    assert secret not in repr(value)
    assert secret not in scrub_secrets_in_text(f"Authorization: Bearer {secret}")
