import importlib
import sqlite3

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.schemas import CounterOffer, Product, QuoteRequest


def _reload_db_stack():
    import app.db as db_module
    import app.create_buyer as create_buyer_module
    import app.seed as seed_module
    import app.main as main_module

    importlib.reload(db_module)
    importlib.reload(create_buyer_module)
    importlib.reload(seed_module)
    importlib.reload(main_module)
    return db_module, create_buyer_module, seed_module, main_module


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("CATALOGAGENT_DB_PATH", str(db_file))
    db_module, create_buyer_module, seed_module, main_module = _reload_db_stack()
    yield db_module, create_buyer_module, seed_module, main_module
    _reload_db_stack()


@pytest.fixture
def client(temp_db):
    _, _, _, main_module = temp_db
    with TestClient(main_module.app) as test_client:
        yield test_client


def test_schemas_reject_unknown_fields():
    with pytest.raises(ValidationError):
        CounterOffer(
            unit_price=10.0,
            min_volume=100,
            payment_terms_days=30,
            delivery_days=14,
            recurring=False,
            extra_field="nope",
        )

    with pytest.raises(ValidationError):
        QuoteRequest(product_id="p1", qty=100, buyer_id="acme", bonus="nope")


def test_seed_and_catalog(client, temp_db):
    db_module, _, seed_module, _ = temp_db
    db_module.init_db()
    count = seed_module.seed_products()
    assert count >= 10

    r = client.get("/catalog")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")

    products = [Product.model_validate(p) for p in r.json()]
    assert len(products) >= 10
    for product in products:
        assert len(product.volume_tiers) >= 3


def test_create_buyer_hashes(temp_db):
    db_module, create_buyer_module, _, _ = temp_db
    db_module.init_db()
    key = create_buyer_module.create_buyer("testbuyer", 25000.0)
    assert key.startswith("bk_testbuyer_")

    key_hash = create_buyer_module.hash_buyer_key(key)
    buyer = db_module.get_buyer_by_hash(key_hash)
    assert buyer is not None
    assert buyer.buyer_id == "testbuyer"
    assert buyer.budget_cap == 25000.0
    assert buyer.buyer_key_hash == key_hash
    assert buyer.buyer_key_hash != key
    assert len(buyer.buyer_key_hash) == 64

    conn = sqlite3.connect(db_module.DB_PATH)
    row = conn.execute("SELECT * FROM buyers WHERE buyer_id = ?", ("testbuyer",)).fetchone()
    conn.close()
    row_text = str(row)
    assert key not in row_text


def test_create_buyer_rejects_duplicate(temp_db, capsys):
    db_module, create_buyer_module, _, _ = temp_db
    db_module.init_db()
    create_buyer_module.create_buyer("dupbuyer", 10000.0)
    code = create_buyer_module.main(["dupbuyer", "--budget", "20000"])
    assert code == 1
    captured = capsys.readouterr()
    assert "already exists" in captured.err


def test_catalog_public(client, temp_db):
    _, _, seed_module, _ = temp_db
    seed_module.seed_products()

    r = client.get("/catalog")
    assert r.status_code == 200
