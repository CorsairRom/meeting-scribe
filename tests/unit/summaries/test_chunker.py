from meeting_scribe.summaries.domain.chunker import (
    chunk_transcript,
    estimate_tokens,
    fits,
    format_line,
    pack_texts,
)
from tests.unit.summaries.factories import line


def test_estimate_is_two_and_a_quarter_characters_per_token_rounded_up() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("a") == 1
    assert estimate_tokens("a" * 9) == 4
    assert estimate_tokens("a" * 10) == 5


def test_fits_compares_the_estimate_with_the_budget() -> None:
    assert fits("a" * 45, 20)
    assert not fits("a" * 46, 20)


def test_format_line_shows_name_and_timestamp() -> None:
    assert (
        format_line(line("Person A", 3725.0, "hello there.")) == "[01:02:05] Person A: hello there."
    )


def test_everything_fits_in_one_chunk() -> None:
    lines = tuple(line(start=float(n), text=f"word{n}.") for n in range(3))

    chunks = chunk_transcript(lines, budget_tokens=1000)

    assert chunks == ("\n".join(format_line(item) for item in lines),)


def test_chunks_split_on_utterance_boundaries_and_each_fits() -> None:
    lines = tuple(line(start=float(n), text=f"item{n} " * 6) for n in range(10))

    chunks = chunk_transcript(lines, budget_tokens=60)

    assert len(chunks) > 1
    assert all(fits(chunk, 60) for chunk in chunks)
    assert "\n".join(chunks) == "\n".join(format_line(item) for item in lines)


def test_oversized_utterance_is_split_on_sentences_and_nothing_is_lost() -> None:
    sentences = [f"Sentence number {n} is here." for n in range(6)]
    big = line(text=" ".join(sentences))

    chunks = chunk_transcript((big,), budget_tokens=25)

    assert len(chunks) > 1
    assert all(fits(chunk, 25) for chunk in chunks)
    joined = "\n".join(chunks)
    assert all(joined.count(sentence) == 1 for sentence in sentences)


def test_a_sentence_longer_than_the_budget_is_split_on_characters() -> None:
    chunks = chunk_transcript((line(text="x" * 200),), budget_tokens=20)

    assert all(fits(chunk, 20) for chunk in chunks)
    assert sum(chunk.count("x") for chunk in chunks) == 200


def test_pack_texts_groups_in_order_within_the_budget() -> None:
    batches = pack_texts(["a" * 9, "b" * 9, "c" * 9], budget_tokens=10)

    assert [len(batch) for batch in batches] == [2, 1]
    assert [text[0] for batch in batches for text in batch] == ["a", "b", "c"]


def test_pack_texts_keeps_an_oversized_text_alone() -> None:
    batches = pack_texts(["a" * 100, "b"], budget_tokens=10)

    assert [len(batch) for batch in batches] == [1, 1]
