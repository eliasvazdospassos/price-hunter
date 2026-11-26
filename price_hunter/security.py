"""Utilitários de segurança: validação de entradas, hashing de senha, tokens e rate limiting."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from urllib.parse import urlparse

CEP_DIGITS_RE = re.compile(r"^\d{8}$")
EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")
CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def normalize_cep(raw_cep: str | None) -> str:
    """Remove máscara e retorna 8 dígitos se válido, ou string vazia."""
    if not raw_cep:
        return ""
    digits = re.sub(r"\D", "", str(raw_cep))
    return digits if CEP_DIGITS_RE.match(digits) else ""


def format_cep(cep_digits: str) -> str:
    digits = normalize_cep(cep_digits)
    if not digits:
        return ""
    return f"{digits[:5]}-{digits[5:]}"


def sanitize_query(raw_query: str | None, max_len: int = 120) -> str:
    """Limpa caracteres de controle e limita tamanho da busca."""
    if not raw_query:
        return ""
    cleaned = CONTROL_CHARS_RE.sub(" ", str(raw_query))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:max_len]


def sanitize_text(value: str | None, max_len: int = 200) -> str:
    if not value:
        return ""
    cleaned = CONTROL_CHARS_RE.sub(" ", str(value)).strip()
    return cleaned[:max_len]


def validate_email(email: str) -> str:
    normalized = sanitize_text(email, 160).lower()
    if not EMAIL_RE.match(normalized):
        raise ValueError("Informe um endereço de e-mail válido.")
    return normalized


def validate_password(password: str) -> None:
    if not isinstance(password, str) or len(password) < 8:
        raise ValueError("A senha deve ter pelo menos 8 caracteres.")
    if len(password) > 128:
        raise ValueError("A senha excede o limite máximo de 128 caracteres.")
    has_letter = any(c.isalpha() for c in password)
    has_digit = any(c.isdigit() for c in password)
    if not (has_letter and has_digit):
        raise ValueError("A senha deve conter letras e números.")


def is_safe_http_url(url: str | None) -> bool:
    """Permite apenas links http/https reais com domínio válido (nunca javascript:, data:, etc.)."""
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc) and "." in parsed.netloc


def hash_password(password: str) -> str:
    """Gera hash seguro com scrypt + salt aleatório (nunca armazena senha em texto puro)."""
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    )
    return f"scrypt$16384$8$1${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verifica senha em tempo constante contra o hash armazenado."""
    try:
        algo, n_s, r_s, p_s, salt_hex, dk_hex = stored_hash.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n_s),
            r=int(r_s),
            p=int(p_s),
            dklen=len(bytes.fromhex(dk_hex)),
        )
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


def generate_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class SlidingWindowRateLimiter:
    """Limitador de requisições por IP em janela deslizante de 60 segundos."""

    def __init__(self, max_requests: int = 60, window_seconds: int = 60) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, client_key: str) -> tuple[bool, int]:
        """Retorna (permitido, segundos_para_tentar_novamente)."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            q = self._hits[client_key]
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= self.max_requests:
                retry_after = max(1, int(self.window_seconds - (now - q[0])) + 1)
                return False, retry_after
            q.append(now)
            return True, 0
