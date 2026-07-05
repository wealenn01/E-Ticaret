"""Destek talepleri — kullanıcı mesajını destek e-postasına iletir."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import auth
from . import db
from . import mail

router = APIRouter(prefix="/api/support", tags=["support"])

_COOLDOWN_SEC = 60


class SupportMessageRequest(BaseModel):
    subject: str = Field(..., min_length=3, max_length=120)
    message: str = Field(..., min_length=10, max_length=4000)


@router.get("/info")
def support_info(request: Request):
    """Destek bilgisi — sadece giriş yapmış kullanıcılara."""
    user = auth.get_session_user(request)
    if not user:
        raise HTTPException(401, "Oturum gerekli.")
    return {
        "mail_configured": mail.is_configured(),
    }


@router.post("/message")
def send_support_message(req: SupportMessageRequest, request: Request):
    user = auth.require_verified_user(request)
    if not mail.is_configured():
        raise HTTPException(503, "E-posta servisi yapılandırılmamış — destek mesajı gönderilemiyor.")

    uid = user["id"]
    # Cooldown DB'de saklanır — restart ve çoklu worker'da çalışır
    last_row = db.query_one(
        "SELECT count FROM usage WHERE user_id=? AND period='support' AND metric='cooldown'",
        (uid,),
    )
    if last_row:
        elapsed = db.now() - last_row["count"]
        if elapsed < _COOLDOWN_SEC:
            wait = int(_COOLDOWN_SEC - elapsed)
            raise HTTPException(429, f"Yeni mesaj için {wait} saniye bekleyin.")

    try:
        mail.send_support_email(
            user_email=user["email"],
            user_name=user.get("full_name") or "",
            subject=req.subject.strip(),
            message=req.message.strip(),
            user_id=uid,
        )
    except RuntimeError as e:
        raise HTTPException(502, str(e)) from e

    # Gönderim zamanını kaydet (count alanını timestamp olarak kullan)
    db.execute(
        "INSERT INTO usage (user_id, period, metric, count) VALUES (?,?,?,?) "
        "ON CONFLICT(user_id, period, metric) DO UPDATE SET count=excluded.count",
        (uid, "support", "cooldown", int(db.now())),
    )
    return {
        "ok": True,
        "message": "Mesajın destek ekibine iletildi. En kısa sürede dönüş yapılacak.",
    }
