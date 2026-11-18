"""The documented .env.example must load cleanly: every key is a real setting and no value is
accidentally a comment (an empty value followed by an inline comment is parsed as that comment)."""
from pathlib import Path

from dotenv import dotenv_values

from app.core.config import Settings

EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"


def test_every_key_is_a_known_setting_and_no_value_is_a_comment():
    values = dotenv_values(EXAMPLE)
    assert len(values) > 25
    fields = {name.upper() for name in Settings.model_fields}
    for key, value in values.items():
        assert key in fields, f"{key} is not a Settings field (typo?)"
        assert "#" not in (value or ""), f"{key} swallowed a comment: {value!r}"


def test_example_loads_with_safe_defaults():
    s = Settings(_env_file=EXAMPLE)
    assert s.metrics_token == ""            # /metrics must not require a token by accident
    assert s.qdrant_api_key == ""
    assert s.trace_content is False         # user text stays out of traces by default
    assert s.db_auto_create is True or s.db_auto_create is False  # parses as bool
