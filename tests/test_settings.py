"""Personal identifiers (gmail address, Bilt phone) resolve from env or 1Password —
never from config.toml, which is committed-adjacent (config.example.toml) and was
how the personal email leaked into nix-config."""

import pytest

from notion_finance_sync.config import settings


@pytest.fixture(autouse=True)
def _clear_caches():
    settings.get_gmail_address.cache_clear()
    settings.get_bilt_phone.cache_clear()
    yield
    settings.get_gmail_address.cache_clear()
    settings.get_bilt_phone.cache_clear()


def test_gmail_address_env_override(monkeypatch):
    monkeypatch.setenv("GMAIL_ADDRESS", "override@example.com")
    assert settings.get_gmail_address() == "override@example.com"


def test_gmail_address_from_1password(monkeypatch, mocker):
    monkeypatch.delenv("GMAIL_ADDRESS", raising=False)
    read = mocker.patch.object(settings, "_read_op_secret", return_value="me@example.com")
    assert settings.get_gmail_address() == "me@example.com"
    read.assert_called_once_with(
        f"op://{settings.OP_VAULT}/{settings.OP_PERSONAL_IDS_ITEM}/gmail_address"
    )


def test_bilt_phone_env_override(monkeypatch):
    monkeypatch.setenv("BILT_PHONE", "5550001111")
    assert settings.get_bilt_phone() == "5550001111"


def test_bilt_phone_from_1password(monkeypatch, mocker):
    monkeypatch.delenv("BILT_PHONE", raising=False)
    read = mocker.patch.object(settings, "_read_op_secret", return_value="5552223333")
    assert settings.get_bilt_phone() == "5552223333"
    read.assert_called_once_with(
        f"op://{settings.OP_VAULT}/{settings.OP_PERSONAL_IDS_ITEM}/bilt_phone"
    )
