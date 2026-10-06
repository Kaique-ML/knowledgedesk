# Demo script

1. Start search mode with `scripts/bootstrap.ps1`, sign in as the sales fixture and show the active mode label.
2. Open the library. Show public documents and sales documents; confirm that the finance-only title/marker is absent.
3. Ask for `AX-120` warranty details. Open the returned source and its locator.
4. Ask for the nonexistent `ZZ-001` warranty and show insufficient evidence rather than a fabricated value.
5. As an editor/admin, upload a replacement Markdown policy. While its job is queued, show that the old active version remains searchable; after completion, show the atomic version switch.
6. Restrict a document to Finance and return to the sales session. Confirm search, original, citation and history no longer disclose it.
7. Submit feedback and explicitly confirm a human-review request. Resolve it in the internal review view.
8. In RAG mode only, repeat an answer and inspect block-level citations. Stop Ollama and show a typed technical failure rather than an insufficient-evidence claim.

Desktop and narrow-screen screenshots must be captured from the running application. None are committed until that inspection is performed.
