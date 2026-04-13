import hashlib
import re


def hash_value(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"[^\d+]", "", (phone or "").strip())
    if not digits:
        return ""
    if not digits.startswith("+"):
        if len(digits) == 10:
            digits = "+1" + digits
        elif len(digits) == 11 and digits.startswith("1"):
            digits = "+" + digits
    return digits


def build_identity_hashes(display_name: str, email: str, phone_number: str) -> dict:
    normalized_name = normalize_name(display_name)
    normalized_email = (email or "").strip().lower()
    normalized_phone = normalize_phone(phone_number)

    return {
        "normalized_name_hash": hash_value(normalized_name) if normalized_name else "",
        "email_hash": hash_value(normalized_email) if normalized_email else "",
        "phone_hash": hash_value(normalized_phone) if normalized_phone else "",
    }
