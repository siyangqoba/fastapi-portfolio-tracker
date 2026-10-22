from tests.conftest import upload


def test_dashboard_and_docs_load(client):
    assert client.get("/").status_code == 200
    assert client.get("/docs").status_code == 200
    assert client.get("/health").json() == {"status": "ok"}


def test_empty_portfolio(client):
    s = client.get("/api/summary").json()
    assert s["total_value"] == 0 and s["twr"] is None
    assert client.get("/api/holdings").json() == []


def test_sample_data_end_to_end(seeded):
    s = seeded.get("/api/summary").json()
    assert s["as_of"] == "2026-09-30"
    assert s["net_contributions"] == 324_000
    assert abs(s["total_value"] - s["market_value"] - s["cash"]) < 0.01
    holdings = seeded.get("/api/holdings").json()
    assert {h["symbol"] for h in holdings} == {"SAEQ", "GLEQ", "SABND", "PROP", "GOLD"}
    assert abs(sum(h["weight"] for h in holdings) + s["cash"] / s["total_value"] - 1) < 1e-9
    alloc = seeded.get("/api/allocation").json()
    assert abs(sum(a["weight"] for a in alloc) - 1) < 1e-9
    weekly = seeded.get("/api/timeseries?freq=W").json()
    daily = seeded.get("/api/timeseries?freq=D").json()
    assert len(daily) > len(weekly) > 150
    assert weekly[-1]["total_value"] == round(s["total_value"], 2)
    assert len(seeded.get("/api/monthly-returns").json()) == 45


def test_import_is_all_or_nothing(seeded):
    before = len(seeded.get("/api/transactions?limit=1000").json())
    bad = ("date,type,symbol,quantity,price,amount,fees\n"
           "2026-10-01,DEPOSIT,,,,1000,\n"
           "2026-10-01,BUY,NOPE,1,10,,\n"
           "2026-10-01,BUY,SAEQ,0,10,,\n"
           "not-a-date,DEPOSIT,,,,5,\n")
    r = upload(seeded, "transactions", bad)
    assert r.status_code == 422
    errors = r.json()["detail"]["errors"]
    assert any("row 3" in e and "unknown symbol" in e for e in errors)
    assert any("row 4" in e and "positive quantity" in e for e in errors)
    assert any("row 5" in e for e in errors)
    assert len(seeded.get("/api/transactions?limit=1000").json()) == before


def test_import_rejects_history_that_oversells(seeded):
    r = upload(seeded, "transactions", "date,type,symbol,quantity,price,amount,fees\n2026-10-01,SELL,GOLD,10000,1,,\n")
    assert r.status_code == 422
    assert "cannot sell" in r.json()["detail"]["errors"][0]


def test_missing_columns(client):
    r = upload(client, "prices", "date,ticker,close\n")
    assert r.status_code == 422
    assert r.json()["detail"]["errors"] == ["missing column(s): symbol"]


def test_price_import_upserts(client):
    upload(client, "instruments", "symbol,name,asset_class\nAAA,Test Fund,equity\n")
    assert upload(client, "prices", "date,symbol,close\n2024-01-02,AAA,10\n").json()["imported"] == 1
    assert upload(client, "prices", "date,symbol,close\n2024-01-02,AAA,12\n").json()["imported"] == 1
    client.post("/api/transactions", json={"date": "2024-01-02", "type": "DEPOSIT", "amount": 100})
    client.post("/api/transactions", json={"date": "2024-01-02", "type": "BUY", "symbol": "AAA",
                                           "quantity": 5, "price": 12})
    assert client.get("/api/holdings").json()[0]["price"] == 12


def test_create_and_delete_transactions(client):
    upload(client, "instruments", "symbol,name,asset_class\nAAA,Test Fund,Equity\n")
    dep = client.post("/api/transactions", json={"date": "2024-01-02", "type": "DEPOSIT", "amount": 500})
    assert dep.status_code == 201
    buy = client.post("/api/transactions", json={"date": "2024-01-03", "type": "BUY", "symbol": "AAA",
                                                 "quantity": 4, "price": 100, "fees": 5})
    assert buy.status_code == 201
    # deleting the deposit would leave the buy unfunded
    r = client.delete(f"/api/transactions/{dep.json()['id']}")
    assert r.status_code == 422 and "negative" in r.json()["detail"]
    assert client.delete(f"/api/transactions/{buy.json()['id']}").status_code == 204
    assert client.delete(f"/api/transactions/{dep.json()['id']}").status_code == 204
    assert client.delete("/api/transactions/9999").status_code == 404


def test_validation_messages(client):
    r = client.post("/api/transactions", json={"date": "2024-01-02", "type": "BUY", "symbol": "AAA"})
    assert r.status_code == 422
    r = client.post("/api/transactions", json={"date": "2024-01-02", "type": "BUY", "symbol": "ZZZ",
                                               "quantity": 1, "price": 1})
    assert r.status_code == 404
    r = client.post("/api/instruments", json={"symbol": "bad symbol", "name": "x", "asset_class": "Equity"})
    assert r.status_code == 422


def test_duplicate_instrument(client):
    payload = {"symbol": "AAA", "name": "Test", "asset_class": "Equity"}
    assert client.post("/api/instruments", json=payload).status_code == 201
    assert client.post("/api/instruments", json=payload).status_code == 409
