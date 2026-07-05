"""Üyelik ve oturum yönetimi.

- Parolalar PBKDF2-HMAC-SHA256 (200k tur) + kullanıcı başına tuz ile saklanır.
- Oturum: httpOnly çerezde rastgele token, sunucuda sessions tablosunda 30 gün.
"""
import hashlib
import re
import secrets
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from . import db

SESSION_COOKIE = "tya_session"
SESSION_TTL = 30 * 24 * 3600  # 30 gün
PBKDF2_ROUNDS = 200_000

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ROUNDS
    ).hex()


def _valid_email(email: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email))


def create_user(email: str, password: str, full_name: str = "", company: str = "") -> dict:
    email = email.strip().lower()
    if not _valid_email(email):
        raise HTTPException(400, "Geçerli bir e-posta adresi girin.")
    if len(password) < 8:
        raise HTTPException(400, "Parola en az 8 karakter olmalı.")
    if db.query_one("SELECT id FROM users WHERE email=?", (email,)):
        raise HTTPException(409, "Bu e-posta ile zaten bir hesap var — giriş yapmayı deneyin.")
    salt = secrets.token_hex(16)
    user_id = db.new_id()
    db.execute(
        "INSERT INTO users (id, email, password_hash, salt, full_name, company, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (user_id, email, _hash_password(password, salt), salt, full_name.strip(), company.strip(), db.now()),
    )
    # Her yeni hesap ücretsiz planla başlar
    db.execute(
        "INSERT INTO subscriptions (id, user_id, plan, status, started_at) VALUES (?,?,?,?,?)",
        (db.new_id(), user_id, "free", "active", db.now()),
    )
    return {"id": user_id, "email": email, "full_name": full_name.strip()}


def verify_login(email: str, password: str) -> Optional[dict]:
    user = db.query_one("SELECT * FROM users WHERE email=?", (email.strip().lower(),))
    if not user:
        return None
    if not secrets.compare_digest(user["password_hash"], _hash_password(password, user["salt"])):
        return None
    return user


def create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
        (token, user_id, db.now(), db.now() + SESSION_TTL),
    )
    return token


def get_session_user(request: Request) -> Optional[dict]:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    row = db.query_one(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token=? AND s.expires_at > ?",
        (token, db.now()),
    )
    return row


def destroy_session(request: Request) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        db.execute("DELETE FROM sessions WHERE token=?", (token,))


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, token, max_age=SESSION_TTL, httponly=True, samesite="lax", path="/",
    )


def public_user(user: dict) -> dict:
    from . import billing  # döngüsel importu önlemek için yerel

    sub = billing.get_subscription(user["id"])
    return {
        "id": user["id"],
        "email": user["email"],
        "full_name": user.get("full_name") or "",
        "company": user.get("company") or "",
        "plan": sub["plan"],
        "plan_name": billing.PLANS[sub["plan"]]["name"],
        "subscription": sub,
    }


# ---- API uçları ----

class RegisterRequest(BaseModel):
    email: str
    password: str
    full_name: str = ""
    company: str = ""


class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/register")
def register(req: RegisterRequest, response: Response):
    user = create_user(req.email, req.password, req.full_name, req.company)
    token = create_session(user["id"])
    _set_cookie(response, token)
    full = db.query_one("SELECT * FROM users WHERE id=?", (user["id"],))
    return public_user(full)


@router.post("/login")
def login(req: LoginRequest, response: Response):
    user = verify_login(req.email, req.password)
    if not user:
        raise HTTPException(401, "E-posta veya parola hatalı.")
    token = create_session(user["id"])
    _set_cookie(response, token)
    return public_user(user)


@router.post("/logout")
def logout(request: Request, response: Response):
    destroy_session(request)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    user = get_session_user(request)
    if not user:
        raise HTTPException(401, "Oturum bulunamadı.")
    return public_user(user)


# ---- Demo hesap oluşturma (ayrı akış) ----

def _seed_demo_data(user_id: str) -> None:
    """Yeni demo hesabına örnek CSV verilerini yükler."""
    import csv as csv_mod
    from pathlib import Path as _Path

    sample_dir = _Path(__file__).resolve().parent.parent / "sample-data"
    users_data_dir = _Path(__file__).resolve().parent.parent / "data" / "users" / user_id
    users_data_dir.mkdir(parents=True, exist_ok=True)

    # Yorumlar
    reviews_csv = sample_dir / "yorumlar_ornek.csv"
    if reviews_csv.exists():
        try:
            from . import ai_seller as _ai_seller
            _ai_seller.set_user_dir(users_data_dir)
            _ai_seller.import_reviews_csv(reviews_csv.read_bytes())
        except Exception:
            pass

    # İadeler
    returns_csv = sample_dir / "iadeler_ornek.csv"
    if returns_csv.exists():
        try:
            from . import ai_seller as _ai_seller
            _ai_seller.set_user_dir(users_data_dir)
            _ai_seller.import_returns_csv(returns_csv.read_bytes())
        except Exception:
            pass

    # Kargolar
    shipments_csv = sample_dir / "kargolar_ornek.csv"
    if shipments_csv.exists():
        try:
            from . import ai_seller as _ai_seller
            _ai_seller.set_user_dir(users_data_dir)
            _ai_seller.import_shipments_csv(shipments_csv.read_bytes())
        except Exception:
            pass

    # Siparişler (analitik için)
    orders_csv = sample_dir / "siparisler_ornek.csv"
    if orders_csv.exists():
        try:
            from . import ai_seller as _ai_seller
            from . import analytics as _analytics
            _ai_seller.set_user_dir(users_data_dir)
            _analytics.import_orders_csv(orders_csv.read_bytes())
        except Exception:
            pass


@router.post("/demo")
def create_demo(response: Response):
    """Tek tıkla demo hesabı oluşturur — örnek verilerle önceden doldurulmuş.
    Demo hesaplar 7 gün sonra otomatik silinir."""
    from . import db as _db

    # Süresi dolmuş demoları temizle
    _db.cleanup_expired_demos()

    suffix = secrets.token_hex(4)
    email = f"demo_{suffix}@trendyolai.demo"
    password = secrets.token_urlsafe(12)

    user = create_user(email, password, full_name="Demo Kullanıcı", company="Demo Mağaza")
    _db.execute("UPDATE users SET is_demo=1 WHERE id=?", (user["id"],))

    # Örnek verileri yükle
    _seed_demo_data(user["id"])

    token = create_session(user["id"])
    _set_cookie(response, token)
    full = _db.query_one("SELECT * FROM users WHERE id=?", (user["id"],))
    return {**public_user(full), "demo_email": email, "demo_password": password, "demo": True}
