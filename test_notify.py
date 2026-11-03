from notify import chunk_lines


def test_chunks_split_on_line_boundaries_and_respect_limit():
    lines = [f"• [기사 {i}](<https://news.example/{'x' * 280}>)" for i in range(12)]
    chunks = chunk_lines("\n".join(lines), limit=1900)
    assert len(chunks) > 1 and all(len(c) <= 1900 for c in chunks)
    assert "\n".join(chunks).split("\n") == lines          # 줄이 하나도 안 잘렸다


def test_overlong_single_line_is_hard_split():
    assert [len(c) for c in chunk_lines("a" * 4000, limit=1900)] == [1900, 1900, 200]
