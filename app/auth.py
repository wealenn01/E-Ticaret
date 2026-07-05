"""Üyelik ve oturum yönetimi.

- Parolalar PBKDF2-HMAC-SHA256 (200k tur) + kullanıcı başına tuz ile saklanır.
- Oturum: httpOnly çerezde rastgele token, sunucuda sessions tablosunda 30 gün.
- E-posta doğrulama: 5 haneli kod (demo hesaplar otomatik doğrulanır).
"""
import hashlib
import re
import secrets
import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from . import db
from . import mail

SESSION_COOKIE = "tya_session"
SESSION_TTL = 30 * 24 * 3600
CODE_TTL = 15 * 60
RESEND_COOLDOWN = 60
PBKDF2_ROUNDS = 200_000
DELETE_CONFIRM = "HESABIMI SIL"

router = APIRouter(prefix="/api/auth", tags=["auth"])
USERS_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "users"


def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ROUNDS
    ).hex()


def _valid_email(email: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email))


def is_email_verified(user: dict) -> bool:
    if user.get("is_demo"):
        return True
    return user.get("email_verified_at") is not None


def create_user(email: str, password: str, full_name: str = "", company: str = "", *, auto_verify: bool = False) -> dict:
    email = email.strip().lower()
    if not _valid_email(email):
        raise HTTPException(400, "Geçerli bir e-posta adresi girin.")
    if len(password) < 8:
        raise HTTPException(400, "Parola en az 8 karakter olmalı.")
    if db.query_one("SELECT id FROM users WHERE email=?", (email,)):
        raise HTTPException(409, "Bu e-posta ile zaten bir hesap var — giriş yapmayı deneyin.")
    salt = secrets.token_hex(16)
    user_id = db.new_id()
    verified_at = db.now() if auto_verify else None
    db.execute(
        "INSERT INTO users (id, email, password_hash, salt, full_name, company, created_at, is_demo, email_verified_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (user_id, email, _hash_password(password, salt), salt, full_name.strip(), company.strip(), db.now(), 0, verified_at),
    )
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
    return db.query_one(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token=? AND s.expires_at > ?",
        (token, db.now()),
    )


def require_verified_user(request: Request) -> dict:
    """Korumalı API uçlarında middleware sonrası doğrulanmış kullanıcı."""
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Oturum gerekli — giriş yap veya kayıt ol.")
    return user


def destroy_session(request: Request) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        db.execute("DELETE FROM sessions WHERE token=?", (token,))


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, token, max_age=SESSION_TTL, httponly=True, samesite="lax", path="/",
    )


def _generate_code() -> str:
    return f"{secrets.randbelow(100000):05d}"


def _create_verification_code(user_id: str) -> str:
    code = _generate_code()
    now = db.now()
    db.execute(
        "INSERT INTO email_verification_codes (user_id, code, created_at, expires_at) VALUES (?,?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET code=excluded.code, created_at=excluded.created_at, expires_at=excluded.expires_at",
        (user_id, code, now, now + CODE_TTL),
    )
    return code


def send_verification_email_for_user(user: dict) -> dict:
    if is_email_verified(user):
        return {"ok": True, "already_verified": True}
    if not mail.is_configured():
        raise HTTPException(
            503,
            "E-posta servisi yapılandırılmamış. Yönetici .env dosyasına Resend veya Gmail SMTP ayarlarını eklemeli.",
        )
    code = _create_verification_code(user["id"])
    try:
        return mail.send_verification_code_email(user["email"], code, user.get("full_name") or "")
    except RuntimeError as e:
        raise HTTPException(502, str(e)) from e


def verify_email_by_code(user_id: str, code: str) -> dict:
    code = (code or "").strip()
    if not code:
        raise HTTPException(400, "5 haneli doğrulama kodunu girin.")
    if not re.fullmatch(r"\d{5}", code):
        raise HTTPException(400, "5 haneli doğrulama kodunu girin.")
    row = db.query_one("SELECT * FROM email_verification_codes WHERE user_id=?", (user_id,))
    if not row:
        raise HTTPException(400, "Doğrulama kodu bulunamadı — yeni kod isteyin.")
    if row["expires_at"] < db.now():
        db.execute("DELETE FROM email_verification_codes WHERE user_id=?", (user_id,))
        raise HTTPException(400, "Kodun süresi dolmuş — yeni kod isteyin.")
    if not secrets.compare_digest(row["code"], code):
        raise HTTPException(400, "Doğrulama kodu hatalı.")
    db.execute("UPDATE users SET email_verified_at=? WHERE id=?", (db.now(), user_id))
    db.execute("DELETE FROM email_verification_codes WHERE user_id=?", (user_id,))
    user = db.query_one("SELECT email FROM users WHERE id=?", (user_id,))
    return {"ok": True, "email": user["email"]}


def delete_user_account(user: dict, password: str, confirm: str) -> None:
    if confirm.strip().upper() != DELETE_CONFIRM:
        raise HTTPException(400, f'Onay metni olarak tam olarak "{DELETE_CONFIRM}" yazmalısın.')
    if not secrets.compare_digest(user["password_hash"], _hash_password(password, user["salt"])):
        raise HTTPException(401, "Parola hatalı.")
    user_id = user["id"]
    user_dir = USERS_DATA_DIR / user_id
    db.delete_user_cascade(user_id)
    if user_dir.is_dir():
        shutil.rmtree(user_dir, ignore_errors=True)


def public_user(user: dict, extra: Optional[dict] = None) -> dict:
    from . import billing

    sub = billing.get_subscription(user["id"])
    out = {
        "id": user["id"],
        "email": user["email"],
        "full_name": user.get("full_name") or "",
        "company": user.get("company") or "",
        "email_verified": is_email_verified(user),
        "is_demo": bool(user.get("is_demo")),
        "plan": sub["plan"],
        "plan_name": billing.PLANS[sub["plan"]]["name"],
        "subscription": sub,
    }
    if extra:
        out.update(extra)
    return out


# ---- API uçları ----

class RegisterRequest(BaseModel):
    email: str
    password: str
    full_name: str = ""
    company: str = ""


class LoginRequest(BaseModel):
    email: str
    password: str


class VerifyCodeRequest(BaseModel):
    code: str


class DeleteAccountRequest(BaseModel):
    password: str
    confirm: str


@router.post("/register")
def register(req: RegisterRequest, response: Response):
    if not mail.is_configured():
        raise HTTPException(
            503,
            "Kayıt şu an e-posta doğrulaması gerektiriyor ancak mail servisi yapılandırılmamış.",
        )
    user = create_user(req.email, req.password, req.full_name, req.company)
    token = create_session(user["id"])
    _set_cookie(response, token)
    full = db.query_one("SELECT * FROM users WHERE id=?", (user["id"],))
    send_verification_email_for_user(full)
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


@router.post("/verify-email-code")
def verify_email_code(req: VerifyCodeRequest, request: Request):
    user = get_session_user(request)
    if not user:
        raise HTTPException(401, "Oturum gerekli.")
    if is_email_verified(user):
        return {"ok": True, "already_verified": True, "message": "E-posta zaten doğrulanmış."}
    result = verify_email_by_code(user["id"], req.code)
    return {"ok": True, "email": result["email"], "message": "E-posta adresin doğrulandı."}


@router.post("/resend-verification")
def resend_verification(request: Request):
    user = get_session_user(request)
    if not user:
        raise HTTPException(401, "Oturum gerekli.")
    if is_email_verified(user):
        return {"ok": True, "already_verified": True, "message": "E-posta zaten doğrulanmış."}
    row = db.query_one("SELECT created_at FROM email_verification_codes WHERE user_id=?", (user["id"],))
    if row and db.now() - row["created_at"] < RESEND_COOLDOWN:
        wait = int(RESEND_COOLDOWN - (db.now() - row["created_at"]))
        raise HTTPException(429, f"Yeni kod için {wait} saniye bekleyin.")
    mail_result = send_verification_email_for_user(user)
    return {"ok": True, "message": "Doğrulama kodu e-postana gönderildi."}


@router.post("/delete-account")
def delete_account(req: DeleteAccountRequest, request: Request, response: Response):
    user = get_session_user(request)
    if not user:
        raise HTTPException(401, "Oturum gerekli.")
    delete_user_account(user, req.password, req.confirm)
    destroy_session(request)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True, "message": "Hesabın kalıcı olarak silindi. Aynı e-posta ile yeniden kayıt olabilirsin."}


# ---- Demo hesap (GitHub akışı) ----

def _seed_demo_data(user_id: str) -> None:
    import csv as csv_mod
    from pathlib import Path as _Path

    sample_dir = _Path(__file__).resolve().parent.parent / "sample-data"
    users_data_dir = _Path(__file__).resolve().parent.parent / "data" / "users" / user_id
    users_data_dir.mkdir(parents=True, exist_ok=True)

    reviews_csv = sample_dir / "yorumlar_ornek.csv"
    if reviews_csv.exists():
        try:
            from . import ai_seller as _ai_seller
            _ai_seller.set_user_dir(users_data_dir)
            _ai_seller.import_reviews_csv(reviews_csv.read_bytes())
        except Exception:
            pass

    returns_csv = sample_dir / "iadeler_ornek.csv"
    if returns_csv.exists():
        try:
            from . import ai_seller as _ai_seller
            _ai_seller.set_user_dir(users_data_dir)
            _ai_seller.import_returns_csv(returns_csv.read_bytes())
        except Exception:
            pass

    shipments_csv = sample_dir / "kargolar_ornek.csv"
    if shipments_csv.exists():
        try:
            from . import ai_seller as _ai_seller
            _ai_seller.set_user_dir(users_data_dir)
            _ai_seller.import_shipments_csv(shipments_csv.read_bytes())
        except Exception:
            pass

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
    """Tek tıkla demo hesabı — örnek verilerle dolu, e-posta doğrulama gerekmez."""
    db.cleanup_expired_demos()

    suffix = secrets.token_hex(4)
    email = f"demo_{suffix}@trendyolai.demo"
    password = secrets.token_urlsafe(12)

    user = create_user(email, password, full_name="Demo Kullanıcı", company="Demo Mağaza", auto_verify=True)
    db.execute("UPDATE users SET is_demo=1 WHERE id=?", (user["id"],))

    _seed_demo_data(user["id"])

    token = create_session(user["id"])
    _set_cookie(response, token)
    full = db.query_one("SELECT * FROM users WHERE id=?", (user["id"],))
    return {**public_user(full), "demo_email": email, "demo_password": password, "demo": True}
