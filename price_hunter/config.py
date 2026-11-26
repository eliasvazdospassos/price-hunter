"""Configuração central do Price Hunter com carregamento seguro de variáveis de ambiente."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def load_dotenv(env_path: Path | None = None) -> None:
    """Carrega variáveis de um arquivo .env local sem sobrescrever variáveis já definidas."""
    path = env_path or (BASE_DIR / ".env")
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv()


DEFAULT_NEW_WEIGHTS: dict[str, int] = {
    "competitive_price": 35,
    "total_cost": 20,
    "delivery_speed": 15,
    "seller_reputation": 15,
    "condition_warranty": 10,
    "proximity": 5,
}

DEFAULT_USED_WEIGHTS: dict[str, int] = {
    "competitive_price": 25,
    "total_cost": 10,
    "delivery_speed": 5,
    "seller_reputation": 15,
    "condition_warranty": 20,
    "proximity": 25,
}


def _default_host() -> str:
    if os.getenv("PRICE_HUNTER_HOST"):
        return os.getenv("PRICE_HUNTER_HOST", "127.0.0.1")
    # Em Google Cloud Run (K_SERVICE / PORT definidos), escuta em todas as interfaces
    if os.getenv("K_SERVICE") or os.getenv("PORT"):
        return "0.0.0.0"
    return "127.0.0.1"


def _default_port() -> int:
    raw = os.getenv("PORT") or os.getenv("PRICE_HUNTER_PORT") or "8000"
    return int(raw)


def _default_db_backend() -> str:
    explicit = os.getenv("PRICE_HUNTER_DB_BACKEND", "auto").strip().lower()
    if explicit in ("sqlite", "firestore"):
        return explicit
    # Em Cloud Run, usa Firestore automaticamente se disponível
    if os.getenv("K_SERVICE"):
        return "firestore"
    return "sqlite"


@dataclass(frozen=True)
class Settings:
    host: str = field(default_factory=_default_host)
    port: int = field(default_factory=_default_port)
    env: str = field(default_factory=lambda: os.getenv("PRICE_HUNTER_ENV", "development"))
    secret_key: str = field(
        default_factory=lambda: os.getenv("PRICE_HUNTER_SECRET_KEY")
        or secrets.token_hex(32)
    )
    db_backend: str = field(default_factory=_default_db_backend)
    db_path: Path = field(
        default_factory=lambda: BASE_DIR
        / os.getenv("PRICE_HUNTER_DB_PATH", "data/price_hunter.db")
    )
    gcp_project: str = field(
        default_factory=lambda: os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("GCP_PROJECT")
        or ""
    )
    firestore_database: str = field(
        default_factory=lambda: os.getenv("FIRESTORE_DATABASE", "(default)").strip()
    )
    rate_limit_per_min: int = field(
        default_factory=lambda: int(os.getenv("PRICE_HUNTER_RATE_LIMIT_PER_MIN", "60"))
    )
    provider_timeout: float = field(
        default_factory=lambda: float(os.getenv("PRICE_HUNTER_PROVIDER_TIMEOUT", "10"))
    )
    # Credenciais opcionais (nunca expostas ao frontend)
    supabase_url: str = field(default_factory=lambda: os.getenv("SUPABASE_URL", "").strip())
    supabase_anon_key: str = field(default_factory=lambda: os.getenv("SUPABASE_ANON_KEY", "").strip())
    ml_access_token: str = field(default_factory=lambda: os.getenv("MERCADOLIVRE_ACCESS_TOKEN", "").strip())
    ml_client_id: str = field(default_factory=lambda: os.getenv("MERCADOLIVRE_CLIENT_ID", "").strip())
    ml_client_secret: str = field(default_factory=lambda: os.getenv("MERCADOLIVRE_CLIENT_SECRET", "").strip())
    amazon_access_key: str = field(default_factory=lambda: os.getenv("AMAZON_ACCESS_KEY", "").strip())
    amazon_secret_key: str = field(default_factory=lambda: os.getenv("AMAZON_SECRET_KEY", "").strip())
    amazon_partner_tag: str = field(default_factory=lambda: os.getenv("AMAZON_PARTNER_TAG", "").strip())
    shopee_app_id: str = field(default_factory=lambda: os.getenv("SHOPEE_APP_ID", "").strip())
    shopee_app_secret: str = field(default_factory=lambda: os.getenv("SHOPEE_APP_SECRET", "").strip())
    magalu_api_key: str = field(default_factory=lambda: os.getenv("MAGALU_API_KEY", "").strip())
    awin_api_token: str = field(default_factory=lambda: os.getenv("AWIN_API_TOKEN", "").strip())
    awin_publisher_id: str = field(default_factory=lambda: os.getenv("AWIN_PUBLISHER_ID", "").strip())
    serpapi_api_key: str = field(default_factory=lambda: os.getenv("SERPAPI_API_KEY", "").strip())

    @property
    def is_production(self) -> bool:
        return self.env.lower() == "production" or bool(os.getenv("K_SERVICE"))


settings = Settings()
