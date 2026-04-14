import hashlib
import re

from .phone_numbers import normalize_phone as normalize_supported_phone


def hash_value(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def normalize_phone(phone: str) -> str:
    try:
        return normalize_supported_phone(phone)
    except ValueError:
        return ""


def build_identity_hashes(display_name: str, email: str, phone_number: str) -> dict:
    normalized_name = normalize_name(display_name)
    normalized_email = (email or "").strip().lower()
    normalized_phone = normalize_phone(phone_number)

    return {
        "normalized_name_hash": hash_value(normalized_name) if normalized_name else "",
        "email_hash": hash_value(normalized_email) if normalized_email else "",
        "phone_hash": hash_value(normalized_phone) if normalized_phone else "",
    }
