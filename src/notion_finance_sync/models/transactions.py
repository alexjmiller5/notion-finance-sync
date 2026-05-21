from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Literal


class TransactionStatus(StrEnum):
    PENDING = "Pending"
    POSTED = "Posted"
    RELEASED = "Released"


class BankName(StrEnum):
    BANK_OF_AMERICA = "Bank of America"
    US_BANK = "U.S. Bank"
    WELLS_FARGO = "Wells Fargo"
    EVERBANK = "Everbank"
    BILT = "Bilt"
    VENMO = "Venmo"
    ETRADE = "E*Trade"
    FIDELITY = "Fidelity"


class AccountType(StrEnum):
    CREDIT_CARD = "Credit Card"
    DEBIT_CARD = "Debit Card"
    CHECKING = "Checking"
    SAVINGS = "Savings"
    P2P = "P2P"
    BROKERAGE = "Brokerage"
    FOUR_OH_ONE_K = "401k"
    IRA = "IRA"


class CardNetwork(StrEnum):
    VISA = "Visa"
    MASTERCARD = "Mastercard"


class CanonicalCategory(StrEnum):
    AIRFARE = "Airfare"
    TRAVEL = "Travel"
    DINING = "Dining"
    GROCERIES = "Groceries"
    GAS = "Gas"
    STREAMING = "Streaming"
    ONLINE_SHOPPING = "Online Shopping"
    CONVENIENCE = "Convenience"
    DEPARTMENT_STORES = "Department Stores"
    WHOLESALE_CLUBS = "Wholesale Clubs"
    TRANSIT = "Transit"
    BILLS_UTILITIES = "Bills & Utilities"
    HEALTHCARE = "Healthcare"
    CASH_ATM = "Cash & ATM"
    TRANSFER = "Transfer"
    INCOME = "Income"
    RENT = "Rent"
    OTHER = "Other"


@dataclass
class TransactionRecord:
    """A single transaction event. Same shape for spending and investment events.

    Investment events populate `quantity`, `ticker`, `price_per_share` and may
    leave other fields null (cash dividend has no shares; fee has no ticker).
    """

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------
    source_id: str
    """Bank-native transaction ID. Writes to Notion's `Transaction Source ID`."""

    source_account_id: str
    """Bank-native account ID. Writes to Notion's `Source Account ID`."""

    # ------------------------------------------------------------------
    # Core spending fields
    # ------------------------------------------------------------------
    name: str
    """Notion row title — e.g., 'Starbucks', 'Sent to John', 'TSLA Buy'."""

    amount: float
    """Signed transaction amount. Negative = spend/sent. Positive = receive/income."""

    transaction_date: date
    """The logical date the bank associates with the transaction."""

    transacted_at: datetime | None
    """Actual timestamp if the bank exposes it (some do, some don't)."""

    status: TransactionStatus

    # ------------------------------------------------------------------
    # Descriptive
    # ------------------------------------------------------------------
    payee: str = ""
    """Merchant name (spending) or counterparty (P2P)."""

    memo: str = ""
    """Bank-provided memo, plus any scraper-appended notes."""

    bank_category: str | None = None
    """RAW bank category label (audit / re-mappable). Writes to `Bank Category`."""

    category: CanonicalCategory | None = None
    """Canonical category. Writes to `Category`."""

    # ------------------------------------------------------------------
    # Account context
    # ------------------------------------------------------------------
    bank: BankName | None = None
    credit_card_account: str | None = None
    """Notion select value for the specific card or account."""

    card_network: CardNetwork | None = None
    account_type: AccountType | None = None
    account_name: str = ""

    # ------------------------------------------------------------------
    # Rewards (Phase 1 + Phase 2)
    # ------------------------------------------------------------------
    calculated_rewards: float | None = None
    """Computed from `config/cards.yaml`."""

    true_rewards: float | None = None
    """Scraped from bank/portal. Null for US Bank by design."""

    bilt_points: float | None = None
    """Cross-card Bilt rewards. Populated by `bilt_portal` enricher."""

    bilt_partner: bool = False
    """Merchant is a Bilt Neighborhood Dining partner."""

    # ------------------------------------------------------------------
    # Investment-only fields (null for spending txns)
    # ------------------------------------------------------------------
    quantity: float | None = None
    """Share count. Signed: negative = sold/transferred-out. Fractional allowed."""

    ticker: str | None = None
    """Stock symbol (e.g., 'TSLA', 'FXAIX')."""

    price_per_share: float | None = None

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------
    raw_data: dict = field(default_factory=dict)
    """Original scraped data dict. Useful for debugging and reparseability."""


@dataclass
class BalanceSnapshot:
    """A point-in-time balance for an investment or cash account.

    Reserved for future use — v1 records investment EVENTS as TransactionRecords,
    not snapshots. This class will be used when/if we add portfolio rollups later.
    """

    account_id: str
    snapshot_date: date
    balance: float
    bank: BankName
    account_type: AccountType


@dataclass
class HoldingSnapshot:
    """A point-in-time holding (shares × ticker) for an investment account.

    Reserved for future use — same as BalanceSnapshot.
    """

    account_id: str
    snapshot_date: date
    ticker: str
    quantity: float
    price_per_share: float | None = None


CategoryMap = dict[str, CanonicalCategory]
"""Per-bank mapping from raw bank-category labels to canonical categories."""
