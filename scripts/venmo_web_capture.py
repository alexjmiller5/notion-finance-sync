"""Force a manual re-capture of the Venmo web session (cookies + external_id).

The daily sync captures its own session automatically when cookies are missing or expired
(see ``notion_finance_sync.banks.venmo_session.capture_session``); this script just invokes
that same logic for a manual/forced refresh:

    PYTHONPATH=src uv run python scripts/venmo_web_capture.py

Credentials come from env (VENMO_LOGIN_EMAIL/VENMO_PASSWORD) or the vault getters.
"""

from __future__ import annotations

import sys

from notion_finance_sync.banks.venmo_session import capture_session


def main() -> int:
    capture_session()
    return 0


if __name__ == "__main__":
    sys.exit(main())
