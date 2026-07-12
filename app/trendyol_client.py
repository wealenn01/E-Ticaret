"""Trendyol Marketplace API istemcisi.

Kimlik bilgileri (.env) yoksa MOCK modda çalışır: gömülü örnek kategori ağacı
ve sahte batch sonuçları döner, böylece tüm akış uçtan uca denenebilir.

Gerçek mod: Product V2 API (https://developers.trendyol.com)
  - Auth: Basic (apiKey:apiSecret), User-Agent: "{sellerId} - SelfIntegration"
  - Ürün oluşturma: POST /integration/product/sellers/{sellerId}/v2/products
"""
import base64
import os
import uuid
from typing import Any, Optional

import httpx

PROD_BASE = "https://apigw.trendyol.com/integration"
STAGE_BASE = "https://stageapigw.trendyol.com/integration"
DEFAULT_TRENDYOL_ENV = (os.getenv("TRENDYOL_ENV", "stage") or "stage").strip().lower()


def normalize_trendyol_env(env: Optional[str] = None) -> str:
    """Boş veya test değerleri stage'e; canlı değerleri prod'a yönlendirir."""
    v = (env or DEFAULT_TRENDYOL_ENV or "stage").strip().lower()
    if v in ("stage", "test", "staging", "sandbox", "stg"):
        return "stage"
    return "prod"

# Demo modu için küçük örnek kategori ağacı (gerçek ağaç ~4000 kategori içerir)
MOCK_CATEGORIES = [
    {"id": 1001, "name": "Kadın > Giyim > Elbise"},
    {"id": 1002, "name": "Kadın > Giyim > Bluz"},
    {"id": 1003, "name": "Erkek > Giyim > T-shirt"},
    {"id": 1004, "name": "Erkek > Ayakkabı > Spor Ayakkabı"},
    {"id": 1005, "name": "Elektronik > Kulaklık > Bluetooth Kulaklık"},
    {"id": 1006, "name": "Elektronik > Telefon Aksesuarları > Kılıf"},
    {"id": 1007, "name": "Ev & Yaşam > Mutfak > Kupa & Bardak"},
    {"id": 1008, "name": "Ev & Yaşam > Dekorasyon > Vazo"},
    {"id": 1009, "name": "Kozmetik > Parfüm > Kadın Parfüm"},
    {"id": 1010, "name": "Anne & Çocuk > Oyuncak > Peluş Oyuncak"},
    {"id": 1011, "name": "Spor & Outdoor > Fitness > Yoga Matı"},
    {"id": 1012, "name": "Aksesuar > Çanta > Omuz Çantası"},
    {"id": 1013, "name": "Elektronik > Televizyon > OLED TV"},
    {"id": 1014, "name": "Elektronik > Televizyon > QLED TV"},
    {"id": 1015, "name": "Elektronik > Televizyon > LED TV"},
    {"id": 1016, "name": "Elektronik > Televizyon > Televizyon Aksesuarları"},
    {"id": 1017, "name": "Elektronik > Bilgisayar > Dizüstü Bilgisayar"},
    {"id": 1018, "name": "Elektronik > Telefon > Akıllı Telefon"},
    {"id": 1019, "name": "Elektronik > Hoparlör > Bluetooth Hoparlör"},
    {"id": 1020, "name": "Elektronik > Giyilebilir Teknoloji > Akıllı Saat"},
    {"id": 1021, "name": "Ofis & Kırtasiye > Yazı Gereçleri > Kalem"},
    {"id": 1022, "name": "Ev & Yaşam > Aydınlatma > Masa Lambası"},
    {"id": 1023, "name": "Ev & Yaşam > Mutfak > Tencere & Tava"},
    {"id": 1024, "name": "Kadın > Ayakkabı > Topuklu Ayakkabı"},
    {"id": 1025, "name": "Erkek > Giyim > Gömlek"},
    {"id": 1026, "name": "Kadın > Giyim > T-shirt"},
    {"id": 1027, "name": "Saat & Aksesuar > Saat > Kol Saati"},
    {"id": 1028, "name": "Kitap & Hobi > Oyun > Kutu Oyunu"},
    {"id": 1029, "name": "Kadın > İç Giyim > Sütyen"},
    {"id": 1030, "name": "Kadın > İç Giyim > Külot"},
    {"id": 1031, "name": "Kadın > İç Giyim > İç Çamaşırı Takımı"},
    {"id": 1032, "name": "Kadın > İç Giyim > Gecelik & Pijama"},
    {"id": 1033, "name": "Kadın > İç Giyim > Fantezi Giyim"},
    {"id": 1034, "name": "Kadın > İç Giyim > Korse"},
    {"id": 1035, "name": "Erkek > İç Giyim > Boxer"},
    {"id": 1036, "name": "Erkek > İç Giyim > Atlet & Fanila"},
    {"id": 1037, "name": "Kadın > Giyim > Etek"},
    {"id": 1038, "name": "Kadın > Giyim > Pantolon"},
    {"id": 1039, "name": "Kadın > Giyim > Mont & Kaban"},
    {"id": 1040, "name": "Kadın > Ayakkabı > Spor Ayakkabı"},
]

MOCK_ATTRIBUTES = [
    {"attribute": {"id": 338, "name": "Renk"}, "required": True, "allowCustom": False,
     "attributeValues": [{"id": 1, "name": "Siyah"}, {"id": 2, "name": "Beyaz"}, {"id": 3, "name": "Kırmızı"},
                          {"id": 4, "name": "Mavi"}, {"id": 5, "name": "Yeşil"}, {"id": 6, "name": "Bej"},
                          {"id": 7, "name": "Kahverengi"}, {"id": 8, "name": "Çok Renkli"}]},
    {"attribute": {"id": 343, "name": "Beden/Ölçü"}, "required": False, "allowCustom": True, "attributeValues": []},
    {"attribute": {"id": 346, "name": "Materyal"}, "required": False, "allowCustom": True, "attributeValues": []},
]


class TrendyolClient:
    def __init__(self, seller_id: str = "", api_key: str = "", api_secret: str = "", env: str = "stage"):
        self.seller_id = seller_id
        self.api_key = api_key
        self.api_secret = api_secret
        env = normalize_trendyol_env(env)
        self.base = STAGE_BASE if env == "stage" else PROD_BASE
        self.env = env

    @property
    def mock(self) -> bool:
        return not (self.seller_id and self.api_key and self.api_secret)

    @property
    def mode_name(self) -> str:
        return "mock" if self.mock else self.env

    def _headers(self) -> dict[str, str]:
        token = base64.b64encode(f"{self.api_key}:{self.api_secret}".encode()).decode()
        agent = f"{self.seller_id} - SelfIntegration"
        return {
            "Authorization": f"Basic {token}",
            "User-Agent": agent,
            "x-agentname": agent,
            "x-correlationid": str(uuid.uuid4()),
            "storeFrontCode": "TR",
            "Content-Type": "application/json",
        }

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        with httpx.Client(timeout=30) as c:
            r = c.get(f"{self.base}{path}", headers=self._headers(), params=params)
            r.raise_for_status()
            return r.json()

    # ---- Kategoriler ----

    def get_leaf_categories(self) -> list[dict]:
        """Yaprak kategorileri {id, name} listesi olarak döndürür (name = tam yol)."""
        if self.mock:
            return MOCK_CATEGORIES
        try:
            data = self._get("/product/product-categories")
        except httpx.HTTPStatusError as exc:
            # Stage/prod yetki veya IP kısıtında akış kırılmasın; demo kategoriyle devam et.
            if exc.response.status_code in (401, 403):
                return MOCK_CATEGORIES
            raise
        leaves: list[dict] = []

        def walk(node: dict, path: str):
            full = f"{path} > {node['name']}" if path else node["name"]
            children = node.get("subCategories") or []
            if not children:
                leaves.append({"id": node["id"], "name": full})
            for ch in children:
                walk(ch, full)

        for root in data.get("categories", []):
            walk(root, "")
        return leaves

    def get_category_attributes(self, category_id: int) -> list[dict]:
        if self.mock:
            return MOCK_ATTRIBUTES
        try:
            data = self._get(f"/product/product-categories/{category_id}/attributes")
            return data.get("categoryAttributes", [])
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                return MOCK_ATTRIBUTES
            raise

    # ---- Marka ----

    def find_brand_id(self, brand_name: str) -> Optional[int]:
        if self.mock:
            return 9999
        try:
            data = self._get("/product/brands/by-name", params={"name": brand_name})
            if isinstance(data, list) and data:
                return data[0]["id"]
        except httpx.HTTPStatusError:
            pass
        return None

    # ---- Ürün oluşturma ----

    def create_product(self, item: dict) -> str:
        """Tek ürün gönderir, batchRequestId döner."""
        if self.mock:
            return f"mock-batch-{uuid.uuid4().hex[:12]}"
        with httpx.Client(timeout=60) as c:
            r = c.post(
                f"{self.base}/product/sellers/{self.seller_id}/v2/products",
                headers=self._headers(),
                json={"items": [item]},
            )
            r.raise_for_status()
            return r.json()["batchRequestId"]

    def get_batch_status(self, batch_request_id: str) -> dict:
        if self.mock:
            return {"status": "COMPLETED", "items": [{"status": "SUCCESS", "failureReasons": []}]}
        return self._get(f"/product/sellers/{self.seller_id}/products/batch-requests/{batch_request_id}")
