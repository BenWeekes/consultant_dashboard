import re


SUPPORTED_PHONE_COUNTRIES = {
    "US": {
        "label": "United States (+1)",
        "prefix": "+1",
    },
    "UK": {
        "label": "United Kingdom (+44)",
        "prefix": "+44",
    },
}


def country_options():
    return [{"code": code, **meta} for code, meta in SUPPORTED_PHONE_COUNTRIES.items()]


def normalize_phone(raw_phone: str, country_code: str = "US") -> str:
    raw = (raw_phone or "").strip()
    if not raw:
        return ""

    compact = re.sub(r"[^\d+]", "", raw)
    if not compact:
        return ""

    if compact.startswith("+"):
        digits = "+" + re.sub(r"[^\d]", "", compact[1:])
        if digits.startswith("+1") and len(digits) == 12:
            return digits
        if digits.startswith("+44") and len(digits) == 13:
            return digits
        raise ValueError("Enter a valid US or UK phone number including country code.")

    normalized_country_code = (country_code or "").upper()
    country = SUPPORTED_PHONE_COUNTRIES.get(normalized_country_code)
    if not country:
        raise ValueError("Select a supported country.")

    digits = re.sub(r"[^\d]", "", compact)
    if normalized_country_code == "US":
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        if len(digits) != 10:
            raise ValueError("Enter a valid US phone number.")
        return country["prefix"] + digits

    if normalized_country_code == "UK":
        if digits.startswith("44") and len(digits) == 12:
            digits = digits[2:]
        if digits.startswith("0") and len(digits) == 11:
            digits = digits[1:]
        if len(digits) != 10:
            raise ValueError("Enter a valid UK phone number.")
        return country["prefix"] + digits

    raise ValueError("Select a supported country.")


def infer_country_code(phone_number: str) -> str:
    normalized = (phone_number or "").strip()
    if normalized.startswith("+44"):
        return "UK"
    return "US"


def local_display_number(phone_number: str) -> str:
    normalized = (phone_number or "").strip()
    if normalized.startswith("+44") and len(normalized) == 13:
        return "0" + normalized[3:]
    if normalized.startswith("+1") and len(normalized) == 12:
        return normalized[2:]
    return normalized
