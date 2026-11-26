"""Adaptador oficial para a API do Mercado Livre (OAuth 2.0) — novos e usados."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

from ..config import settings
from ..location import STATE_APPROX_COORDS, LocationInfo, haversine_km, sanitize_seller_location
from .base import BaseProvider, ProviderInfo, make_offer_id, sanitize_offer

ML_SEARCH_URL = "https://api.mercadolibre.com/sites/MLB/search"
USER_AGENT = "PriceHunter/1.0"


class MercadoLivreProvider(BaseProvider):
    """
    Mercado Livre passou a exigir autenticação OAuth 2.0 (PolicyAgent) em /sites/MLB/search.
    Quando MERCADOLIVRE_ACCESS_TOKEN está configurado no .env, a consulta é executada
    diretamente na API oficial (suportando novos e usados com localização aproximada).
    """

    def __init__(self, access_token: str | None = None) -> None:
        self._token = (access_token if access_token is not None else settings.ml_access_token).strip()

    def info(self) -> ProviderInfo:
        active = bool(self._token)
        return ProviderInfo(
            id="mercadolivre",
            name="Mercado Livre",
            status="active" if active else "requires_credentials",
            supports_new=True,
            supports_used=True,
            supports_cep_shipping=False,
            supports_pickup=True,
            auth_required="OAuth 2.0 Bearer Token (Mercado Livre Developers)",
            env_vars=["MERCADOLIVRE_ACCESS_TOKEN", "MERCADOLIVRE_CLIENT_ID", "MERCADOLIVRE_CLIENT_SECRET"],
            rate_limit_notes="Sujeito às cotas da aplicação registrada no DevCenter do Mercado Livre.",
            limitations=(
                "A API oficial /sites/MLB/search bloqueia chamadas anônimas (HTTP 403 PolicyAgent). "
                "Configure MERCADOLIVRE_ACCESS_TOKEN no .env para ativar novos e usados no Mercado Livre."
            ),
            docs_url="https://developers.mercadolivre.com.br/devcenter",
        )

    def search(
        self,
        query: str,
        location: LocationInfo | None = None,
        mode: str = "all",
        limit: int = 12,
        timeout: float = 10.0,
    ) -> list[dict[str, Any]]:
        if not self._token:
            return []

        params: dict[str, Any] = {"q": query, "limit": min(max(limit, 5), 25)}
        if mode == "new":
            params["condition"] = "new"
        elif mode == "used":
            params["condition"] = "used"

        url = f"{ML_SEARCH_URL}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self._token}",
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))

        offers: list[dict[str, Any]] = []
        for item in data.get("results") or []:
            cond_raw = (item.get("condition") or "new").lower()
            condition = "used" if cond_raw == "used" else "new"
            shipping = item.get("shipping") or {}
            free_ship = bool(shipping.get("free_shipping"))
            store_pickup = bool(shipping.get("store_pick_up"))
            logistic_type = shipping.get("logistic_type") or ""
            pickup_only = (shipping.get("mode") == "not_specified") and condition == "used"

            addr = item.get("address") or {}
            city = addr.get("city_name") or ""
            state_id = (addr.get("state_id") or "").replace("BR-", "").upper()
            seller_loc = sanitize_seller_location(city=city, state=state_id)

            dist_km = None
            same_state = False
            if location and state_id:
                same_state = location.state == state_id
                if location.lat is not None and location.lng is not None and state_id in STATE_APPROX_COORDS:
                    st_lat, st_lng = STATE_APPROX_COORDS[state_id]
                    dist_km = haversine_km(location.lat, location.lng, st_lat, st_lng)

            seller = item.get("seller") or {}
            rep = seller.get("seller_reputation") or {}
            level_id = rep.get("level_id") or ""
            rating_map = {"5_green": 4.9, "4_light_green": 4.4, "3_yellow": 3.7, "2_orange": 2.8, "1_red": 1.8}
            seller_rating = rating_map.get(level_id)

            thumb = (item.get("thumbnail") or "").replace("http://", "https://")
            permalink = item.get("permalink") or ""

            offer = sanitize_offer(
                {
                    "id": make_offer_id("mercadolivre", str(item.get("id") or ""), permalink),
                    "external_id": str(item.get("id") or ""),
                    "provider_id": "mercadolivre",
                    "store": "Mercado Livre",
                    "seller_name": seller.get("nickname") or "Vendedor Mercado Livre",
                    "is_marketplace": True,
                    "title": item.get("title") or "",
                    "brand": "",
                    "category": item.get("category_id") or "",
                    "condition": condition,
                    "price": item.get("price"),
                    "list_price": item.get("original_price"),
                    "shipping_price": 0.0 if free_ship else None,
                    "free_shipping": free_ship,
                    "shipping_note": (
                        "Frete grátis (Mercado Envios)"
                        if free_ship
                        else ("Apenas retirada / combinar com vendedor" if pickup_only else "Consultar frete no anúncio")
                    ),
                    "delivery_days": 2 if logistic_type == "fulfillment" else None,
                    "delivery_label": "Full (entrega expressa)" if logistic_type == "fulfillment" else "Consultar no anúncio",
                    "pickup_available": store_pickup or pickup_only,
                    "pickup_only": pickup_only,
                    "distance_km": dist_km,
                    "distance_is_approximate": True,
                    "same_state": same_state,
                    "seller_location": seller_loc,
                    "seller_rating": seller_rating,
                    "rating_count": int((rep.get("transactions") or {}).get("completed") or 0),
                    "warranty": item.get("warranty") or "",
                    "image_url": thumb,
                    "url": permalink,
                    "source_real": True,
                }
            )
            if offer:
                offers.append(offer)
        return offers
