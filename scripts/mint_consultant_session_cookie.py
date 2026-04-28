import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from consultant_dashboard.app import create_app
from consultant_dashboard.core.db import get_consultant_by_email, get_db, get_vendor_by_slug


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    parser.add_argument("--vendor-slug", default="mindfix")
    args = parser.parse_args()

    app = create_app()
    email = args.email.strip().lower()
    vendor_slug = (args.vendor_slug or "mindfix").strip().lower()

    with app.app_context():
        db = get_db(app.config)
        vendor = get_vendor_by_slug(db, vendor_slug)
        if not vendor:
            db.close()
            raise SystemExit(f"Vendor not found: {vendor_slug}")
        consultant = get_consultant_by_email(db, email, vendor_id=vendor["id"])
        db.close()
        if not consultant:
            raise SystemExit(f"Consultant not found: {email}")

        with app.test_request_context("/", environ_overrides={"HTTP_HOST": "mindfix.me"}):
            session_payload = {
                "consultant_id": consultant["id"],
                "_permanent": True,
            }
            serializer = app.session_interface.get_signing_serializer(app)
            if serializer is None:
                raise SystemExit("Unable to initialize Flask session serializer")
            cookie = serializer.dumps(session_payload)
            print(cookie)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
