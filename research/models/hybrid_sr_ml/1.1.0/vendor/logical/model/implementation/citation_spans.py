"""Deterministic, exact source spans suitable for bounded citations."""


def quote_pool(text, max_chars=500):
    """Return exact source substrings, covering long text with overlapping spans.

    Documents at or below the limit remain one complete quote. Longer documents
    are covered by max-sized windows, including the first and last full windows.
    Very short documents cannot satisfy the caller's existing eight-character
    citation minimum, so they yield no citation candidate.
    """
    if not isinstance(text, str):
        raise TypeError("text must be str")
    if isinstance(max_chars, bool) or not isinstance(max_chars, int):
        raise TypeError("max_chars must be an integer")
    if max_chars < 8:
        raise ValueError("max_chars must be at least 8 characters")
    if len(text) < 8:
        return []
    if len(text) <= max_chars:
        return [text]

    # At the default limit this is a 400-character stride with 100-character
    # overlap. Smaller custom windows preserve overlap when possible.
    stride = max(1, max_chars - min(100, max_chars // 5))
    last_start = len(text) - max_chars
    starts = list(range(0, last_start + 1, stride))
    if starts[-1] != last_start:
        starts.append(last_start)

    spans = []
    for start in starts:
        quote = text[start:start + max_chars]
        if len(quote) < 8:
            continue
        # Keep repeated text windows: they may cover distinct source locations.
        spans.append(quote)
    return spans
