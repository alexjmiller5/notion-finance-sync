"""TD Bank statement PDF parser.

Will be written when Alex receives PDFs from TD customer service. Statement
layout is unknown until we see one.
"""

from __future__ import annotations

from pathlib import Path

from notion_finance_sync.models import TransactionRecord


def parse(pdf_paths: list[Path]) -> list[TransactionRecord]:
    raise NotImplementedError(
        "TODO: design pending receipt of TD Bank PDFs. Statement layout unknown until then."
    )
