"""Contrato base para adaptadores de provedores de produtos do Price Hunter."""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any

from ..location import LocationInfo
from ..security import is_safe_http_url


@dataclass
class ProviderInfo:
    id: str
    name: str
    status: str  # "active" | "requires_credentials" | "unavailable_api"
    supports_new: bool = True
    supports_used: bool = False
    supports_cep_shipping: bool = False
    supports_pickup: bool = False
    auth_required: str = "Nenhuma (API pública de catálogo)"
    env_vars: list[str] = field(default_factory=list)
    rate_limit_notes: str = ""
    limitations: str = ""
    docs_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProviderResult:
    provider_id: str
    provider_name: str
    ok: bool
    offers: list[dict[str, Any]] = field(default_factory=list)
    elapsed_ms: int = 0
    error: str = ""
    warning: str = ""


def make_offer_id(provider_id: str, external_id: str, url: str) -> str:
    raw = f"{provider_id}:{external_id}:{url}"
    return f"{provider_id}_{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:12]}"


def sanitize_offer(offer: dict[str, Any]) -> dict[str, Any] | None:
    """Valida campos mínimos e garante que nunca exista link fictício ou inválido."""
    url = (offer.get("url") or "").strip()
    title = (offer.get("title") or "").strip()
    price = offer.get("price")
    if not is_safe_http_url(url) or not title or price is None:
        return None
    try:
        price_f = round(float(price), 2)
    except (TypeError, ValueError):
        return None
    if price_f <= 0:
        return None
    offer["price"] = price_f
    offer["url"] = url
    offer["title"] = title
    img = (offer.get("image_url") or "").strip()
    offer["image_url"] = img if is_safe_http_url(img) else ""
    return offer


class BaseProvider(ABC):
    """Interface abstrata que permite adicionar novos provedores sem reescrever o sistema."""

    @abstractmethod
    def info(self) -> ProviderInfo:
        raise NotImplementedError

    @property
    def is_active(self) -> bool:
        return self.info().status == "active"

    @abstractmethod
    def search(
        self,
        query: str,
        location: LocationInfo | None = None,
        mode: str = "all",
        limit: int = 12,
        timeout: float = 10.0,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError
