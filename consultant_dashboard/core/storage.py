import json
import os
from pathlib import Path
from typing import Dict, Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


class EncryptedStorage:
    def __init__(self, root: str, master_key_hex: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.root = self.root.resolve()
        self.master_key = bytes.fromhex(master_key_hex)

    def _safe_path(self, storage_key: str) -> Path:
        candidate = (self.root / str(storage_key)).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("storage key escapes configured storage root") from exc
        return candidate

    def _derive_key(self, client_id: str, salt: bytes) -> bytes:
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            info=client_id.encode("utf-8"),
        )
        return hkdf.derive(self.master_key)

    def put_json(self, storage_key: str, client_id: str, payload: dict) -> str:
        path = self._safe_path(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        salt = os.urandom(16)
        nonce = os.urandom(12)
        key = self._derive_key(client_id, salt)
        encrypted = AESGCM(key).encrypt(nonce, json.dumps(payload).encode("utf-8"), None)
        path.write_bytes(salt + nonce + encrypted)
        return storage_key

    def get_json(self, storage_key: str, client_id: str) -> Optional[Dict]:
        path = self._safe_path(storage_key)
        if not path.exists():
            return None
        data = path.read_bytes()
        salt = data[:16]
        nonce = data[16:28]
        encrypted = data[28:]
        key = self._derive_key(client_id, salt)
        plaintext = AESGCM(key).decrypt(nonce, encrypted, None)
        return json.loads(plaintext.decode("utf-8"))

    def delete_json(self, storage_key: str) -> bool:
        path = self._safe_path(storage_key)
        if not path.exists():
            return False
        path.unlink()
        return True
