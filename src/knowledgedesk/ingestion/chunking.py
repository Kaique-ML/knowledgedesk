import hashlib
from dataclasses import dataclass

from knowledgedesk.ingestion.parsers import ExtractionBlock


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    sequence: int
    content: str
    content_sha256: str
    locator: dict[str, object]


def chunk_blocks(
    blocks: tuple[ExtractionBlock, ...], *, max_characters: int = 1400
) -> list[DocumentChunk]:
    if max_characters < 200:
        raise ValueError("max_characters must be at least 200")
    chunks: list[DocumentChunk] = []
    pending_text = ""
    pending_locators: list[dict[str, object]] = []

    def flush() -> None:
        nonlocal pending_text, pending_locators
        content = pending_text.strip()
        if content:
            locator: dict[str, object] = (
                pending_locators[0] if len(pending_locators) == 1 else {"parts": pending_locators}
            )
            chunks.append(
                DocumentChunk(
                    sequence=len(chunks),
                    content=content,
                    content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    locator=locator,
                )
            )
        pending_text = ""
        pending_locators = []

    for block in blocks:
        text = block.text.strip()
        if not text:
            continue
        if len(text) > max_characters:
            flush()
            for offset in range(0, len(text), max_characters):
                part = text[offset : offset + max_characters]
                locator = {
                    **block.locator,
                    "character_start": offset,
                    "character_end": offset + len(part),
                }
                chunks.append(
                    DocumentChunk(
                        sequence=len(chunks),
                        content=part,
                        content_sha256=hashlib.sha256(part.encode("utf-8")).hexdigest(),
                        locator=locator,
                    )
                )
            continue
        candidate = f"{pending_text}\n\n{text}" if pending_text else text
        if len(candidate) > max_characters:
            flush()
            pending_text = text
            pending_locators = [block.locator]
        else:
            pending_text = candidate
            pending_locators.append(block.locator)
    flush()
    return chunks
