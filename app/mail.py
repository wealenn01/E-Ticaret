"""E-posta gönderimi — Resend API veya Gmail/SMTP.

Yapılandırma (.env):
  MAIL_PROVIDER=resend  + RESEND_API_KEY
  veya
  MAIL_PROVIDER=smtp    + SMTP_HOST, SMTP_USER, SMTP_PASSWORD

Demo mod yok — kod yalnızca gerçek e-posta ile gider.
"""
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx

logger = logging.getLogger("trendolai.mail")

MAIL_FROM = os.getenv("MAIL_FROM", "TrendolAi <onboarding@resend.dev>")
SUPPORT_INBOX = os.getenv("SUPPORT_EMAIL", "trendyolai0108@gmail.com").strip()
MAIL_DEV_LOG = os.getenv("MAIL_DEV_LOG", "").lower() in ("1", "true", "yes")

_PLACEHOLDER_MARKERS = (
    "senin@gmail.com",
    "16_haneli",
    "uygulama_sifresi",
    "re_xxxxxxxx",
    "your_",
    "example.com",
    "changeme",
)


def _extract_email(addr: str) -> str:
    if "<" in addr and "@" in addr:
        return addr.split("<")[1].rstrip(">").strip()
    return addr.strip()


def _looks_like_placeholder(value: str) -> bool:
    v = value.strip().lower()
    return not v or any(m in v for m in _PLACEHOLDER_MARKERS)


def _smtp_config_issues() -> list[str]:
    issues: list[str] = []
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").replace(" ", "").strip()
    from_addr = _extract_email(MAIL_FROM)
    if _looks_like_placeholder(user):
        issues.append("SMTP_USER hâlâ şablon değerde — .env içinde gerçek Gmail adresini yaz.")
    elif user.lower() != from_addr.lower():
        issues.append(
            f"SMTP_USER ({user}) ile MAIL_FROM ({from_addr}) eşleşmiyor — ikisi de aynı Gmail olmalı."
        )
    if not password:
        issues.append(
            "SMTP_PASSWORD boş — Google uygulama şifresi gerekli: "
            "https://myaccount.google.com/apppasswords"
        )
    elif _looks_like_placeholder(password):
        issues.append("SMTP_PASSWORD hâlâ şablon metin — gerçek 16 haneli uygulama şifresini yapıştır.")
    elif len(password) != 16:
        issues.append(
            f"SMTP_PASSWORD {len(password)} karakter — Gmail uygulama şifresi tam 16 karakter olmalı."
        )
    return issues


def _active_provider() -> str:
    forced = os.getenv("MAIL_PROVIDER", "").strip().lower()
    if forced in ("resend", "smtp"):
        return forced
    if os.getenv("RESEND_API_KEY", "").strip():
        return "resend"
    if os.getenv("SMTP_HOST", "").strip() and os.getenv("SMTP_USER", "").strip():
        return "smtp"
    return ""


def provider_name() -> str:
    return _active_provider() or "unconfigured"


def is_configured() -> bool:
    if MAIL_DEV_LOG:
        return True
    p = _active_provider()
    if p == "resend":
        key = os.getenv("RESEND_API_KEY", "").strip()
        return bool(key) and not _looks_like_placeholder(key)
    if p == "smtp":
        if _smtp_config_issues():
            return False
        return bool(
            os.getenv("SMTP_HOST", "").strip()
            and os.getenv("SMTP_USER", "").strip()
            and os.getenv("SMTP_PASSWORD", "").strip()
        )
    return False


def config_status() -> dict:
    """Arayüz / status uç noktası için mail yapılandırma özeti."""
    provider = _active_provider() or "unconfigured"
    issues = _smtp_config_issues() if provider == "smtp" else []
    configured = is_configured()
    out = {
        "provider": provider,
        "configured": configured,
        "dev_log": MAIL_DEV_LOG,
    }
    if issues:
        out["issues"] = issues
    if MAIL_DEV_LOG and issues:
        out["note"] = "MAIL_DEV_LOG açık — kodlar sunucu konsoluna yazılır, e-posta gitmez."
    return out


def _code_html(name: str, code: str) -> str:
    greeting = f"Merhaba {name}," if name else "Merhaba,"
    return f"""<!DOCTYPE html><html lang="tr"><body style="font-family:sans-serif;background:#f5f3ff;padding:32px">
<div style="max-width:480px;margin:0 auto;background:#fff;border-radius:16px;padding:32px">
<h1 style="color:#2b2640;font-size:22px">Doğrulama kodun</h1>
<p style="color:#7b7690">{greeting}<br>TrendolAi hesabını doğrulamak için aşağıdaki 5 haneli kodu gir:</p>
<div style="margin:24px 0;padding:18px;background:#f0edfe;border-radius:14px;text-align:center;font-size:36px;font-weight:800;letter-spacing:10px;color:#8b7cf6">{code}</div>
<p style="color:#7b7690;font-size:13px">Kod 15 dakika geçerlidir. Bu işlemi sen yapmadıysan bu e-postayı yok say.</p>
</div></body></html>"""


def _code_text(name: str, code: str) -> str:
    greeting = f"Merhaba {name}," if name else "Merhaba,"
    return f"{greeting}\n\nTrendolAi doğrulama kodun: {code}\n\nKodu uygulamadaki doğrulama ekranına gir. Kod 15 dakika geçerlidir.\n"


def send_verification_code_email(to: str, code: str, name: str = "") -> dict:
    provider = _active_provider()
    issues = _smtp_config_issues() if provider == "smtp" else []
    if MAIL_DEV_LOG and issues:
        logger.warning("=" * 50)
        logger.warning("MAIL_DEV_LOG — doğrulama kodu: %s  →  %s", code, to)
        logger.warning("=" * 50)
        return {"ok": True, "mode": "dev_log", "dev": True}
    if not provider:
        raise RuntimeError(
            "E-posta gönderimi yapılandırılmamış. .env dosyasına RESEND_API_KEY "
            "veya SMTP_HOST + SMTP_USER + SMTP_PASSWORD ekleyin."
        )
    if provider == "smtp" and issues:
        raise RuntimeError(" ".join(issues))
    subject = f"TrendolAi doğrulama kodun: {code}"
    html, text = _code_html(name, code), _code_text(name, code)
    return _deliver(provider, to, subject, html, text)


def send_support_email(
    *,
    user_email: str,
    user_name: str,
    subject: str,
    message: str,
    user_id: str = "",
) -> dict:
    """Destek talebini SUPPORT_INBOX adresine iletir; Reply-To kullanıcı e-postası."""
    provider = _active_provider()
    if not provider:
        raise RuntimeError("E-posta servisi yapılandırılmamış.")
    subj = f"[TrendolAi Destek] {subject}"
    safe_msg = message.strip()
    html = f"""<!DOCTYPE html><html lang="tr"><body style="font-family:sans-serif;padding:24px">
<div style="max-width:560px;margin:0 auto;background:#fff;border:1px solid #edeaf5;border-radius:14px;padding:24px">
<h2 style="margin:0 0 12px;color:#2b2640">Yeni destek mesajı</h2>
<p style="color:#7b7690;font-size:14px"><strong>Gönderen:</strong> {user_name or '—'}<br>
<strong>E-posta:</strong> {user_email}<br>
<strong>Kullanıcı ID:</strong> {user_id or '—'}</p>
<hr style="border:0;border-top:1px solid #edeaf5;margin:16px 0">
<p style="white-space:pre-wrap;font-size:15px;line-height:1.6;color:#2b2640">{_esc_html(safe_msg)}</p>
<p style="color:#9a94b8;font-size:12px;margin-top:20px">Yanıtlamak için bu e-postaya doğrudan cevap verebilirsin (Reply-To: {user_email}).</p>
</div></body></html>"""
    text = (
        f"Yeni destek mesajı\n\nGönderen: {user_name or '—'}\n"
        f"E-posta: {user_email}\nKullanıcı ID: {user_id or '—'}\n\n"
        f"Konu: {subject}\n\n{safe_msg}\n"
    )
    return _deliver(provider, SUPPORT_INBOX, subj, html, text, reply_to=user_email)


def _esc_html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _deliver(provider: str, to: str, subject: str, html: str, text: str, reply_to: str = "") -> dict:
    if provider == "resend":
        return _send_resend(to, subject, html, text, reply_to=reply_to)
    return _send_smtp(to, subject, html, text, reply_to=reply_to)


def _send_resend(to: str, subject: str, html: str, text: str, reply_to: str = "") -> dict:
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("RESEND_API_KEY tanımlı değil.")
    payload: dict = {"from": MAIL_FROM, "to": [to], "subject": subject, "html": html, "text": text}
    if reply_to:
        payload["reply_to"] = [reply_to]
    try:
        r = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=20,
        )
        if r.status_code >= 400:
            detail = r.text[:200]
            raise RuntimeError(f"Resend API hatası ({r.status_code}): {detail}")
        return {"ok": True, "mode": "resend", "id": r.json().get("id")}
    except httpx.HTTPError as e:
        logger.exception("Resend gönderim hatası")
        raise RuntimeError(f"E-posta gönderilemedi: {str(e)[:120]}") from e


def _smtp_from_address(user: str) -> str:
    """Gmail, giriş yapılan adresle From adresinin eşleşmesini zorunlu kılar."""
    if "<" in MAIL_FROM and "@" in MAIL_FROM:
        addr = MAIL_FROM.split("<")[1].rstrip(">").strip()
        if addr.lower() == user.lower():
            return MAIL_FROM
    return user


def _smtp_auth_error_hint(exc: Exception) -> str:
    err = str(exc).lower()
    if "535" in err or "badcredentials" in err or "username and password not accepted" in err:
        return (
            "Gmail kimlik doğrulaması başarısız. Normal Gmail şifresi çalışmaz — "
            "Google hesabında 2 adımlı doğrulama açıp 'Uygulama şifresi' oluşturmalısın "
            "(https://myaccount.google.com/apppasswords). .env içindeki SMTP_PASSWORD "
            "bu 16 haneli uygulama şifresi olmalı; SMTP_USER ise tam Gmail adresin olmalı."
        )
    return f"Gmail/SMTP gönderilemedi: {str(exc)[:150]}"


def _send_smtp(to: str, subject: str, html: str, text: str, reply_to: str = "") -> dict:
    host = os.getenv("SMTP_HOST", "").strip()
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER", "").strip()
    # Uygulama şifreleri bazen boşluklu kopyalanır — Gmail boşluksuz bekler
    password = os.getenv("SMTP_PASSWORD", "").replace(" ", "").strip()
    if not host or not user:
        raise RuntimeError("SMTP_HOST ve SMTP_USER tanımlı değil.")
    if not password:
        raise RuntimeError("SMTP_PASSWORD tanımlı değil.")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = _smtp_from_address(user)
    msg["To"] = to
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP(host, port, timeout=20) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(user, password)
            server.sendmail(user, [to], msg.as_string())
        logger.info("SMTP e-posta gönderildi: %s", to)
        return {"ok": True, "mode": "smtp"}
    except smtplib.SMTPException as e:
        logger.exception("SMTP gönderim hatası")
        raise RuntimeError(_smtp_auth_error_hint(e)) from e
