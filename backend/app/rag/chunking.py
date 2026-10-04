from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Chunk:
    section: str
    text: str


def structure_aware_chunks(text: str, target_words: int = 120, overlap_words: int = 20) -> list[Chunk]:
    sections = re.split(r'\n(?=#+\s)', text.strip())
    chunks: list[Chunk] = []
    for block in sections:
        lines = block.splitlines()
        if not lines:
            continue
        title = lines[0].lstrip('#').strip() or 'General'
        body = ' '.join(x.strip() for x in lines[1:] if x.strip())
        words = body.split()
        if not words:
            continue
        start = 0
        while start < len(words):
            end = min(len(words), start + target_words)
            chunk_words = words[start:end]
            chunks.append(Chunk(section=title, text=' '.join(chunk_words)))
            if end == len(words):
                break
            start = max(0, end - overlap_words)
    return chunks
