import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

from flask import current_app, g, request, session, url_for

from .db import get_db, get_vendor_by_id, get_vendor_by_slug


DEFAULT_VENDOR_SLUG = "mindfix"


def request_vendor_slug() -> str:
    environ_slug = (request.environ.get("mindfix.vendor_slug") or "").strip().lower()
    if environ_slug:
        return environ_slug
    session_slug = (session.get("vendor_slug") or "").strip().lower()
    if session_slug:
        return session_slug
    return DEFAULT_VENDOR_SLUG


def get_current_vendor() -> Dict[str, Any]:
    cached = getattr(g, "_current_vendor", None)
    if cached is not None:
        return cached

    slug = request_vendor_slug()
    db = get_db(current_app.config)
    row = get_vendor_by_slug(db, slug) or get_vendor_by_slug(db, DEFAULT_VENDOR_SLUG)
    db.close()
    if row is None:
        vendor = {
            "slug": DEFAULT_VENDOR_SLUG,
            "name": current_app.config["BRAND_NAME"],
            "storage_root": current_app.config["STORAGE_ROOT"],
            "www_root": str(Path(current_app.root_path).parent / "www" / DEFAULT_VENDOR_SLUG),
            "primary_host": current_app.config["PUBLIC_BASE_URL"],
            "brand_config_json": "",
            "is_active": 1,
        }
    else:
        vendor = dict(row)

    g._current_vendor = vendor
    session["vendor_slug"] = vendor["slug"]
    return vendor


def current_vendor_slug() -> str:
    return get_current_vendor()["slug"]


def tenant_prefix(slug: str = "") -> str:
    resolved = (slug or request_vendor_slug()).strip().lower()
    if not resolved:
        return ""
    return f"/v/{resolved}"


def tenant_path(path: str, slug: str = "") -> str:
    target = (path or "/").strip() or "/"
    if target.startswith("http://") or target.startswith("https://"):
        return target
    if not target.startswith("/"):
        target = "/" + target
    if target.startswith("/v/"):
        return target
    return f"{tenant_prefix(slug)}{target}"


def tenant_url_for(endpoint: str, **values) -> str:
    path = url_for(endpoint, **values)
    return tenant_path(path)


def current_storage_root() -> str:
    vendor = get_current_vendor()
    return (vendor.get("storage_root") or current_app.config["STORAGE_ROOT"]).strip()


def tenant_public_url(path: str, slug: str = "") -> str:
    vendor = get_current_vendor() if not slug else None
    base = ((vendor or {}).get("primary_host") or current_app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")
    if not base:
        host = current_app.config.get("HOST", "127.0.0.1")
        port = current_app.config.get("PORT", 8090)
        base = f"http://{host}:{port}"
    return f"{base}{tenant_path(path, slug)}"


def storage_root_for_client(client_id: str) -> str:
    db = get_db(current_app.config)
    try:
        client = db.execute(
            "SELECT vendor_id FROM clients WHERE id = ? LIMIT 1",
            (client_id,),
        ).fetchone()
        if not client or not client["vendor_id"]:
            return current_app.config["STORAGE_ROOT"]
        vendor = get_vendor_by_id(db, client["vendor_id"])
        if not vendor:
            return current_app.config["STORAGE_ROOT"]
        return (vendor["storage_root"] or current_app.config["STORAGE_ROOT"]).strip()
    finally:
        db.close()


@lru_cache(maxsize=64)
def _load_brand_file(www_root: str) -> Dict[str, Any]:
    brand_path = Path(www_root) / "brand.json"
    if not brand_path.exists():
        return {}
    try:
        return json.loads(brand_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def current_branding() -> Dict[str, Any]:
    vendor = get_current_vendor()
    brand_data: Dict[str, Any] = {}
    if vendor.get("brand_config_json"):
        try:
            brand_data.update(json.loads(vendor["brand_config_json"]))
        except Exception:
            pass
    www_root = (vendor.get("www_root") or "").strip()
    if www_root:
        brand_data.update(_load_brand_file(www_root))
    brand_data.setdefault("slug", vendor["slug"])
    brand_data.setdefault("name", vendor.get("name") or current_app.config["BRAND_NAME"])
    brand_data.setdefault("www_root", www_root)
    brand_data.setdefault("wordmark", brand_data.get("site_title") or brand_data["name"])
    brand_data.setdefault("site_title", brand_data["name"])
    brand_data.setdefault("icon_class", "fa-solid fa-brain")
    brand_data.setdefault("logo_url", "/img/logo-mark.svg")
    brand_data.setdefault("accent", "#0f6b42")
    brand_data.setdefault("accent_soft", "rgba(15, 107, 66, 0.08)")
    brand_data.setdefault("accent_soft_solid", "#ebf4ec")
    brand_data.setdefault("consultant_accent", "#6b9bd1")
    brand_data.setdefault("consultant_accent_soft", "rgba(107, 155, 209, 0.12)")
    brand_data.setdefault("consultant_accent_soft_solid", "#edf3fb")
    brand_data.setdefault("text_main", "#132218")
    brand_data.setdefault("text_muted", "#5a7261")
    brand_data.setdefault("consultant_text_main", "#17314f")
    brand_data.setdefault("consultant_text_muted", "#58708b")
    brand_data.setdefault("bg_start", "#f4f7f2")
    brand_data.setdefault("bg_end", "#eef5ef")
    brand_data.setdefault("consultant_bg_start", "#f4f8fd")
    brand_data.setdefault("consultant_bg_end", "#ecf3fb")
    brand_data.setdefault("topbar_bg", "#1b2838")
    brand_data.setdefault("topbar_text", "#ffffff")
    brand_data.setdefault("topbar_mark", "#2bb58e")
    return brand_data


class TenantPrefixMiddleware:
    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        if path.startswith("/v/"):
            parts = path.split("/")
            if len(parts) >= 3 and parts[2]:
                vendor_slug = parts[2].strip().lower()
                environ["mindfix.vendor_slug"] = vendor_slug
                current_script = environ.get("SCRIPT_NAME", "")
                environ["SCRIPT_NAME"] = f"{current_script}/v/{vendor_slug}".rstrip("/")
                suffix = "/" + "/".join(parts[3:])
                environ["PATH_INFO"] = suffix or "/"
        return self.app(environ, start_response)
