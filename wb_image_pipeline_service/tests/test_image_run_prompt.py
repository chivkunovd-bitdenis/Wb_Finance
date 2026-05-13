from __future__ import annotations

import pytest

from app.services.image_run_prompt import (
    PROMPT_TEMPLATE_VERSION,
    bake_prompt_fields,
    build_effective_image_prompt,
    normalize_user_text,
    template_body_fingerprint,
)


def test_normalize_user_text_empty_uses_placeholder() -> None:
    text, empty, trunc = normalize_user_text("   \n")
    assert empty is True
    assert trunc is False
    assert "не задано" in text


def test_normalize_user_text_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WIP_IMAGE_PROMPT_USER_TEXT_MAX_CHARS", "100")
    long = "a" * 500
    text, empty, trunc = normalize_user_text(long)
    assert empty is False
    assert trunc is True
    assert len(text) == 100


def test_build_effective_image_prompt_contains_user_text() -> None:
    p = build_effective_image_prompt({"description_user": "  Белые кроссовки  "})
    assert "Белые кроссовки" in p
    assert "Пожелания продавца" in p


def test_bake_prompt_fields_includes_version_and_hash() -> None:
    out = bake_prompt_fields({"description_user": "x", "title": None})
    assert "wip_effective_image_prompt" in out
    assert out["wip_prompt_template_version"] == PROMPT_TEMPLATE_VERSION
    assert len(out["wip_prompt_template_hash"]) == 16


def test_template_fingerprint_stable() -> None:
    fp = template_body_fingerprint("hello {user_text}")
    assert fp == template_body_fingerprint("hello {user_text}")


def test_custom_template_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WIP_IMAGE_PROMPT_TEMPLATE", "X:{user_text}:Y")
    p = build_effective_image_prompt({"description_user": "z"})
    assert p == "X:z:Y"
