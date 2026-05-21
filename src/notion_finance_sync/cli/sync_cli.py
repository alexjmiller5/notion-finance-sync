"""Importable CLI logic for the notion-finance-sync sync command.

This module contains the actual business logic so it can be imported and tested
directly. The thin shim ``scripts/sync.py`` delegates here.

Exit codes:
    0  — all banks succeeded or were skipped
    1  — at least one bank failed
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta

import structlog

from notion_finance_sync.sync.orchestrator import SyncResult, run_all_banks, run_one_bank

logger = structlog.get_logger()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments. Pass ``argv`` explicitly in tests; defaults to sys.argv[1:]."""
    p = argparse.ArgumentParser(description="notion-finance-sync CLI")
    p.add_argument(
        "--bank",
        help="Session ID to sync (bofa, wells_fargo, us_bank, bilt, everbank, "
        "venmo, etrade, fidelity). Omit to sync all active banks.",
    )
    p.add_argument(
        "--interactive",
        action="store_true",
        help="Manual escape hatch — pause at unsolvable challenges (CAPTCHA, "
        "unknown 2FA flow) for human intervention, then resume. "
        "(Not yet implemented — will log a warning and continue.)",
    )
    p.add_argument(
        "--since",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        default=date.today() - timedelta(days=14),
        help="Earliest transaction date to fetch (default: today - 14 days).",
    )
    p.add_argument(
        "--skip-enrichers",
        action="store_true",
        help="Skip Phase 2 enrichers (Bilt portal, BofA rewards, Wells rewards).",
    )
    return p.parse_args(argv)


def _format_result(result: SyncResult) -> str:
    """Format a single SyncResult as a human-readable summary line."""
    if result.status == "success":
        counts = (
            f"{result.transactions_created} created, "
            f"{result.transactions_updated} updated, "
            f"{result.transactions_unchanged} unchanged, "
            f"{result.pending_released} released"
        )
        return (
            f"{result.session_id:<16} {result.status:<8} ({counts})  {result.duration_seconds:.2f}s"
        )
    elif result.status == "failure":
        error = result.error or "unknown error"
        return (
            f"{result.session_id:<16} {result.status:<8} "
            f"({error}) {result.attempts} attempts, {result.duration_seconds:.1f}s"
        )
    else:  # skipped
        return f"{result.session_id:<16} {result.status:<8}   {result.duration_seconds:.2f}s"


async def main(argv: list[str] | None = None) -> int:
    """Entry point for the sync CLI.

    Args:
        argv: Argument list (defaults to sys.argv[1:] via argparse when None).

    Returns:
        Exit code: 0 if all banks succeeded/skipped, 1 if any failed.
    """
    args = parse_args(argv)

    if args.interactive:
        logger.warning(
            "interactive_mode_not_implemented",
            message="--interactive is not yet supported; the orchestrator will run "
            "non-interactively. This flag will be wired in a future task.",
        )

    logger.info(
        "sync_start",
        bank=args.bank or "all",
        interactive=args.interactive,
        since=args.since.isoformat(),
    )

    results: dict[str, SyncResult]

    if args.bank:
        result = await run_one_bank(args.bank, since=args.since)
        results = {args.bank: result}
    else:
        results = await run_all_banks(
            since=args.since,
            skip_enrichers=args.skip_enrichers,
        )

    # Print summary to stdout (user-facing CLI output)
    for result in results.values():
        print(_format_result(result))

    any_failed = any(r.status == "failure" for r in results.values())
    return 1 if any_failed else 0


if __name__ == "__main__":
    import asyncio

    sys.exit(asyncio.run(main()))
