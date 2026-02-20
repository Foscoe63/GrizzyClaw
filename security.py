"""Security utilities for GrizzyClaw"""

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from cryptography.fernet import Fernet
from jose import jwt, JWTError
from passlib.context import CryptContext


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class SecurityManager:
    """Handles encryption, authentication, and security utilities"""

    def __init__(self, secret_key: str):
        self.secret_key = secret_key
        self._fernet = Fernet(self._derive_key(secret_key))

    def _derive_key(self, secret: str) -> bytes:
        """Derive Fernet key from secret"""
        key = hashlib.sha256(secret.encode()).digest()
        return base64.urlsafe_b64encode(key)

    def encrypt(self, data: str) -> str:
        """Encrypt sensitive data"""
        return self._fernet.encrypt(data.encode()).decode()

    def decrypt(self, encrypted: str) -> str:
        """Decrypt sensitive data"""
        return self._fernet.decrypt(encrypted.encode()).decode()

    def hash_password(self, password: str) -> str:
        """Hash a password"""
        return pwd_context.hash(password)

    def verify_password(self, password: str, hashed: str) -> bool:
        """Verify a password against its hash"""
        return pwd_context.verify(password, hashed)

    def create_jwt_token(
        self, data: dict, expires_delta: Optional[timedelta] = None
    ) -> str:
        """Create JWT token"""
        to_encode = data.copy()

        if expires_delta:
            expire = datetime.now(timezone.utc) + expires_delta
        else:
            expire = datetime.now(timezone.utc) + timedelta(hours=24)

        to_encode.update({"exp": expire})
        return jwt.encode(to_encode, self.secret_key, algorithm="HS256")

    def verify_jwt_token(self, token: str) -> Optional[dict]:
        """Verify and decode JWT token"""
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=["HS256"])
            return payload
        except JWTError:
            return None

    def generate_api_key(self) -> str:
        """Generate a secure API key"""
        return f"gc_{secrets.token_urlsafe(32)}"

    def generate_session_id(self) -> str:
        """Generate a secure session ID"""
        return secrets.token_urlsafe(16)

    def verify_webhook_signature(
        self, payload: bytes, signature: str, secret: str
    ) -> bool:
        """Verify webhook signature (HMAC-SHA256)"""
        expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)


# Rate limiter
class RateLimiter:
    """Simple in-memory rate limiter"""

    def __init__(self, max_requests: int = 100, window: int = 60):
        self.max_requests = max_requests
        self.window = window
        self._requests = {}

    def is_allowed(self, key: str) -> bool:
        """Check if request is allowed under rate limit"""
        now = datetime.now(timezone.utc)

        if key not in self._requests:
            self._requests[key] = []

        # Clean old requests
        self._requests[key] = [
            req_time
            for req_time in self._requests[key]
            if (now - req_time).total_seconds() < self.window
        ]

        if len(self._requests[key]) >= self.max_requests:
            return False

        self._requests[key].append(now)
        return True
