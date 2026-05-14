from __future__ import annotations

from app.services.image_generation_prompts import build_main_image_prompt


def test_main_image_prompt_allows_styling_but_preserves_product() -> None:
    prompt = build_main_image_prompt("Old money образ на веранде")

    assert "Строго сохранить товар" in prompt
    assert "нижнюю одежду, обувь, аксессуары и общий styling" in prompt
    assert "Не делай белый/нейтральный фон по умолчанию" in prompt
