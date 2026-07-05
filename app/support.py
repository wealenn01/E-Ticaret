"""Destek talepleri — kullanıcı mesajını destek e-postasına iletir."""
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import auth
from . import mail

router = APIRouter(prefix="/api/support", tags=["support"])

_COOLDOWN_SEC = 60
_last_sent: dict[str, float] = {}


class SupportMessageRequest(BaseModel):
    subject: str = Field(..., min_length=3, max_length=120)
    message: str = Field(..., min_length=10, max_length=4000)


@router.get("/info")
def support_info():
    return {
        "inbox": mail.SUPPORT_INBOX,
        "mail_configured": mail.is_configured(),
    }


@router.post("/message")
def send_support_message(req: SupportMessageRequest, request: Request):
    user = auth.require_verified_user(request)
    if not mail.is_configured():
        raise HTTPException(503, "E-posta servisi yapılandırılmamış — destek mesajı gönderilemiyor.")

    uid = user["id"]
    now = time.time()
    last = _last_sent.get(uid, 0)
    if now - last < _COOLDOWN_SEC:
        wait = int(_COOLDOWN_SEC - (now - last))
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

    _last_sent[uid] = now
    return {
        "ok": True,
        "message": f"Mesajın {mail.SUPPORT_INBOX} adresine iletildi. En kısa sürede dönüş yapılacak.",
    }
