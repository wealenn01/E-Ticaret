"""Kapsamlı API testleri — tüm endpoint'lerin doğru yanıt döndürdüğünü doğrular.

Çalıştırmak için:
    pip install httpx pytest
    pytest tests/test_api.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp()
os.environ.setdefault("GEMINI_API_KEY", "")
os.environ.setdefault("RESEND_API_KEY", "")

import app.db as _db
_db.DB_PATH = Path(_TMP) / "test.db"
_db.DATA_DIR = Path(_TMP)
_db.DATA_DIR.mkdir(exist_ok=True)
_db.init_db()

import app.mail as _mail
_mail.is_configured = lambda: False

import app.auth as _auth

from app.main import app
client = TestClient(app, raise_server_exceptions=True)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _register(email: str, password: str = "Test1234!", name: str = "Test") -> dict:
    user = _auth.create_user(email, password, name, auto_verify=True)
    token = _auth.create_session(user["id"])
    client.cookies.set(_auth.SESSION_COOKIE, token)
    return _db.query_one("SELECT * FROM users WHERE id=?", (user["id"],))


def _logout() -> None:
    client.post("/api/auth/logout")
    client.cookies.clear()


class TestAuth:
    def test_register_no_mail_503(self):
        r = client.post("/api/auth/register", json={
            "email": f"reg_{_uid()}@x.com", "password": "Test1234!"
        })
        assert r.status_code == 503

    def test_login_success(self):
        email = f"login_{_uid()}@x.com"
        _register(email)
        _logout()
        r = client.post("/api/auth/login", json={"email": email, "password": "Test1234!"})
        assert r.status_code == 200
        assert r.json()["email"] == email
        _logout()

    def test_login_wrong_password(self):
        email = f"loginbad_{_uid()}@x.com"
        _register(email)
        _logout()
        r = client.post("/api/auth/login", json={"email": email, "password": "YANLIS!"})
        assert r.status_code == 401
        _logout()

    def test_login_nonexistent(self):
        r = client.post("/api/auth/login", json={"email": "yok@x.com", "password": "Test1234!"})
        assert r.status_code == 401

    def test_me_authenticated(self):
        email = f"me_{_uid()}@x.com"
        _register(email)
        r = client.get("/api/auth/me")
        assert r.status_code == 200
        assert r.json()["email"] == email
        assert r.json()["email_verified"] is True
        _logout()

    def test_me_unauthenticated(self):
        _logout()
        assert client.get("/api/auth/me").status_code == 401

    def test_logout(self):
        _register(f"lo_{_uid()}@x.com")
        client.post("/api/auth/logout")
        assert client.get("/api/auth/me").status_code == 401
        _logout()

    def test_demo_account_creation(self):
        r = client.post("/api/auth/demo")
        assert r.status_code == 200
        d = r.json()
        assert d.get("demo") is True
        assert d.get("email_verified") is True
        assert d.get("is_demo") is True
        assert "demo_email" in d and "demo_password" in d
        assert d["plan"] == "free"
        _logout()

    def test_demo_data_seeded(self):
        r = client.post("/api/auth/demo")
        assert r.status_code == 200
        stats = client.get("/api/assistant/stats")
        assert stats.status_code == 200
        d = stats.json()
        has_data = (d.get("negative_reviews", 0) > 0 or d.get("total_products", 0) > 0 or bool(d.get("complaint_breakdown")))
        assert has_data, f"Demo verisi yüklenmemiş: {d}"
        _logout()

    def test_email_not_verified_blocked(self):
        user = _auth.create_user(f"unv_{_uid()}@x.com", "Test1234!", auto_verify=False)
        token = _auth.create_session(user["id"])
        client.cookies.set(_auth.SESSION_COOKIE, token)
        r = client.get("/api/mp/orders")
        assert r.status_code == 403
        assert r.json().get("code") == "email_not_verified"
        _logout()

    def test_verify_email_code(self):
        user = _auth.create_user(f"vfy_{_uid()}@x.com", "Test1234!", auto_verify=False)
        token = _auth.create_session(user["id"])
        client.cookies.set(_auth.SESSION_COOKIE, token)
        code = _auth._create_verification_code(user["id"])
        r = client.post("/api/auth/verify-email-code", json={"code": code})
        assert r.status_code == 200
        assert r.json().get("ok") is True
        _logout()

    def test_verify_wrong_code(self):
        user = _auth.create_user(f"wc_{_uid()}@x.com", "Test1234!", auto_verify=False)
        token = _auth.create_session(user["id"])
        client.cookies.set(_auth.SESSION_COOKIE, token)
        _auth._create_verification_code(user["id"])
        r = client.post("/api/auth/verify-email-code", json={"code": "00000"})
        assert r.status_code == 400
        _logout()

    def test_sql_injection_email(self):
        r = client.post("/api/auth/login", json={"email": "' OR '1'='1", "password": "x"})
        assert r.status_code == 401


class TestConnections:
    def setup_method(self):
        _register(f"conn_{_uid()}@x.com")

    def teardown_method(self):
        _logout()

    def test_list_platforms_public(self):
        _logout()
        r = client.get("/api/connections/platforms")
        assert r.status_code == 200
        assert {p["key"] for p in r.json()} == {"trendyol", "hepsiburada", "n11", "amazon"}

    def test_add_trendyol_no_keys(self):
        r = client.post("/api/connections", json={"platform": "trendyol", "credentials": {}})
        assert r.status_code == 200
        assert r.json()["connection"]["status"] == "unverified"

    def test_add_hepsiburada(self):
        r = client.post("/api/connections", json={"platform": "hepsiburada", "credentials": {}})
        assert r.status_code == 200

    def test_add_n11(self):
        r = client.post("/api/connections", json={"platform": "n11", "credentials": {}})
        assert r.status_code == 200

    def test_add_amazon(self):
        r = client.post("/api/connections", json={"platform": "amazon", "credentials": {}})
        assert r.status_code == 200

    def test_duplicate_rejected(self):
        client.post("/api/connections", json={"platform": "n11", "credentials": {}})
        r = client.post("/api/connections", json={"platform": "n11", "credentials": {}})
        assert r.status_code == 409

    def test_unknown_platform(self):
        r = client.post("/api/connections", json={"platform": "bilinmeyen", "credentials": {}})
        assert r.status_code == 400

    def test_list_connections(self):
        client.post("/api/connections", json={"platform": "trendyol", "credentials": {}})
        r = client.get("/api/connections")
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_credentials_masked(self):
        r = client.post("/api/connections", json={
            "platform": "hepsiburada",
            "credentials": {"merchant_id": "12345", "api_key": "gizli_sifre_abcd"}
        })
        assert r.status_code == 200
        assert r.json()["connection"]["credentials"].get("api_key", "").startswith("••••")

    def test_unauthenticated(self):
        _logout()
        assert client.get("/api/connections").status_code == 401

    def test_other_user_inaccessible(self):
        r = client.post("/api/connections", json={"platform": "n11", "credentials": {}})
        conn_id = r.json()["connection"]["id"]
        _logout()
        _register(f"other_{_uid()}@x.com")
        assert client.put(f"/api/connections/{conn_id}", json={"platform": "n11", "credentials": {}}).status_code == 404


class TestOps:
    def setup_method(self):
        _register(f"ops_{_uid()}@x.com")

    def teardown_method(self):
        _logout()

    def test_overview(self):
        r = client.get("/api/mp/overview")
        assert r.status_code == 200
        for k in ("orders_total", "waiting_questions", "low_stock_count", "platforms"):
            assert k in r.json()

    def test_orders(self):
        r = client.get("/api/mp/orders")
        assert r.status_code == 200
        d = r.json()
        assert "rows" in d and "platforms" in d
        if d["rows"]:
            for k in ("order_id", "date", "status", "total", "items", "cargo"):
                assert k in d["rows"][0]

    def test_orders_sorted(self):
        rows = client.get("/api/mp/orders").json().get("rows", [])
        if len(rows) > 1:
            dates = [x["date"] for x in rows if x["date"]]
            assert dates == sorted(dates, reverse=True)

    def test_orders_status_filter(self):
        r = client.get("/api/mp/orders?status=Delivered")
        assert r.status_code == 200
        for o in r.json().get("rows", []):
            assert o["status"] == "Delivered"

    def test_products(self):
        r = client.get("/api/mp/products")
        assert r.status_code == 200
        d = r.json()
        assert "rows" in d and "threshold" in d
        if d["rows"]:
            for k in ("sku", "title", "price", "stock", "status", "low_stock"):
                assert k in d["rows"][0]

    def test_questions(self):
        r = client.get("/api/mp/questions")
        assert r.status_code == 200
        if r.json().get("rows"):
            for k in ("question_id", "product", "question", "status"):
                assert k in r.json()["rows"][0]

    def test_claims(self):
        assert client.get("/api/mp/claims").status_code == 200

    def test_settings(self):
        assert client.get("/api/mp/settings").json()["low_stock_threshold"] == 5
        client.post("/api/mp/settings", json={"low_stock_threshold": 10})
        assert client.get("/api/mp/settings").json()["low_stock_threshold"] == 10

    def test_price_stock_demo(self):
        r = client.post("/api/mp/price-stock", json={
            "platform": "trendyol",
            "items": [{"sku": "SKU-KUPA-01", "price": 299.9, "stock": 50}]
        })
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_price_stock_empty(self):
        assert client.post("/api/mp/price-stock", json={"platform": "trendyol", "items": []}).status_code == 400

    def test_finance_requires_plan(self):
        assert client.get("/api/mp/finance").status_code == 402

    def test_unauthenticated(self):
        _logout()
        assert client.get("/api/mp/orders").status_code == 401


class TestBilling:
    def setup_method(self):
        _register(f"bill_{_uid()}@x.com")

    def teardown_method(self):
        _logout()

    def test_plans_public(self):
        _logout()
        r = client.get("/api/billing/plans")
        assert r.status_code == 200
        d = r.json()
        plans = d.get("plans", d) if isinstance(d, dict) else d
        assert {p["key"] for p in plans} == {"free", "starter", "pro", "enterprise"}

    def test_free_plan_values(self):
        _logout()
        d = client.get("/api/billing/plans").json()
        plans = d.get("plans", d) if isinstance(d, dict) else d
        free = next(p for p in plans if p["key"] == "free")
        assert free["price"] == 0
        assert free["limits"]["ai_listing"] == 3
        assert free["limits"]["stores"] == 1

    def test_subscription(self):
        r = client.get("/api/billing/subscription")
        assert r.status_code == 200
        d = r.json()
        assert "subscription" in d and "usage" in d and "limits" in d
        assert d["subscription"]["plan"] == "free"

    def test_invoices(self):
        assert client.get("/api/billing/invoices").status_code == 200

    def test_unauthenticated(self):
        _logout()
        assert client.get("/api/billing/subscription").status_code == 401


class TestAssistant:
    def setup_method(self):
        _register(f"asst_{_uid()}@x.com")

    def teardown_method(self):
        _logout()

    def _upload(self, n: int = 3) -> dict:
        rows = "\n".join(f"Ürün {i},SKU-{i},{5 if i%2 else 2},Yorum {i},2026-01-0{i}" for i in range(1, n+1))
        csv = f"product_name,sku,rating,comment_text,review_date\n{rows}".encode()
        r = client.post("/api/assistant/import-reviews", files={"file": ("r.csv", csv, "text/csv")})
        assert r.status_code == 200
        return r.json()

    def test_import_reviews(self):
        d = self._upload(3)
        assert d["added"] == 3

    def test_stats(self):
        self._upload(4)
        r = client.get("/api/assistant/stats")
        assert r.status_code == 200
        d = r.json()
        assert d.get("total_reviews", 0) > 0 or d.get("total_products", 0) > 0

    def test_settings(self):
        assert client.get("/api/assistant/settings").status_code == 200
        r = client.post("/api/assistant/settings", json={"platform": "trendyol", "seller_model": "manufacturer"})
        assert r.status_code == 200

    def test_delete_reviews(self):
        self._upload(2)
        r = client.delete("/api/assistant/reviews")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_ask_empty(self):
        assert client.post("/api/assistant/ask", json={"question": ""}).status_code == 400

    def test_ask_no_gemini(self):
        self._upload(2)
        r = client.post("/api/assistant/ask", json={"question": "En çok şikayet edilen ürün?"})
        assert r.status_code in (200, 503)

    def test_import_returns_plan(self):
        csv = b"product_name,sku,return_reason,return_date,quantity\nUrn,SKU-1,Beden,2026-01-01,1"
        r = client.post("/api/assistant/import-returns", files={"file": ("r.csv", csv, "text/csv")})
        assert r.status_code in (200, 402)

    def test_presets(self):
        assert client.get("/api/assistant/presets").status_code == 200


class TestWarehouse:
    def setup_method(self):
        _register(f"wh_{_uid()}@x.com")

    def teardown_method(self):
        _logout()

    def _save(self, title: str = "Test") -> str:
        r = client.post("/api/warehouse/items", json={
            "draft": {"title": title, "image_filename": "t.jpg", "sale_price": 199.9},
            "note": "not"
        })
        assert r.status_code == 200
        return r.json()["id"]

    def test_list_empty(self):
        r = client.get("/api/warehouse/items")
        assert r.status_code == 200
        assert r.json()["total"] == 0

    def test_save(self):
        r = client.post("/api/warehouse/items", json={
            "draft": {"title": "Kupa", "image_filename": "k.jpg", "sale_price": 259.9}, "note": ""
        })
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert "id" in r.json()

    def test_list_after_save(self):
        self._save("A"); self._save("B")
        assert client.get("/api/warehouse/items").json()["total"] == 2

    def test_get_item(self):
        iid = self._save()
        r = client.get(f"/api/warehouse/items/{iid}")
        assert r.status_code == 200
        assert r.json()["id"] == iid

    def test_get_nonexistent(self):
        assert client.get("/api/warehouse/items/yokolanid").status_code == 404

    def test_update(self):
        iid = self._save()
        r = client.put(f"/api/warehouse/items/{iid}", json={
            "draft": {"title": "Güncellendi", "image_filename": "g.jpg", "sale_price": 300.0}, "note": ""
        })
        assert r.status_code == 200
        assert client.get(f"/api/warehouse/items/{iid}").json()["title"] == "Güncellendi"

    def test_delete(self):
        iid = self._save()
        assert client.delete(f"/api/warehouse/items/{iid}").json()["ok"] is True
        assert client.get(f"/api/warehouse/items/{iid}").status_code == 404

    def test_invalid_draft(self):
        r = client.post("/api/warehouse/items", json={"draft": {"title": ""}, "note": ""})
        assert r.status_code == 400

    def test_unauthenticated(self):
        _logout()
        assert client.get("/api/warehouse/items").status_code == 401


class TestSupport:
    def test_message_no_mail(self):
        _register(f"sup_{_uid()}@x.com")
        r = client.post("/api/support/message", json={
            "subject": "Yardım lazım",
            "message": "Panelde bir sorun yaşıyorum, yardımcı olur musunuz lütfen?"
        })
        assert r.status_code == 503
        _logout()

    def test_message_unauthenticated(self):
        _logout()
        r = client.post("/api/support/message", json={"subject": "Test", "message": "Bu bir test mesajıdır."})
        assert r.status_code == 401

    def test_message_validation(self):
        _register(f"supv_{_uid()}@x.com")
        r = client.post("/api/support/message", json={"subject": "AB", "message": "kısa"})
        assert r.status_code == 422
        _logout()


class TestMarketplaceClients:
    def test_trendyol_demo_products(self):
        from app.marketplace_api.trendyol_api import TrendyolAPI
        api = TrendyolAPI({})
        assert api.mode == "demo"
        p = api.list_products()
        assert len(p) > 0
        for k in ("sku", "barcode", "title", "price", "stock", "status", "platform", "mode"):
            assert k in p[0]

    def test_trendyol_demo_orders(self):
        from app.marketplace_api.trendyol_api import TrendyolAPI
        orders = TrendyolAPI({}).list_orders()
        assert len(orders) > 0
        for k in ("order_id", "date", "customer", "status", "total", "items", "cargo"):
            assert k in orders[0]

    def test_trendyol_demo_claims(self):
        from app.marketplace_api.trendyol_api import TrendyolAPI
        assert len(TrendyolAPI({}).list_claims()) > 0

    def test_trendyol_demo_questions(self):
        from app.marketplace_api.trendyol_api import TrendyolAPI
        assert len(TrendyolAPI({}).list_questions()) > 0

    def test_trendyol_demo_settlements(self):
        from app.marketplace_api.trendyol_api import TrendyolAPI
        s = TrendyolAPI({}).settlements()
        assert len(s) > 0
        for k in ("period", "gross", "commission", "net"):
            assert k in s[0]

    def test_trendyol_configured(self):
        from app.marketplace_api.trendyol_api import TrendyolAPI
        assert TrendyolAPI({}).configured is False
        assert TrendyolAPI({"seller_id": "1", "api_key": "k", "api_secret": "s"}).configured is True

    def test_hepsiburada_demo(self):
        from app.marketplace_api.hepsiburada_api import HepsiburadaAPI
        api = HepsiburadaAPI({})
        assert api.mode == "demo"
        assert len(api.list_products()) > 0
        assert len(api.list_orders()) > 0
        assert HepsiburadaAPI({"merchant_id": "x", "api_key": "y"}).configured is True

    def test_n11_demo(self):
        from app.marketplace_api.n11_api import N11API
        api = N11API({})
        assert api.mode == "demo"
        assert len(api.list_products()) > 0
        assert N11API({"api_key": "k", "api_secret": "s"}).configured is True

    def test_amazon_demo(self):
        from app.marketplace_api.amazon_api import AmazonAPI
        api = AmazonAPI({})
        assert api.mode == "demo"
        assert len(api.list_orders()) > 0
        assert AmazonAPI({"refresh_token": "r", "lwa_client_id": "c", "lwa_client_secret": "s"}).configured is True

    def test_platform_tag(self):
        from app.marketplace_api.trendyol_api import TrendyolAPI
        for p in TrendyolAPI({}).list_products():
            assert p["platform"] == "trendyol" and p["mode"] == "demo"

    def test_clients_for_user_fallback(self):
        from app.marketplace_api import clients_for_user
        uid = _db.new_id()
        _db.execute(
            "INSERT INTO users (id,email,password_hash,salt,created_at) VALUES (?,?,?,?,?)",
            (uid, f"tmp_{uid}@t.com", "x", "y", _db.now()),
        )
        clients = clients_for_user(uid)
        assert len(clients) == 1
        assert clients[0].platform == "trendyol" and clients[0].mode == "demo"


class TestSecurity:
    def test_protected_require_auth(self):
        _logout()
        for method, path in [
            ("GET", "/api/mp/orders"), ("GET", "/api/mp/products"),
            ("GET", "/api/mp/questions"), ("GET", "/api/mp/claims"),
            ("GET", "/api/mp/finance"), ("GET", "/api/mp/overview"),
            ("GET", "/api/billing/subscription"), ("GET", "/api/connections"),
            ("GET", "/api/warehouse/items"),
        ]:
            r = client.request(method, path)
            assert r.status_code == 401, f"{method} {path} → {r.status_code}"

    def test_public_endpoints(self):
        _logout()
        for method, path in [
            ("GET", "/api/billing/plans"),
            ("GET", "/api/connections/platforms"),
            ("GET", "/api/status"),
        ]:
            assert client.request(method, path).status_code == 200, f"{method} {path}"

    def test_status_fields(self):
        r = client.get("/api/status")
        assert r.status_code == 200
        for k in ("trendyol_mode", "ai_mode", "logged_in", "mail_configured"):
            assert k in r.json()

    def test_sql_injection(self):
        assert client.post("/api/auth/login", json={"email": "' OR '1'='1", "password": "x"}).status_code == 401


class TestAnalytics:
    def setup_method(self):
        _register(f"ana_{_uid()}@x.com")

    def teardown_method(self):
        _logout()

    def test_import_orders(self):
        csv = (
            "order_id,product_name,sku,quantity,unit_price,cost,"
            "commission_rate,order_date,city,status,platform\n"
            "O1,Urun,SKU-1,1,100.0,40.0,0.12,2026-01-01,Istanbul,delivered,trendyol\n"
        ).encode("utf-8")
        r = client.post("/api/analytics/import-orders", files={"file": ("o.csv", csv, "text/csv")})
        assert r.status_code in (200, 402)

    def test_stats_requires_pro(self):
        assert client.get("/api/analytics/stats").status_code == 402
