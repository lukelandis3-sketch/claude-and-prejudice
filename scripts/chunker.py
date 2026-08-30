"""Turn arbitrary prose into single-line fragments fit for a spinner or status line.

Sentences are kept whole (a long one is allowed to wrap rather than be cut), except
where a "sentence" is so long it would swallow the screen -- those are split at clause
boundaries and, failing that, at word boundaries.
"""

import re
import unicodedata

# Sentence-ending punctuation followed by optional closing quotes/brackets, then space.
_SENTENCE_END = re.compile(r'(?<=[.!?…])["\'”’)\]]*\s+')

# Abbreviations that end in a period but do not end a sentence.
_ABBREV = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "mt", "vs", "etc", "e.g",
    "i.e", "cf", "al", "fig", "no", "vol", "ch", "pp", "ed", "trans", "approx",
}

_CLAUSE_SPLIT = re.compile(
    r'(?<=[;:—])\s+|(?<=,)\s+(?=(?:and|but|or|which|who|that|while|though)\s)'
)

_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
_ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')

DEFAULT_HARD_MAX = 280
MIN_FRAGMENT = 2


def clean_text(raw):
    """Normalise raw extracted text into a single whitespace-collapsed string."""
    if not raw:
        return ""
    text = unicodedata.normalize("NFC", raw)
    text = _ANSI.sub(" ", text)
    text = _CONTROL.sub(" ", text)
    # Soft hyphen and zero-width characters read as garbage in a terminal.
    for junk in ("­", "​", "﻿"):
        text = text.replace(junk, "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _ends_with_abbreviation(chunk):
    match = re.search(r'([A-Za-z.]+)\.$', chunk.strip())
    if not match:
        return False
    return match.group(1).lower().strip(".") in _ABBREV


def split_sentences(text):
    """Split cleaned text into sentences, rejoining false breaks after abbreviations."""
    if not text:
        return []
    sentences = []
    for piece in _SENTENCE_END.split(text):
        piece = piece.strip()
        if not piece:
            continue
        if sentences and _ends_with_abbreviation(sentences[-1]):
            sentences[-1] = sentences[-1] + " " + piece
        else:
            sentences.append(piece)
    return sentences


def _wrap_words(sentence, hard_max):
    """Last resort: break an over-long run at word boundaries."""
    out, current = [], ""
    for word in sentence.split(" "):
        candidate = word if not current else current + " " + word
        if len(candidate) <= hard_max:
            current = candidate
            continue
        if current:
            out.append(current)
            current = ""
        # A single word longer than hard_max still has to go somewhere.
        while len(word) > hard_max:
            out.append(word[:hard_max])
            word = word[hard_max:]
        current = word
    if current:
        out.append(current)
    return out


def _split_long(sentence, hard_max):
    """Split an over-long sentence at clause boundaries, then at word boundaries."""
    clauses = [c.strip() for c in _CLAUSE_SPLIT.split(sentence) if c and c.strip()]
    if len(clauses) <= 1:
        return _wrap_words(sentence, hard_max)

    out, current = [], ""
    for clause in clauses:
        candidate = clause if not current else current + " " + clause
        if len(candidate) <= hard_max:
            current = candidate
            continue
        if current:
            out.append(current)
            current = ""
        if len(clause) <= hard_max:
            current = clause
        else:
            out.extend(_wrap_words(clause, hard_max))
    if current:
        out.append(current)
    return out


def is_meaningful(fragment):
    """Reject fragments that are only punctuation, digits, or too short to read."""
    if len(fragment) < MIN_FRAGMENT:
        return False
    return any(ch.isalpha() for ch in fragment)


def to_fragments(raw, hard_max=DEFAULT_HARD_MAX):
    """Full pipeline: raw text in, displayable one-line fragments out.

    Every returned fragment is non-empty and contains at least one letter -- an empty
    verbs array makes Claude Code fall back to its stock gerunds silently.
    """
    text = clean_text(raw)
    fragments = []
    for sentence in split_sentences(text):
        parts = [sentence] if len(sentence) <= hard_max else _split_long(sentence, hard_max)
        for part in parts:
            part = part.strip()
            if is_meaningful(part):
                fragments.append(part)
    return fragments
