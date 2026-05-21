"""BofA statement PDF parser.

BofA's statement layout is consistent across the credit card products. Use
pdfplumber to extract the transaction table from each statement. Categories
and rewards data are NOT in PDFs — those fields stay null on PDF-sourced rows
(per SPEC §10/§11).
"""

from __future__ import annotations

from pathlib import Path

from notion_finance_sync.models import TransactionRecord


def parse(pdf_paths: list[Path]) -> list[TransactionRecord]:
    raise NotImplementedError(
        "TODO: pdfplumber-based extraction. One transaction row per line in the "
        "Transactions table. Date / Description / Amount columns. Set bank='Bank of America', "
        "leave Category and rewards fields null."
    )
