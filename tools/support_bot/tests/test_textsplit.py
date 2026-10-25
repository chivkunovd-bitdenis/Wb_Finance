from wbf_support_bot.textsplit import TELEGRAM_LIMIT, split_text, utf16_len


def test_short_text_is_one_chunk():
    assert split_text("Добрый день!") == ["Добрый день!"]


def test_empty_text_gives_no_chunks():
    assert split_text("   \n  ") == []


def test_splits_on_paragraph_boundaries():
    paragraph = "а" * 3000
    text = "\n\n".join([paragraph, paragraph, paragraph])
    chunks = split_text(text)
    assert chunks == [paragraph, paragraph, paragraph]


def test_keeps_small_paragraphs_together_until_limit():
    paragraphs = [f"Абзац номер {i}. " + "т" * 500 for i in range(12)]
    text = "\n\n".join(paragraphs)
    chunks = split_text(text)
    assert len(chunks) > 1
    for chunk in chunks:
        assert utf16_len(chunk) <= TELEGRAM_LIMIT
        # Каждый фрагмент начинается с начала абзаца, а не с середины слова.
        assert chunk.startswith("Абзац номер")
    assert "\n\n".join(chunks) == text


def test_long_text_without_paragraphs_splits_on_spaces_without_losing_words():
    words = [f"слово{i}" for i in range(2000)]
    text = " ".join(words)
    chunks = split_text(text)
    assert len(chunks) > 1
    for chunk in chunks:
        assert utf16_len(chunk) <= TELEGRAM_LIMIT
    assert " ".join(chunks).split() == text.split()


def test_emoji_count_as_two_telegram_units():
    text = "😀" * 3000  # 6000 UTF-16 единиц, при подсчёте в символах уложилось бы в лимит
    chunks = split_text(text)
    assert len(chunks) == 2
    for chunk in chunks:
        assert utf16_len(chunk) <= TELEGRAM_LIMIT
    assert "".join(chunks) == text


def test_word_free_text_is_hard_split():
    text = "x" * 10000
    chunks = split_text(text)
    assert [len(chunk) for chunk in chunks] == [4096, 4096, 1808]
    assert "".join(chunks) == text


def test_custom_limit_is_respected():
    chunks = split_text("раз два три четыре пять шесть", limit=10)
    assert all(utf16_len(chunk) <= 10 for chunk in chunks)
    assert " ".join(chunks).split() == "раз два три четыре пять шесть".split()
