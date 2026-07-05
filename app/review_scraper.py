from __future__ import annotations
import asyncio
import json
import logging
import re
from datetime import date
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

MAX_REVIEWS = 100
FETCH_TIMEOUT = 30.0
ALLOWED_HOSTS = frozenset(
    {
        "www.hepsiburada.com",
        "hepsiburada.com",
        "www.trendyol.com",
        "trendyol.com",
        "www.n11.com",
        "n11.com",
        "www.amazon.com.tr",
        "amazon.com.tr",
    }
)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_HERMES_REVIEWS_API = (
    "https://user-content-gw-hermes.hepsiburada.com/queryapi/v2/ApprovedUserContents"
)
_TRENDYOL_REVIEWS_API = (
    "https://apigw.trendyol.com/discovery-web-websfxsocialreviewrating-santral/product-reviews-detailed"
)
_CURL_IMPERSONATE = "chrome131"
_TR_MONTHS = {
    "ocak": 1,
    "subat": 2,
    "şubat": 2,
    "mart": 3,
    "nisan": 4,
    "mayis": 5,
    "mayıs": 5,
    "haziran": 6,
    "temmuz": 7,
    "agustos": 8,
    "ağustos": 8,
    "eylul": 9,
    "eylül": 9,
    "ekim": 10,
    "kasim": 11,
    "kasım": 11,
    "aralik": 12,
    "aralık": 12,
}


def _platform_label(platform: str) -> str:
    if platform == "trendyol":
        return "Trendyol"
    if platform == "n11":
        return "N11"
    if platform == "amazon":
        return "Amazon"
    return "Hepsiburada"


def _browser_headers(
    platform: str = "hepsiburada",
    *,
    accept: str | None = None,
    referer: str | None = None,
) -> dict[str, str]:
    base_referer = {
        "trendyol": "https://www.trendyol.com/",
        "n11": "https://www.n11.com/",
        "amazon": "https://www.amazon.com.tr/",
    }.get(platform, "https://www.hepsiburada.com/")
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": accept
        or "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": referer or base_referer,
        "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Upgrade-Insecure-Requests": "1",
    }
    if platform == "trendyol" and accept and "json" in accept:
        headers.update(
            {
                "Origin": "https://www.trendyol.com",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-site",
            }
        )
    else:
        headers.update(
            {
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-origin",
            }
        )
    return headers


def validate_review_page_url(url: str) -> str:
    raw = url.strip()
    if not raw.startswith(("http://", "https://")):
        raise ValueError("Geçerli bir link girin (https:// ile başlamalı)")
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise ValueError(
            "Şimdilik Hepsiburada, Trendyol, N11 ve Amazon.com.tr linkleri destekleniyor "
            "(hepsiburada.com / trendyol.com / n11.com / amazon.com.tr)"
        )
    if not parsed.path or parsed.path == "/":
        raise ValueError("Ürün veya yorum sayfası linki girin")
    return raw


def detect_platform(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "hepsiburada" in host:
        return "hepsiburada"
    if "trendyol" in host:
        return "trendyol"
    if "n11" in host:
        return "n11"
    if "amazon.com.tr" in host:
        return "amazon"
    return "unknown"


def _sku_from_url(url: str, platform: str) -> str:
    if platform == "hepsiburada":
        m = re.search(r"-p-([A-Z0-9]+)", url, re.I)
        if m:
            return m.group(1).upper()
        m = re.search(r"-pm-([A-Za-z0-9]+)", url, re.I)
        if m:
            return m.group(1).upper()
        m = re.search(r"(HB[A-Z0-9]{6,})", url, re.I)
        if m:
            return m.group(1).upper()
    if platform == "trendyol":
        content_id = _trendyol_content_id(url)
        if content_id:
            return f"TY-{content_id}"
    if platform == "n11":
        product_id = _n11_review_product_id_from_url(url)
        if product_id:
            return f"N11-{product_id}"
    if platform == "amazon":
        asin = _amazon_asin_from_url(url)
        if asin:
            return f"AMZ-{asin}"
    return f"SCRAPE-{abs(hash(url)) % 10_000_000}"


def _trendyol_content_id(url: str) -> str | None:
    m = re.search(r"-p-(\d+)", url)
    return m.group(1) if m else None


def _amazon_asin_from_url(url: str) -> str | None:
    match = re.search(r"/(?:dp|gp/product|product-reviews)/([A-Z0-9]{10})", url, re.I)
    return match.group(1).upper() if match else None


def _amazon_product_url(asin: str) -> str:
    return f"https://www.amazon.com.tr/dp/{asin}"


def _parse_tr_number(value: str) -> int:
    digits = re.sub(r"[^\d]", "", value or "")
    return int(digits) if digits else 0


def _amazon_total_reviews_from_html(html: str) -> int:
    for pattern in (
        r'id="acrCustomerReviewText"[^>]*>\s*\(?\s*([\d.,]+)',
        r'([\d.,]+)\s*(?:yorum|değerlendirme|küresel derecelendirme)',
    ):
        match = re.search(pattern, html, re.I)
        if match:
            total = _parse_tr_number(match.group(1))
            if total > 0:
                return total
    return 0


def _product_name_from_amazon_html(html: str, url: str) -> str:
    match = re.search(r'<span id="productTitle"[^>]*>\s*([^<]+)', html, re.I)
    if match:
        return match.group(1).strip()[:200]
    return _product_name_from_html(html, url)


def _parse_amazon_review_chunk(chunk: str) -> dict | None:
    from html import unescape

    rating_match = re.search(
        r'data-hook="review-star-rating"[\s\S]{0,500}?class="a-icon-alt">([0-5])[.,]?\d*\s*(?:yıldız|star)',
        chunk,
        re.I,
    )
    if not rating_match:
        rating_match = re.search(r'class="a-icon-alt">([0-5])[.,]?\d*\s*(?:yıldız|star)', chunk, re.I)
    body_match = re.search(
        r'data-hook="reviewRichContentContainer"[\s\S]{0,2500}?<(?:span|p)[^>]*>([\s\S]{3,8000}?)</(?:span|p)>',
        chunk,
        re.I,
    )
    if not body_match:
        body_match = re.search(
            r'data-hook="reviewText"[\s\S]{0,2500}?<span[^>]*>([\s\S]{3,8000}?)</span>',
            chunk,
            re.I,
        )
    if not rating_match or not body_match:
        return None
    text = unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body_match.group(1)))).strip()
    if len(text) < 3:
        return None
    try:
        rating = int(rating_match.group(1))
    except (TypeError, ValueError):
        return None
    if not 1 <= rating <= 5:
        return None
    date_match = re.search(r'data-hook="review-date"[^>]*>([^<]+)', chunk, re.I)
    return {
        "rating": rating,
        "comment_text": text[:4000],
        "review_date": _parse_date(date_match.group(1).strip() if date_match else ""),
    }


def _parse_amazon_reviews_from_html(html: str) -> list[dict]:
    collected: list[dict] = []
    seen: set[str] = set()
    parts = re.split(r'(<div[^>]+data-hook="review"[^>]*>)', html)
    for index in range(1, len(parts), 2):
        chunk = parts[index] + (parts[index + 1] if index + 1 < len(parts) else "")
        parsed = _parse_amazon_review_chunk(chunk[:35000])
        if not parsed:
            continue
        key = parsed["comment_text"][:120]
        if key in seen:
            continue
        seen.add(key)
        collected.append(parsed)
        if len(collected) >= MAX_REVIEWS:
            break
    return collected


async def _fetch_amazon_page_bundle(url: str) -> dict:
    asin = _amazon_asin_from_url(url)
    if not asin:
        raise ValueError("Amazon ürün kodu (ASIN) bulunamadı. /dp/... linki deneyin.")
    page_url = _amazon_product_url(asin)
    html = await fetch_review_page_html(page_url, "amazon")
    return {
        "page_url": page_url,
        "asin": asin,
        "product_name": _product_name_from_amazon_html(html, page_url),
        "image_url": _product_image_from_html(html),
        "html": html,
        "total_available": _amazon_total_reviews_from_html(html),
    }


async def fetch_amazon_tr_reviews_api(asin: str, source_url: str, html: str) -> tuple[list[dict], dict]:
    reviews = _parse_amazon_reviews_from_html(html)
    if not reviews:
        raise ValueError(
            "Bu Amazon ürünü için yorum bulunamadı. Ürün sayfası linkini kontrol edin veya CSV ile yükleyin."
        )
    total_available = _amazon_total_reviews_from_html(html) or len(reviews)
    meta = {
        "platform": "amazon",
        "product_name": _product_name_from_amazon_html(html, source_url),
        "sku": f"AMZ-{asin}",
        "average_rating": round(sum(r["rating"] for r in reviews) / len(reviews), 2),
        "total_available": total_available,
    }
    return reviews, meta


def _product_name_from_n11_url(url: str) -> str:
    path = urlparse(url).path.rstrip("/").split("/")[-1]
    if path.isdigit():
        return "N11 Ürün"
    slug = re.sub(r"-\d+$", "", path)
    return slug.replace("-", " ").strip()[:200] or "N11 Ürün"


def _n11_review_product_id_from_url(url: str) -> str | None:
    match = re.search(r"/product-reviews/(\d+)", url)
    return match.group(1) if match else None


def _n11_review_product_id_from_html(html: str) -> str | None:
    for pattern in (
        r'href="/product-reviews/(\d+)"',
        r"/product-reviews/(\d+)",
        r'"productId"\s*:\s*(\d+)',
    ):
        match = re.search(pattern, html)
        if match:
            return match.group(1)
    return None


async def _fetch_n11_page_bundle(url: str) -> dict:
    review_id = _n11_review_product_id_from_url(url)
    if review_id:
        page_url = url.strip()
        return {
            "page_url": page_url,
            "review_product_id": review_id,
            "product_name": _product_name_from_n11_url(url),
            "image_url": None,
            "html": "",
        }

    page_url = normalize_product_page_url(url, "n11")
    html = await fetch_review_page_html(page_url, "n11")
    review_product_id = _n11_review_product_id_from_html(html)
    if not review_product_id:
        raise ValueError("N11 ürün kimliği bulunamadı. Ürün sayfası linkini kontrol edin.")
    return {
        "page_url": page_url,
        "review_product_id": review_product_id,
        "product_name": _product_name_from_html(html, page_url),
        "image_url": _product_image_from_html(html),
        "html": html,
    }


def _product_name_from_trendyol_url(url: str) -> str:
    path = urlparse(url).path.rstrip("/").split("/")[-1]
    slug = re.sub(r"-p-\d+$", "", path, flags=re.I)
    return slug.replace("-", " ").strip()[:200] or "Trendyol Ürün"


def _product_name_from_html(html: str, url: str) -> str:
    m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', html, re.I)
    if m:
        return m.group(1).strip()[:200]
    m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']', html, re.I)
    if m:
        return m.group(1).strip()[:200]
    m = re.search(r"<title>([^<]+)</title>", html, re.I)
    if m:
        title = re.sub(r"\s*[-|].*(?:Hepsiburada|Trendyol|n11).*$", "", m.group(1), flags=re.I).strip()
        if title:
            return title[:200]
    path = urlparse(url).path.rstrip("/").split("/")[-1]
    return path.replace("-", " ")[:200] or "Scrape Ürün"


def _product_image_from_html(html: str) -> str | None:
    patterns = [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'"image"\s*:\s*"(https://[^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"',
    ]
    for pat in patterns:
        m = re.search(pat, html, re.I)
        if m:
            url = m.group(1).strip()
            if url.startswith("//"):
                url = "https:" + url
            if url.startswith("http"):
                return url[:2000]
    return None


def normalize_product_page_url(url: str, platform: str) -> str:
    """Ürün sayfası linkine çevirir (-yorumlari kaldırır, pm/p formatını korur)."""
    parsed = urlparse(url.strip())
    path = parsed.path or ""
    if platform == "hepsiburada":
        path = re.sub(r"-yorumlari/?$", "", path, flags=re.I)
        path = re.sub(r"/yorumlar/?$", "", path, flags=re.I)
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def _review_sku_from_html(html: str, url_sku: str) -> str:
    """Hepsiburada yorum API'si için gerçek SKU (page_value meta veya HBV varyant)."""
    for pat in (
        r'<meta[^>]+name=["\']page_value["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']page_value["\']',
    ):
        m = re.search(pat, html, re.I)
        if m:
            val = m.group(1).strip().upper()
            if re.fullmatch(r"[A-Z0-9]{6,20}", val):
                return val
    m = re.search(r"\bHBV[A-Z0-9]{8,14}\b", html, re.I)
    if m:
        return m.group(0).upper()
    return url_sku.upper()


async def _fetch_hepsiburada_page_bundle(url: str) -> dict:
    page_url = normalize_product_page_url(url, "hepsiburada")
    url_sku = _sku_from_url(url, "hepsiburada")
    html = await fetch_review_page_html(page_url, "hepsiburada")
    return {
        "page_url": page_url,
        "url_sku": url_sku,
        "review_sku": _review_sku_from_html(html, url_sku),
        "product_name": _product_name_from_html(html, page_url),
        "image_url": _product_image_from_html(html),
        "html": html,
    }


async def fetch_product_page_meta(url: str, platform: str) -> dict:
    """Ürün sayfasından başlık, ana görsel ve yorum API SKU."""
    if platform == "hepsiburada":
        bundle = await _fetch_hepsiburada_page_bundle(url)
        return {
            "product_name": bundle["product_name"],
            "image_url": bundle["image_url"],
            "page_url": bundle["page_url"],
            "review_sku": bundle["review_sku"],
        }
    page_url = normalize_product_page_url(url, platform)
    html = await fetch_review_page_html(page_url, platform)
    return {
        "product_name": _product_name_from_html(html, page_url),
        "image_url": _product_image_from_html(html),
        "page_url": page_url,
    }


def _parse_date(value: str | None) -> date:
    if not value:
        return date.today()
    value = str(value).strip()[:40]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            from datetime import datetime

            return datetime.strptime(value[:10], fmt).date()
        except ValueError:
            continue
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", value)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(\d{1,2})\s+([A-Za-zçğıöşüÇĞİÖŞÜ]+)\s+(\d{4})", value)
    if m:
        month = _TR_MONTHS.get(m.group(2).lower())
        if month:
            return date(int(m.group(3)), month, int(m.group(1)))
    return date.today()


def _walk_json_for_reviews(obj, found: list[dict], seen: set[str]) -> None:
    if isinstance(obj, dict):
        rating = obj.get("rating") or obj.get("star") or obj.get("rate") or obj.get("score")
        text = (
            obj.get("content")
            or obj.get("comment")
            or obj.get("comment_text")
            or obj.get("reviewText")
            or obj.get("text")
        )
        if rating is not None and text and isinstance(text, str):
            text = text.strip()
            if len(text) >= 3 and text not in seen:
                try:
                    r = int(round(float(rating)))
                    if 1 <= r <= 5:
                        seen.add(text)
                        found.append(
                            {
                                "rating": r,
                                "comment_text": text[:4000],
                                "review_date": _parse_date(
                                    str(obj.get("created_at") or obj.get("createdAt") or obj.get("date") or "")
                                ),
                            }
                        )
                except (TypeError, ValueError):
                    pass
        for v in obj.values():
            _walk_json_for_reviews(v, found, seen)
    elif isinstance(obj, list):
        for item in obj:
            _walk_json_for_reviews(item, found, seen)


def _extract_from_embedded_json(html: str) -> list[dict]:
    found: list[dict] = []
    seen: set[str] = set()

    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S | re.I)
    if m:
        try:
            _walk_json_for_reviews(json.loads(m.group(1)), found, seen)
        except json.JSONDecodeError:
            pass

    for block in re.findall(r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", html, re.S | re.I):
        try:
            _walk_json_for_reviews(json.loads(block), found, seen)
        except json.JSONDecodeError:
            continue

    return found


def _parse_hepsiburada_review_item(item: dict) -> dict | None:
    star = item.get("star") or item.get("rating")
    review = item.get("review") or {}
    text = review.get("content") if isinstance(review, dict) else None
    if text is None:
        text = item.get("content")
    if star is None or not text or not isinstance(text, str):
        return None
    text = text.strip()
    if len(text) < 3:
        return None
    try:
        rating = int(round(float(star)))
    except (TypeError, ValueError):
        return None
    if not 1 <= rating <= 5:
        return None
    return {
        "rating": rating,
        "comment_text": text[:4000],
        "review_date": _parse_date(str(item.get("createdAt") or item.get("contentUpdatedAt") or "")),
        "product_name": (item.get("product") or {}).get("name"),
    }


async def fetch_hepsiburada_reviews_api(sku: str) -> tuple[list[dict], dict]:
    """Fetch reviews from Hepsiburada's public Hermes user-content API."""
    collected: list[dict] = []
    product_name = ""
    offset = 0
    page_size = 50
    total_available = 0

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=FETCH_TIMEOUT,
        headers=_browser_headers("hepsiburada", accept="application/json, text/plain, */*"),
    ) as client:
        while len(collected) < MAX_REVIEWS:
            response = await client.get(
                _HERMES_REVIEWS_API,
                params={
                    "sku": sku,
                    "from": offset,
                    "size": page_size,
                },
            )
            if response.status_code == 404:
                break
            response.raise_for_status()
            payload = response.json()
            total_available = int(payload.get("totalItemCount") or 0)
            items = (
                payload.get("data", {})
                .get("approvedUserContent", {})
                .get("approvedUserContentList", [])
            )
            if not items:
                break

            for item in items:
                parsed = _parse_hepsiburada_review_item(item)
                if not parsed:
                    continue
                if not product_name and parsed.get("product_name"):
                    product_name = str(parsed["product_name"])
                collected.append(parsed)
                if len(collected) >= MAX_REVIEWS:
                    break

            offset += len(items)
            if offset >= total_available or len(items) < page_size:
                break

    if not collected:
        raise ValueError(
            "Bu ürün için yorum bulunamadı. Linkin doğru olduğundan emin olun veya CSV ile yükleyin."
        )

    meta = {
        "platform": "hepsiburada",
        "product_name": product_name or sku,
        "sku": sku,
        "average_rating": round(sum(r["rating"] for r in collected) / len(collected), 2),
        "total_available": total_available or len(collected),
    }
    return collected, meta


def _extract_from_html_patterns(html: str) -> list[dict]:
    found: list[dict] = []
    seen: set[str] = set()
    patterns = [
        re.compile(
            r'data-rating=["\'](\d)["\'][^>]*>([^<]{10,})',
            re.I | re.S,
        ),
        re.compile(r'"rating"\s*:\s*(\d)[^}]{0,120}?"(?:content|comment|text)"\s*:\s*"([^"]{10,})"', re.S),
    ]
    for pat in patterns:
        for rating, text in pat.findall(html):
            text = re.sub(r"<[^>]+>", " ", text).strip()
            if text and text not in seen and 1 <= int(rating) <= 5:
                seen.add(text)
                found.append(
                    {
                        "rating": int(rating),
                        "comment_text": text[:4000],
                        "review_date": date.today(),
                    }
                )
    return found


def _access_blocked_message(platform: str) -> str:
    label = _platform_label(platform)
    if platform == "trendyol":
        return (
            f"{label} yorumlarına erişim engellendi. "
            "Bir süre sonra tekrar deneyin veya yorumları CSV ile yükleyin."
        )
    return (
        f"{label} sayfasına erişim engellendi. Ürün linkini (yorumlar sayfası olmadan) "
        "deneyin veya CSV ile yükleyin."
    )


def _curl_get_sync(
    url: str,
    *,
    headers: dict[str, str],
    params: dict | None = None,
) -> tuple[int, str]:
    from curl_cffi import requests as curl_requests

    response = curl_requests.get(
        url,
        headers=headers,
        params=params,
        impersonate=_CURL_IMPERSONATE,
        timeout=FETCH_TIMEOUT,
    )
    return response.status_code, response.text


async def _curl_get(
    url: str,
    *,
    headers: dict[str, str],
    params: dict | None = None,
) -> tuple[int, str]:
    return await asyncio.to_thread(_curl_get_sync, url, headers=headers, params=params)


async def fetch_review_page_html(url: str, platform: str | None = None) -> str:
    platform = platform or detect_platform(url)
    headers = _browser_headers(platform, referer=url)
    if platform in ("trendyol", "n11", "amazon"):
        status_code, text = await _curl_get(url, headers=headers)
        if status_code == 403:
            raise ValueError(_access_blocked_message(platform))
        if status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {status_code}",
                request=httpx.Request("GET", url),
                response=httpx.Response(status_code, text=text),
            )
        return text

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=FETCH_TIMEOUT,
        headers=headers,
    ) as client:
        response = await client.get(url)
        if response.status_code == 403:
            raise ValueError(_access_blocked_message(platform))
        response.raise_for_status()
        return response.text


def _parse_n11_review_item(item: dict) -> dict | None:
    rating = item.get("scoreAsStar") or item.get("score")
    text = item.get("contents") or item.get("comment")
    if rating is None or not text or not isinstance(text, str):
        return None
    text = text.strip()
    if len(text) < 3:
        return None
    try:
        stars = int(round(float(rating)))
    except (TypeError, ValueError):
        return None
    if not 1 <= stars <= 5:
        return None
    return {
        "rating": stars,
        "comment_text": text[:4000],
        "review_date": _parse_date(str(item.get("createdDate") or "")),
        "product_name": item.get("productTitle"),
    }


async def fetch_n11_reviews_api(product_id: str, source_url: str) -> tuple[list[dict], dict]:
    """Fetch reviews from N11's public getProductReviews API."""
    collected: list[dict] = []
    product_name = ""
    total_available = 0
    page = 1
    review_referer = f"https://www.n11.com/product-reviews/{product_id}"

    while len(collected) < MAX_REVIEWS:
        api_url = f"https://www.n11.com/getProductReviews/{product_id}?currentPage={page}"
        status_code, text = await _curl_get(
            api_url,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "tr-TR,tr;q=0.9",
                "Referer": review_referer,
            },
        )
        if status_code == 403:
            raise ValueError(_access_blocked_message("n11"))
        if status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {status_code}",
                request=httpx.Request("GET", api_url),
                response=httpx.Response(status_code, text=text),
            )
        payload = json.loads(text)
        total_available = int(
            payload.get("commentCount")
            or (payload.get("reviewStatistics") or {}).get("commentCount")
            or 0
        )
        items = payload.get("productFeedBackReviewList") or []
        if not items:
            break

        for item in items:
            parsed = _parse_n11_review_item(item)
            if not parsed:
                continue
            if not product_name and parsed.get("product_name"):
                product_name = str(parsed["product_name"])
            collected.append(parsed)
            if len(collected) >= MAX_REVIEWS:
                break

        pagination = payload.get("pagination") or {}
        page_count = int(pagination.get("pageCount") or 0)
        if page >= page_count or len(items) < int(pagination.get("itemsPerPage") or 8):
            break
        page += 1

    if not collected:
        raise ValueError(
            "Bu N11 ürünü için yorum bulunamadı. Linkin doğru olduğundan emin olun veya CSV ile yükleyin."
        )

    meta = {
        "platform": "n11",
        "product_name": product_name or _product_name_from_n11_url(source_url),
        "sku": f"N11-{product_id}",
        "average_rating": round(sum(r["rating"] for r in collected) / len(collected), 2),
        "total_available": total_available or len(collected),
    }
    return collected, meta


def _parse_trendyol_review_item(item: dict) -> dict | None:
    rate = item.get("rate") or item.get("rating")
    text = item.get("comment") or item.get("content")
    if rate is None or not text or not isinstance(text, str):
        return None
    text = text.strip()
    if len(text) < 3:
        return None
    try:
        rating = int(round(float(rate)))
    except (TypeError, ValueError):
        return None
    if not 1 <= rating <= 5:
        return None
    return {
        "rating": rating,
        "comment_text": text[:4000],
        "review_date": _parse_date(
            str(item.get("lastModifiedDate") or item.get("commentDateISOtype") or "")
        ),
    }


async def fetch_trendyol_reviews_api(content_id: str, source_url: str) -> tuple[list[dict], dict]:
    """Fetch reviews from Trendyol's public discovery review API."""
    collected: list[dict] = []
    total_available = 0
    page = 0
    headers = _browser_headers(
        "trendyol",
        accept="application/json, text/plain, */*",
        referer=source_url,
    )

    while len(collected) < MAX_REVIEWS:
        status_code, text = await _curl_get(
            _TRENDYOL_REVIEWS_API,
            headers=headers,
            params={
                "contentId": content_id,
                "page": page,
                "order": "DESC",
                "orderBy": "Score",
                "channelId": 1,
            },
        )
        if status_code == 403:
            raise ValueError(_access_blocked_message("trendyol"))
        if status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {status_code}",
                request=httpx.Request("GET", _TRENDYOL_REVIEWS_API),
                response=httpx.Response(status_code, text=text),
            )
        payload = json.loads(text)
        reviews_block = (payload.get("result") or {}).get("productReviews") or {}
        total_available = int(reviews_block.get("totalElements") or 0)
        items = reviews_block.get("content") or []
        if not items:
            break

        for item in items:
            parsed = _parse_trendyol_review_item(item)
            if not parsed:
                continue
            collected.append(parsed)
            if len(collected) >= MAX_REVIEWS:
                break

        total_pages = int(reviews_block.get("totalPages") or 0)
        page += 1
        if page >= total_pages or len(items) < int(reviews_block.get("size") or 30):
            break

    if not collected:
        raise ValueError(
            "Bu Trendyol ürünü için yorum bulunamadı. Linkin doğru olduğundan emin olun veya CSV ile yükleyin."
        )

    product_name = _product_name_from_trendyol_url(source_url)
    meta = {
        "platform": "trendyol",
        "product_name": product_name,
        "sku": f"TY-{content_id}",
        "average_rating": round(sum(r["rating"] for r in collected) / len(collected), 2),
        "total_available": total_available or len(collected),
    }
    return collected, meta


def _rows_from_raw_reviews(
    raw_reviews: list[dict],
    *,
    product_name: str,
    sku: str,
    platform: str,
    source_url: str,
    total_available: int | None = None,
) -> tuple[list[dict], dict]:
    unique: list[dict] = []
    seen: set[str] = set()
    for r in raw_reviews:
        key = r["comment_text"][:120]
        if key not in seen:
            seen.add(key)
            unique.append(r)

    raw_reviews = unique[:MAX_REVIEWS]
    if not raw_reviews:
        raise ValueError(
            "Bu sayfadan yorum çıkarılamadı. Ürünün yorumlar sekmesi linkini deneyin "
            "veya CSV ile yükleyin. Site yapısı değişmiş olabilir."
        )

    rows = [
        {
            "product_name": product_name,
            "sku": sku,
            "rating": r["rating"],
            "comment_text": r["comment_text"],
            "review_date": r["review_date"],
        }
        for r in raw_reviews
    ]
    avg = round(sum(r["rating"] for r in rows) / len(rows), 2)
    meta = {
        "platform": platform,
        "product_name": product_name,
        "sku": sku,
        "average_rating": avg,
        "source_url": source_url,
    }
    if total_available is not None:
        meta["total_available"] = total_available
    return rows, meta


async def scrape_reviews_from_url(url: str) -> tuple[list[dict], dict]:
    """Returns (review rows in CSV-compatible format, meta dict)."""
    url = validate_review_page_url(url)
    platform = detect_platform(url)
    sku = _sku_from_url(url, platform)
    product_name = ""
    image_url: str | None = None
    page_url = normalize_product_page_url(url, platform)
    html = ""

    if platform == "amazon":
        try:
            bundle = await _fetch_amazon_page_bundle(url)
            asin = str(bundle["asin"])
            page_url = str(bundle["page_url"])
            image_url = bundle.get("image_url")
            html = str(bundle.get("html") or "")
            raw_reviews, api_meta = await fetch_amazon_tr_reviews_api(asin, page_url, html)
            product_name = str(
                api_meta.get("product_name")
                or bundle.get("product_name")
                or asin
            )
            sku = f"AMZ-{asin}"
            rows, meta = _rows_from_raw_reviews(
                raw_reviews,
                product_name=product_name,
                sku=sku,
                platform=platform,
                source_url=page_url,
                total_available=int(api_meta.get("total_available") or len(raw_reviews)),
            )
            meta["image_url"] = image_url
            meta["source_url"] = page_url
            meta["asin"] = asin
            if int(api_meta.get("total_available") or 0) > len(raw_reviews):
                meta["note"] = (
                    f"Amazon ön yüzünden son {len(raw_reviews)} yorum çekildi "
                    f"(toplam {api_meta.get('total_available')})."
                )
            return rows, meta
        except ValueError:
            raise
        except httpx.HTTPStatusError as exc:
            logger.warning("Amazon page fetch failed for %s: %s", url, exc)
        except httpx.RequestError as exc:
            logger.warning("Amazon request failed for %s: %s", url, exc)

    if platform == "n11":
        try:
            bundle = await _fetch_n11_page_bundle(url)
            review_product_id = str(bundle["review_product_id"])
            page_url = str(bundle["page_url"])
            image_url = bundle.get("image_url")
            product_name = str(bundle.get("product_name") or "")
            sku = f"N11-{review_product_id}"
            raw_reviews, api_meta = await fetch_n11_reviews_api(review_product_id, page_url)
            product_name = str(
                api_meta.get("product_name")
                or product_name
                or _product_name_from_n11_url(url)
            )
            rows, meta = _rows_from_raw_reviews(
                raw_reviews,
                product_name=product_name,
                sku=sku,
                platform=platform,
                source_url=page_url,
                total_available=int(api_meta.get("total_available") or len(raw_reviews)),
            )
            meta["image_url"] = image_url
            meta["source_url"] = page_url
            meta["review_product_id"] = review_product_id
            return rows, meta
        except httpx.HTTPStatusError as exc:
            logger.warning("N11 API failed for %s: %s", url, exc)
        except ValueError:
            raise
        except httpx.RequestError as exc:
            logger.warning("N11 API request failed for %s: %s", url, exc)

    if platform == "trendyol":
        content_id = _trendyol_content_id(url)
        if content_id:
            try:
                raw_reviews, api_meta = await fetch_trendyol_reviews_api(content_id, page_url)
                product_name = str(api_meta.get("product_name") or _product_name_from_trendyol_url(url))
                rows, meta = _rows_from_raw_reviews(
                    raw_reviews,
                    product_name=product_name,
                    sku=sku,
                    platform=platform,
                    source_url=page_url,
                    total_available=int(api_meta.get("total_available") or len(raw_reviews)),
                )
                meta["source_url"] = page_url
                meta["content_id"] = content_id
                return rows, meta
            except httpx.HTTPStatusError as exc:
                logger.warning("Trendyol API failed for %s: %s", content_id, exc)
            except ValueError:
                raise
            except httpx.RequestError as exc:
                logger.warning("Trendyol API request failed for %s: %s", content_id, exc)

    if platform == "hepsiburada" and not sku.startswith("SCRAPE-"):
        html = ""
        review_sku = sku
        try:
            bundle = await _fetch_hepsiburada_page_bundle(url)
            image_url = bundle.get("image_url")
            product_name = str(bundle.get("product_name") or "")
            page_url = str(bundle.get("page_url") or page_url)
            review_sku = str(bundle.get("review_sku") or sku)
            html = bundle.get("html") or ""
            if review_sku != sku:
                logger.info("Hepsiburada review SKU %s (url SKU %s)", review_sku, sku)
        except Exception as exc:
            logger.warning("Product page meta fetch failed: %s", exc)

        try:
            raw_reviews, api_meta = await fetch_hepsiburada_reviews_api(review_sku)
            if not product_name:
                product_name = str(api_meta["product_name"])
            rows, meta = _rows_from_raw_reviews(
                raw_reviews,
                product_name=product_name,
                sku=sku,
                platform=platform,
                source_url=page_url,
                total_available=int(api_meta.get("total_available") or len(raw_reviews)),
            )
            meta["image_url"] = image_url
            meta["source_url"] = page_url
            meta["review_sku"] = review_sku
            return rows, meta
        except httpx.HTTPStatusError as exc:
            logger.warning("Hepsiburada API failed for %s: %s", review_sku, exc)
        except ValueError:
            raise
        except httpx.RequestError as exc:
            logger.warning("Hepsiburada API request failed for %s: %s", review_sku, exc)

    try:
        if not html:
            html = await fetch_review_page_html(page_url, platform)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise ValueError("Sayfa bulunamadı — linki kontrol edin") from exc
        raise ValueError(f"Sayfa açılamadı (HTTP {exc.response.status_code})") from exc
    except httpx.RequestError as exc:
        raise ValueError("Sayfa yüklenemedi — linki kontrol edin veya sonra tekrar deneyin") from exc

    product_name = product_name or _product_name_from_html(html, page_url)
    image_url = image_url or _product_image_from_html(html)
    raw_reviews = _extract_from_embedded_json(html)
    if len(raw_reviews) < 3:
        raw_reviews.extend(_extract_from_html_patterns(html))

    rows, meta = _rows_from_raw_reviews(
        raw_reviews,
        product_name=product_name,
        sku=sku,
        platform=platform,
        source_url=page_url,
    )
    meta["image_url"] = image_url
    meta["source_url"] = page_url
    return rows, meta
