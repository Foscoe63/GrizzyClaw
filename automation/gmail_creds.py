"""Gmail OAuth credentials: load/save with optional encryption at rest"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Magic prefix for encrypted credential files
_ENCRYPTED_PREFIX = "GCE:"


def load_gmail_credentials(
    path: str,
    secret_key: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Load Gmail OAuth credentials from file.
    Supports plain JSON or encrypted format (GCE: prefix).

    Args:
        path: Path to credentials file (.json or .enc)
        secret_key: App secret key for decryption (required if file is encrypted)

    Returns:
        Credentials dict or None on failure
    """
    p = Path(path).expanduser()
    if not p.exists():
        return None
    try:
        raw = p.read_text(encoding="utf-8")
        if raw.startswith(_ENCRYPTED_PREFIX):
            if not secret_key:
                logger.warning("Encrypted credentials require secret_key")
                return None
            from grizzyclaw.security import SecurityManager
            mgr = SecurityManager(secret_key)
            decrypted = mgr.decrypt(raw[len(_ENCRYPTED_PREFIX):])
            return json.loads(decrypted)
        return json.loads(raw)
    except Exception as e:
        logger.debug(f"Failed to load Gmail credentials: {e}")
        return None


def save_gmail_credentials_encrypted(
    credentials: Dict[str, Any],
    output_path: str,
    secret_key: str,
) -> bool:
    """
    Save Gmail credentials encrypted to file.

    Args:
        credentials: OAuth token dict
        output_path: Path for encrypted file (e.g. .enc)
        secret_key: App secret key for encryption

    Returns:
        True on success
    """
    try:
        from grizzyclaw.security import SecurityManager
        mgr = SecurityManager(secret_key)
        json_str = json.dumps(credentials)
        encrypted = mgr.encrypt(json_str)
        p = Path(output_path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_ENCRYPTED_PREFIX + encrypted, encoding="utf-8")
        return True
    except Exception as e:
        logger.error(f"Failed to encrypt Gmail credentials: {e}", exc_info=True)
        return False
