"""Kullanıcı deposu — AI ile hazırlanan ilan taslakları."""
import json
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import db

router = APIRouter(prefix="/api/warehouse", tags=["warehouse"])
ROOT = Path(__file__).resolve().parent.parent / "data" / "users"


class WarehouseSaveRequest(BaseModel):
    draft: dict
    note: str = Field(default="", max_length=500)


def _warehouse_dir(user_id: str) -> Path:
    d = ROOT / user_id / "warehouse"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_item_path(uid: str, item_id: str) -> Path:
    """item_id'nin warehouse dizini dışına çıkmasını engeller (path traversal koruması)."""
    # item_id yalnızca hex karakterlerinden oluşmalı
    if not item_id or not all(c in "0123456789abcdefABCDEF" for c in item_id):
        raise HTTPException(400, "Geçersiz öğe kimliği.")
    wdir = _warehouse_dir(uid)
    p = (wdir / f"{item_id}.json").resolve()
    if p.parent != wdir.resolve():
        raise HTTPException(400, "Geçersiz öğe kimliği.")
    return p


def _read_item(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise HTTPException(500, "Depo kaydı okunamadı.") from e


@router.get("/items")
def list_items(request: Request):
    """Depodaki ürün özetleri (yeniden eskiye)."""
    uid = request.state.user["id"]
    wdir = _warehouse_dir(uid)
    items = []
    for path in wdir.glob("*.json"):
        try:
            rec = _read_item(path)
        except HTTPException:
            continue
        draft = rec.get("draft") or {}
        items.append({
            "id": rec.get("id", path.stem),
            "title": rec.get("title") or draft.get("title") or "İsimsiz ürün",
            "sale_price": rec.get("sale_price") or draft.get("sale_price"),
            "image_filename": rec.get("image_filename") or draft.get("image_filename"),
            "source_image_filenames": rec.get("source_image_filenames")
            or draft.get("source_image_filenames")
            or ([draft.get("image_filename")] if draft.get("image_filename") else []),
            "created_at": rec.get("created_at"),
            "updated_at": rec.get("updated_at"),
            "note": rec.get("note", ""),
        })
    items.sort(key=lambda x: x.get("updated_at") or x.get("created_at") or 0, reverse=True)
    return {"items": items, "total": len(items)}


@router.get("/items/{item_id}")
def get_item(item_id: str, request: Request):
    uid = request.state.user["id"]
    path = _safe_item_path(uid, item_id)
    if not path.is_file():
        raise HTTPException(404, "Depo kaydı bulunamadı.")
    return _read_item(path)


@router.post("/items")
def save_item(req: WarehouseSaveRequest, request: Request):
    """Analiz edilmiş taslağı depoya kaydet."""
    uid = request.state.user["id"]
    draft = req.draft
    if not draft.get("title") or not draft.get("image_filename"):
        raise HTTPException(400, "Taslak eksik — önce AI analizi yapın.")

    item_id = db.new_id()
    now = time.time()
    sources = draft.get("source_image_filenames") or [draft["image_filename"]]
    record = {
        "id": item_id,
        "created_at": now,
        "updated_at": now,
        "title": (draft.get("title") or "").strip()[:120],
        "sale_price": draft.get("sale_price"),
        "image_filename": draft.get("image_filename"),
        "source_image_filenames": sources,
        "draft": draft,
        "note": (req.note or "").strip(),
    }
    path = _warehouse_dir(uid) / f"{item_id}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "ok": True,
        "id": item_id,
        "message": "Ürün depoya kaydedildi. Depodaki Ürünlerim sekmesinden erişebilirsin.",
    }


@router.put("/items/{item_id}")
def update_item(item_id: str, req: WarehouseSaveRequest, request: Request):
    """Mevcut depo kaydını güncelle."""
    uid = request.state.user["id"]
    path = _safe_item_path(uid, item_id)
    if not path.is_file():
        raise HTTPException(404, "Depo kaydı bulunamadı.")
    existing = _read_item(path)
    draft = req.draft
    if not draft.get("title") or not draft.get("image_filename"):
        raise HTTPException(400, "Taslak eksik — başlık ve görsel gerekli.")
    sources = draft.get("source_image_filenames") or [draft.get("image_filename")]
    existing.update({
        "updated_at": time.time(),
        "title": (draft.get("title") or "").strip()[:120],
        "sale_price": draft.get("sale_price"),
        "image_filename": draft.get("image_filename"),
        "source_image_filenames": sources,
        "draft": draft,
        "note": (req.note or existing.get("note", "")).strip(),
    })
    path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "id": item_id, "message": "Depo kaydı güncellendi."}


@router.delete("/items/{item_id}")
def delete_item(item_id: str, request: Request):
    uid = request.state.user["id"]
    path = _safe_item_path(uid, item_id)
    if not path.is_file():
        raise HTTPException(404, "Depo kaydı bulunamadı.")
    path.unlink()
    return {"ok": True, "message": "Ürün depodan silindi."}
