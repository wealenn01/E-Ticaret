"""Kapsamlı entegrasyon testleri — gerçek HTTP çağrısı gerektirmez.

Çalıştırmak için:
    pip install httpx pytest
    pytest tests/test_api.py -v
"""
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Proje kökünü Python path'ine ekle
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Test için geçici veritabanı kullan
_tmp_dir = tempfile.mkdtemp()
os.environ.setdefault("TRENDYOL_SELLER_ID", "")
os.environ.setdefault("TRENDYOL_API_KEY", "")
os.environ.setdefault("TRENDYOL_API_SECRET", "")
os.environ.setdefault("GEMINI_API_KEY", "")


# Geçici data dizini kullan (gerçek DB'ye dokunmama)
import app.db as _db_mod
_db_mod.DB_PATH = Path(_tmp_dir) / "test.db"
_db_mod.DATA_DIR = Path(_tmp_dir)
_db_mod.DATA_DIR.mkdir(exist_ok=True)
_db_mod.init_db()

from app.main import app

client = TestClient(app, raise_server_exceptions=True)


# -----------------------------------------------------------------------
# Yardımcılar
# -----------------------------------------------------------------------

def _uid() -> str:
    """Her çağrıda benzersiz kısa ID üretir."""
    return uuid.uuid4().hex[:8]


def _register(email: str, password: str = "Test1234!", name: str = "Test") -> dict:
    r = client.post("/api/auth/register", json={
        "email": email, "password": password, "full_name": name
    })
    assert r.status_code == 200, r.text
    return r.json()


def _login(email: str, password: str = "Test1234!") -> None:
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text


def _logout() -> None:
    client.post("/api/auth/logout")


# -----------------------------------------------------------------------
# Kimlik doğrulama testleri
# -----------------------------------------------------------------------

class TestAuth:
    def test_register_success(self):
        r = client.post("/api/auth/register", json={
            "email": "auth_test@example.com",
            "password": "Guvenli123!",
            "full_name": "Ali Yılmaz",
            "company": "Test Şirketi",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["email"] == "auth_test@example.com"
        assert data["plan"] == "free"
        assert "id" in data

    def test_register_duplicate_email(self):
        client.post("/api/auth/register", json={
            "email": "duplicate@example.com", "password": "Test1234!"
        })
        r = client.post("/api/auth/register", json={
            "email": "duplicate@example.com", "password": "Baska123!"
        })
        assert r.status_code == 409

    def test_register_invalid_email(self):
        r = client.post("/api/auth/register", json={
            "email": "gecersiz-email", "password": "Test1234!"
        })
        assert r.status_code == 400

    def test_register_short_password(self):
        r = client.post("/api/auth/register", json={
            "email": "short_pw@example.com", "password": "abc"
        })
        assert r.status_code == 400

    def test_login_success(self):
        client.post("/api/auth/register", json={
            "email": "login_ok@example.com", "password": "Correct123!"
        })
        r = client.post("/api/auth/login", json={
            "email": "login_ok@example.com", "password": "Correct123!"
        })
        assert r.status_code == 200
        assert r.json()["email"] == "login_ok@example.com"

    def test_login_wrong_password(self):
        client.post("/api/auth/register", json={
            "email": "login_bad@example.com", "password": "Correct123!"
        })
        r = client.post("/api/auth/login", json={
            "email": "login_bad@example.com", "password": "YanlisParola"
        })
        assert r.status_code == 401

    def test_login_nonexistent_user(self):
        r = client.post("/api/auth/login", json={
            "email": "yok@example.com", "password": "Test1234!"
        })
        assert r.status_code == 401

    def test_me_authenticated(self):
        _register("me_test@example.com")
        r = client.get("/api/auth/me")
        assert r.status_code == 200
        assert r.json()["email"] == "me_test@example.com"
        _logout()

    def test_me_unauthenticated(self):
        _logout()
        r = client.get("/api/auth/me")
        assert r.status_code == 401

    def test_logout(self):
        _register("logout_test@example.com")
        r = client.post("/api/auth/logout")
        assert r.status_code == 200
        r2 = client.get("/api/auth/me")
        assert r2.status_code == 401

    def test_demo_account_creation(self):
        """Demo hesap oluşturma — ayrı endpoint, örnek verilerle dolu."""
        r = client.post("/api/auth/demo")
        assert r.status_code == 200
        data = r.json()
        assert data.get("demo") is True
        assert "demo_email" in data
        assert data["demo_email"].startswith("demo_")
        assert "demo_password" in data
        assert data["plan"] == "free"
        _logout()

    def test_demo_data_seeded(self):
        """Demo hesapta örnek yorum verileri yüklenmiş olmalı."""
        r = client.post("/api/auth/demo")
        assert r.status_code == 200
        # Demo verilerini kontrol et
        stats = client.get("/api/assistant/stats")
        assert stats.status_code == 200
        data = stats.json()
        # Örnek CSV'deki yorumlar yüklenmiş olmalı
        has_reviews = (
            data.get("negative_reviews", 0) > 0
            or data.get("total_reviews", 0) > 0
            or bool(data.get("complaint_breakdown"))
            or data.get("total_products", 0) > 0
        )
        assert has_reviews, f"Demo verisi yüklenmemiş: {data}"
        _logout()


# -----------------------------------------------------------------------
# Bağlantı (connections) testleri
# -----------------------------------------------------------------------

class TestConnections:
    def setup_method(self):
        self._email = f"conn_{_uid()}@example.com"
        _register(self._email)

    def teardown_method(self):
        _logout()

    def test_list_platforms(self):
        """Platform listesi oturum gerektirmemeli."""
        _logout()
        r = client.get("/api/connections/platforms")
        assert r.status_code == 200
        platforms = r.json()
        keys = {p["key"] for p in platforms}
        assert "trendyol" in keys
        assert "hepsiburada" in keys
        assert "n11" in keys
        assert "amazon" in keys

    def test_add_trendyol_connection_no_keys(self):
        """Anahtarsız Trendyol bağlantısı unverified kaydedilmeli."""
        r = client.post("/api/connections", json={
            "platform": "trendyol",
            "label": "Test Mağaza",
            "credentials": {"seller_id": "", "api_key": "", "api_secret": ""},
        })
        assert r.status_code == 200
        data = r.json()
        assert data["connection"]["status"] == "unverified"

    def test_add_hepsiburada_no_keys(self):
        """Anahtarsız HB bağlantısı unverified kaydedilmeli."""
        r = client.post("/api/connections", json={
            "platform": "hepsiburada",
            "label": "HB Mağaza",
            "credentials": {"merchant_id": "", "api_key": ""},
        })
        assert r.status_code == 200
        assert r.json()["connection"]["status"] == "unverified"

    def test_add_n11_no_keys(self):
        r = client.post("/api/connections", json={
            "platform": "n11",
            "label": "N11 Mağaza",
            "credentials": {"api_key": "", "api_secret": ""},
        })
        assert r.status_code == 200
        assert r.json()["connection"]["status"] == "unverified"

    def test_add_amazon_no_keys(self):
        r = client.post("/api/connections", json={
            "platform": "amazon",
            "label": "Amazon TR",
            "credentials": {"seller_id": "", "refresh_token": ""},
        })
        assert r.status_code == 200
        assert r.json()["connection"]["status"] == "unverified"

    def test_duplicate_platform_rejected(self):
        """Aynı platform iki kez eklenemez."""
        client.post("/api/connections", json={
            "platform": "hepsiburada", "credentials": {}
        })
        r = client.post("/api/connections", json={
            "platform": "hepsiburada", "credentials": {}
        })
        assert r.status_code == 409

    def test_list_my_connections(self):
        client.post("/api/connections", json={"platform": "n11", "credentials": {}})
        r = client.get("/api/connections")
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        assert len(r.json()) >= 1

    def test_unknown_platform_rejected(self):
        r = client.post("/api/connections", json={
            "platform": "bilinmeyen_platform", "credentials": {}
        })
        assert r.status_code == 400

    def test_credentials_masked_in_response(self):
        """Secret alanlar ••••XXXX formatında döndürülmeli."""
        r = client.post("/api/connections", json={
            "platform": "hepsiburada",
            "label": "Mask Test",
            "credentials": {"merchant_id": "12345", "api_key": "gizli_sifre_1234"},
        })
        assert r.status_code == 200
        creds = r.json()["connection"]["credentials"]
        assert creds.get("api_key", "").startswith("••••")

    def test_connection_unauthenticated(self):
        _logout()
        r = client.get("/api/connections")
        assert r.status_code == 401


# -----------------------------------------------------------------------
# Operasyon (ops) testleri — demo mod
# -----------------------------------------------------------------------

class TestOps:
    def setup_method(self):
        self._email = f"ops_{_uid()}@example.com"
        _register(self._email)

    def teardown_method(self):
        _logout()

    def test_overview_returns_data(self):
        r = client.get("/api/mp/overview")
        assert r.status_code == 200
        data = r.json()
        assert "orders_total" in data
        assert "waiting_questions" in data
        assert "low_stock_count" in data
        assert "platforms" in data

    def test_orders_list(self):
        r = client.get("/api/mp/orders")
        assert r.status_code == 200
        data = r.json()
        assert "rows" in data
        assert isinstance(data["rows"], list)
        if data["rows"]:
            order = data["rows"][0]
            for key in ("order_id", "date", "status", "total", "items"):
                assert key in order, f"'{key}' alanı eksik"

    def test_orders_status_filter(self):
        r = client.get("/api/mp/orders?status=Delivered")
        assert r.status_code == 200
        for order in r.json().get("rows", []):
            assert order["status"] == "Delivered"

    def test_products_list(self):
        r = client.get("/api/mp/products")
        assert r.status_code == 200
        data = r.json()
        assert "rows" in data
        assert "threshold" in data
        if data["rows"]:
            p = data["rows"][0]
            for key in ("sku", "title", "price", "stock", "status", "low_stock"):
                assert key in p

    def test_questions_list(self):
        r = client.get("/api/mp/questions")
        assert r.status_code == 200
        data = r.json()
        assert "rows" in data
        if data["rows"]:
            q = data["rows"][0]
            for key in ("question_id", "product", "question", "status"):
                assert key in q

    def test_claims_list(self):
        r = client.get("/api/mp/claims")
        assert r.status_code == 200
        data = r.json()
        assert "rows" in data

    def test_settings_get_default(self):
        r = client.get("/api/mp/settings")
        assert r.status_code == 200
        assert r.json().get("low_stock_threshold") == 5

    def test_settings_save(self):
        r = client.post("/api/mp/settings", json={"low_stock_threshold": 10})
        assert r.status_code == 200
        assert r.json()["low_stock_threshold"] == 10
        # Doğrulama
        r2 = client.get("/api/mp/settings")
        assert r2.json()["low_stock_threshold"] == 10

    def test_price_stock_update_demo(self):
        r = client.post("/api/mp/price-stock", json={
            "platform": "trendyol",
            "items": [{"sku": "SKU-KUPA-01", "price": 299.90, "stock": 50}],
        })
        assert r.status_code == 200
        data = r.json()
        assert data.get("ok") is True

    def test_price_stock_empty_items(self):
        r = client.post("/api/mp/price-stock", json={
            "platform": "trendyol", "items": []
        })
        assert r.status_code == 400

    def test_ops_unauthenticated(self):
        _logout()
        r = client.get("/api/mp/orders")
        assert r.status_code == 401

    def test_platform_filter(self):
        r = client.get("/api/mp/orders?platform=trendyol")
        assert r.status_code == 200

    def test_orders_sorted_by_date_desc(self):
        r = client.get("/api/mp/orders")
        rows = r.json().get("rows", [])
        if len(rows) > 1:
            dates = [row["date"] for row in rows if row["date"]]
            assert dates == sorted(dates, reverse=True)


# -----------------------------------------------------------------------
# Fatura / plan limiti testleri
# -----------------------------------------------------------------------

class TestBilling:
    def setup_method(self):
        self._email = f"billing_{_uid()}@example.com"
        _register(self._email)

    def teardown_method(self):
        _logout()

    def test_plans_public(self):
        _logout()
        r = client.get("/api/billing/plans")
        assert r.status_code == 200
        data = r.json()
        # Response: {"provider": ..., "plans": [...]}
        plans_list = data.get("plans", data) if isinstance(data, dict) else data
        plan_keys = {p["key"] for p in plans_list}
        assert {"free", "starter", "pro", "enterprise"} == plan_keys

    def test_free_plan_limits(self):
        """Ücretsiz plan limitlerinin doğru yapılandırıldığını doğrula."""
        _logout()
        r = client.get("/api/billing/plans")
        data = r.json()
        plans_list = data.get("plans", data) if isinstance(data, dict) else data
        plans = {p["key"]: p for p in plans_list}
        free = plans["free"]
        assert free["price"] == 0
        assert free["limits"]["ai_listing"] == 3
        assert free["limits"]["stores"] == 1

    def test_usage_summary(self):
        r = client.get("/api/billing/subscription")
        assert r.status_code == 200
        data = r.json()
        assert "usage" in data
        assert "limits" in data

    def test_finance_requires_plan(self):
        """Ücretsiz planda finans modülü 402 döndürmeli."""
        r = client.get("/api/mp/finance")
        assert r.status_code == 402


# -----------------------------------------------------------------------
# Mağaza asistanı testleri
# -----------------------------------------------------------------------

class TestAssistant:
    def setup_method(self):
        self._email = f"assistant_{_uid()}@example.com"
        _register(self._email)

    def teardown_method(self):
        _logout()

    def test_import_reviews_csv(self):
        csv_content = (
            "product_name,sku,rating,comment_text,review_date\n"
            "Test Ürün,SKU-001,5,Harika ürün çok memnun kaldım,2026-01-01\n"
            "Test Ürün,SKU-001,1,Kargo geç geldi kötü paketleme,2026-01-02\n"
            "Test Ürün 2,SKU-002,4,Kaliteli ürün tavsiye ederim,2026-01-03\n"
        ).encode()
        r = client.post(
            "/api/assistant/import-reviews",
            files={"file": ("reviews.csv", csv_content, "text/csv")},
        )
        assert r.status_code == 200
        data = r.json()
        assert data.get("total", 0) == 3

    def test_stats_after_import(self):
        # Yorum yükle
        csv_content = (
            "product_name,sku,rating,comment_text,review_date\n"
            "Ürün A,SKU-A,5,Güzel,2026-01-01\n"
            "Ürün A,SKU-A,2,Kötü kargo,2026-01-02\n"
        ).encode()
        client.post(
            "/api/assistant/import-reviews",
            files={"file": ("r.csv", csv_content, "text/csv")},
        )
        r = client.get("/api/assistant/stats")
        assert r.status_code == 200
        data = r.json()
        # Herhangi bir yorum verisi olmalı
        has_data = (
            data.get("negative_reviews", 0) > 0
            or data.get("total_reviews", 0) > 0
            or data.get("total_products", 0) > 0
            or bool(data.get("complaint_breakdown"))
        )
        assert has_data, f"Yorum stats boş: {data}"

    def test_assistant_settings_get(self):
        r = client.get("/api/assistant/settings")
        assert r.status_code == 200

    def test_assistant_settings_update(self):
        r = client.post("/api/assistant/settings", json={
            "platform": "trendyol", "seller_model": "manufacturer"
        })
        assert r.status_code == 200

    def test_import_returns_csv(self):
        csv_content = (
            "product_name,sku,return_reason,return_date,quantity\n"
            "Test Ürün,SKU-001,Beden uymadı,2026-01-01,1\n"
            "Test Ürün,SKU-001,Kalite sorunu,2026-01-02,2\n"
        ).encode()
        r = client.post(
            "/api/assistant/import-returns",
            files={"file": ("returns.csv", csv_content, "text/csv")},
        )
        # Free planda returns özelliği kapalı (402) veya Pro'da 200
        assert r.status_code in (200, 402)

    def test_import_returns_requires_plan(self):
        """Returns plan gerektiriyor — free planda 402 almalı."""
        # Returns free planda kapalı; önce stats endpoint üzerinden doğrulayalım
        r = client.get("/api/assistant/returns-insights")
        assert r.status_code == 402

    def test_delete_reviews(self):
        r = client.delete("/api/assistant/reviews")
        assert r.status_code == 200
        assert r.json().get("ok") is True

    def test_ask_no_gemini(self):
        """Gemini yokken ask 503 dönebilir ama 500 dönmemeli."""
        r = client.post("/api/assistant/ask", json={"question": "En çok hangi ürün iade ediliyor?"})
        assert r.status_code in (200, 503)

    def test_ask_empty_question(self):
        r = client.post("/api/assistant/ask", json={"question": ""})
        assert r.status_code == 400


# -----------------------------------------------------------------------
# Marketplace API istemci birim testleri (demo mod)
# -----------------------------------------------------------------------

class TestMarketplaceClients:
    """Gerçek API çağrısı yapmadan istemci davranışını test eder."""

    def test_trendyol_demo_products(self):
        from app.marketplace_api.trendyol_api import TrendyolAPI
        api = TrendyolAPI({})
        assert api.mode == "demo"
        products = api.list_products()
        assert len(products) > 0
        p = products[0]
        for key in ("sku", "barcode", "title", "price", "stock", "status", "platform"):
            assert key in p, f"'{key}' eksik"

    def test_trendyol_demo_orders(self):
        from app.marketplace_api.trendyol_api import TrendyolAPI
        api = TrendyolAPI({})
        orders = api.list_orders()
        assert len(orders) > 0
        o = orders[0]
        for key in ("order_id", "date", "customer", "status", "total", "items", "cargo"):
            assert key in o

    def test_trendyol_demo_claims(self):
        from app.marketplace_api.trendyol_api import TrendyolAPI
        api = TrendyolAPI({})
        claims = api.list_claims()
        assert len(claims) > 0

    def test_trendyol_demo_questions(self):
        from app.marketplace_api.trendyol_api import TrendyolAPI
        api = TrendyolAPI({})
        questions = api.list_questions()
        assert len(questions) > 0

    def test_trendyol_demo_settlements(self):
        from app.marketplace_api.trendyol_api import TrendyolAPI
        api = TrendyolAPI({})
        settlements = api.settlements()
        assert len(settlements) > 0
        s = settlements[0]
        for key in ("period", "gross", "commission", "net"):
            assert key in s

    def test_trendyol_demo_price_stock(self):
        from app.marketplace_api.trendyol_api import TrendyolAPI
        api = TrendyolAPI({})
        result = api.update_price_stock([{"sku": "SKU-001", "price": 100.0, "stock": 5}])
        assert result.get("ok") is True
        assert result.get("mode") == "demo"

    def test_trendyol_configured_check(self):
        from app.marketplace_api.trendyol_api import TrendyolAPI
        assert TrendyolAPI({}).configured is False
        assert TrendyolAPI({"seller_id": "123", "api_key": "k", "api_secret": "s"}).configured is True

    def test_hepsiburada_demo_products(self):
        from app.marketplace_api.hepsiburada_api import HepsiburadaAPI
        api = HepsiburadaAPI({})
        assert api.mode == "demo"
        products = api.list_products()
        assert len(products) > 0

    def test_hepsiburada_demo_orders(self):
        from app.marketplace_api.hepsiburada_api import HepsiburadaAPI
        api = HepsiburadaAPI({})
        orders = api.list_orders()
        assert len(orders) > 0

    def test_hepsiburada_configured_check(self):
        from app.marketplace_api.hepsiburada_api import HepsiburadaAPI
        assert HepsiburadaAPI({}).configured is False
        assert HepsiburadaAPI({"merchant_id": "123", "api_key": "x"}).configured is True

    def test_n11_demo_products(self):
        from app.marketplace_api.n11_api import N11API
        api = N11API({})
        assert api.mode == "demo"
        products = api.list_products()
        assert len(products) > 0

    def test_n11_demo_orders(self):
        from app.marketplace_api.n11_api import N11API
        api = N11API({})
        orders = api.list_orders()
        assert len(orders) > 0

    def test_n11_configured_check(self):
        from app.marketplace_api.n11_api import N11API
        assert N11API({}).configured is False
        assert N11API({"api_key": "k", "api_secret": "s"}).configured is True

    def test_amazon_demo_orders(self):
        from app.marketplace_api.amazon_api import AmazonAPI
        api = AmazonAPI({})
        assert api.mode == "demo"
        orders = api.list_orders()
        assert len(orders) > 0

    def test_amazon_configured_check(self):
        from app.marketplace_api.amazon_api import AmazonAPI
        assert AmazonAPI({}).configured is False
        assert AmazonAPI({
            "refresh_token": "rt", "lwa_client_id": "ci", "lwa_client_secret": "cs"
        }).configured is True

    def test_all_clients_tag_platform(self):
        """_tag() her satıra platform ve mode eklemeli."""
        from app.marketplace_api.trendyol_api import TrendyolAPI
        api = TrendyolAPI({})
        products = api.list_products()
        for p in products:
            assert p["platform"] == "trendyol"
            assert p["mode"] == "demo"

    def test_clients_for_user_fallback(self):
        """Bağlantısı olmayan kullanıcı için demo TrendyolAPI döner."""
        from app.marketplace_api import clients_for_user
        # Geçici test kullanıcısı
        import app.db as db
        import uuid
        uid = uuid.uuid4().hex
        db.execute(
            "INSERT INTO users (id, email, password_hash, salt, created_at) VALUES (?,?,?,?,?)",
            (uid, f"tmp_{uid}@t.com", "x", "y", db.now()),
        )
        clients = clients_for_user(uid)
        assert len(clients) == 1
        assert clients[0].platform == "trendyol"
        assert clients[0].mode == "demo"


# -----------------------------------------------------------------------
# Güvenlik testleri
# -----------------------------------------------------------------------

class TestSecurity:
    def test_session_cookie_httponly(self):
        """Session cookie httpOnly olmalı."""
        r = client.post("/api/auth/register", json={
            "email": "sec_test@example.com", "password": "Secure123!"
        })
        assert r.status_code == 200
        cookies = r.cookies
        # TestClient üzerinden direkt header kontrolü
        set_cookie = r.headers.get("set-cookie", "")
        assert "httponly" in set_cookie.lower()
        _logout()

    def test_protected_endpoints_require_auth(self):
        _logout()
        protected = [
            ("/api/mp/orders", "GET"),
            ("/api/mp/products", "GET"),
            ("/api/mp/questions", "GET"),
            ("/api/mp/claims", "GET"),
            ("/api/mp/finance", "GET"),
            ("/api/mp/overview", "GET"),
            ("/api/billing/usage", "GET"),
            ("/api/connections", "GET"),
        ]
        for path, method in protected:
            r = client.request(method, path)
            assert r.status_code == 401, f"{method} {path} → {r.status_code} (401 beklendi)"

    def test_public_endpoints_accessible(self):
        """Bu endpoint'ler giriş gerektirmemeli."""
        _logout()
        public = [
            ("/api/billing/plans", "GET"),
            ("/api/connections/platforms", "GET"),
            ("/api/status", "GET"),
        ]
        for path, method in public:
            r = client.request(method, path)
            assert r.status_code == 200, f"{method} {path} → {r.status_code}"

    def test_other_user_connection_not_accessible(self):
        """Başka kullanıcının bağlantısı düzenlenememeli."""
        _register("user_a@example.com")
        r_conn = client.post("/api/connections", json={
            "platform": "n11", "credentials": {}
        })
        conn_id = r_conn.json()["connection"]["id"]
        _logout()

        _register("user_b@example.com")
        # user_b, user_a'nın bağlantısını düzenlemeye çalışıyor
        r = client.put(f"/api/connections/{conn_id}", json={
            "platform": "n11", "credentials": {}
        })
        assert r.status_code == 404
        _logout()

    def test_sql_injection_email(self):
        """SQL injection email'de hata değil, reddetmeli."""
        r = client.post("/api/auth/login", json={
            "email": "' OR '1'='1", "password": "x"
        })
        assert r.status_code == 401

    def test_status_endpoint(self):
        r = client.get("/api/status")
        assert r.status_code == 200
        data = r.json()
        assert "ai_mode" in data
        assert "logged_in" in data


# -----------------------------------------------------------------------
# Analitik testleri
# -----------------------------------------------------------------------

class TestAnalytics:
    def setup_method(self):
        self._email = f"analytics_{_uid()}@example.com"
        _register(self._email)

    def teardown_method(self):
        _logout()

    def test_import_orders_csv(self):
        """Analitik sipariş CSV import kontrolü."""
        csv_content = (
            "order_id,product_name,sku,quantity,unit_price,cost,commission_rate,"
            "order_date,city,status,platform\n"
            "ORD-001,Test Ürün,SKU-001,2,199.90,80.00,0.12,2026-01-01,İstanbul,delivered,trendyol\n"
            "ORD-002,Test Ürün 2,SKU-002,1,499.90,200.00,0.12,2026-01-02,Ankara,delivered,hepsiburada\n"
        ).encode()
        r = client.post(
            "/api/analytics/import-orders",
            files={"file": ("orders.csv", csv_content, "text/csv")},
        )
        # Pro plan gerekiyor (free → 402) veya 200
        assert r.status_code in (200, 402)

    def test_analytics_requires_pro(self):
        r = client.get("/api/analytics/stats")
        assert r.status_code == 402
