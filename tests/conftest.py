import json
from pathlib import Path

import pytest

from zatca_tools import Zatca

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def spec():
    return json.loads((FIXTURES / "scenarios.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def test_credentials():
    """A test-only certificate and key, generated for these tests. Never valid at ZATCA."""
    return {
        "certificate": (FIXTURES / "test_certificate.txt").read_text().strip(),
        "secret": "test-secret",
        "private_key": (FIXTURES / "test_private_key.pem").read_text(),
    }


@pytest.fixture
def zatca(spec, test_credentials):
    return Zatca("simulation", seller=spec["seller"], credentials=test_credentials, chain="new")


@pytest.fixture
def simple_invoice():
    return {
        "type": "simplified",
        "number": "INV-1",
        "date": "2026-10-01",
        "time": "09:00:00",
        "items": [{"name": "Service", "quantity": 1, "unit_price": 100}],
    }


@pytest.fixture
def buyer():
    return {"name": "Buyer Co", "vat_number": "399999999800003", "address": {"street": "Main", "city": "Riyadh", "building_number": "1234", "district": "Olaya", "postal_code": "12345"}}
