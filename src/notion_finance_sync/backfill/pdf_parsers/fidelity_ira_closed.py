"""Closed Fidelity IRA statement PDF parser.

Will be written when Alex digs up old Fidelity IRA statements. Layout unknown
until then. Some data may need manual CSV import — see SPEC §16.
"""

from __future__ import annotations

from pathlib import Path

from notion_finance_sync.models import TransactionRecord


def parse(pdf_paths: list[Path]) -> list[TransactionRecord]:
    raise NotImplementedError(
        "TODO: design pending availability of closed-Fidelity-IRA statements."
    )
