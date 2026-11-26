"""Adaptador real para a API pública de catálogo do KaBuM! (novos, OpenBox e marketplace)."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from ..location import LocationInfo
from .base import BaseProvider, ProviderInfo, make_offer_id, sanitize_offer

KABUM_SEARCH_URL = "https://servicespub.prod.api.aws.grupokabum.com.br/catalog/v2/products"
USER_AGENT = "PriceHunter/1.0 (Comparador de Precos e Oportunidades por CEP)"


class KabumProvider(BaseProvider):
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="kabum",
            name="KaBuM!",
            status="active",
            supports_new=True,
            supports_used=True,  # OpenBox e anúncios de usados no marketplace KaBuM
            supports_cep_shipping=False,
            supports_pickup=False,
            auth_required="Nenhuma (API pública de catálogo storefront)",
            env_vars=[],
            rate_limit_notes="Consultas sob demanda com timeout e limite de itens por pesquisa.",
            limitations=(
                "Cálculo de frete por CEP não é público sem sessão autenticada no KaBuM!; "
                "o sistema indica frete grátis quando declarado pela API e avisa quando o frete deve ser confirmado na loja."
            ),
            docs_url="https://www.kabum.com.br/",
        )

    def search(
        self,
        query: str,
        location: LocationInfo | None = None,
        mode: str = "all",
        limit: int = 12,
        timeout: float = 10.0,
    ) -> list[dict[str, Any]]:
        params = urllib.parse.urlencode(
            {
                "query": query,
                "page_number": 1,
                "page_size": min(max(limit, 4), 24),
            }
        )
        url = f"{KABUM_SEARCH_URL}?{params}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))

        offers: list[dict[str, Any]] = []
        for item in payload.get("data") or []:
            attrs = item.get("attributes") or {}
            if not attrs.get("available", True):
                continue

            item_id = str(item.get("id") or "")
            title = (attrs.get("title") or "").strip()
            slug = (attrs.get("product_link") or "").strip()
            if not item_id or not title:
                continue

            offer_url = (
                f"https://www.kabum.com.br/produto/{item_id}/{slug}"
                if slug
                else f"https://www.kabum.com.br/produto/{item_id}"
            )

            # Usa menor preço real anunciado (PIX/desconto à vista quando disponível)
            price_disc = attrs.get("price_with_discount")
            price_reg = attrs.get("price")
            price = price_disc if (price_disc and float(price_disc) > 0) else price_reg
            if not price or float(price) <= 0:
                continue

            old_price = attrs.get("old_price") or (price_reg if price_disc and price_reg and float(price_reg) > float(price_disc) else None)
            images = attrs.get("images") or []
            image_url = images[0] if isinstance(images, list) and images else ""

            manufacturer = attrs.get("manufacturer") or {}
            brand = manufacturer.get("name", "") if isinstance(manufacturer, dict) else ""

            is_mp = bool(attrs.get("is_marketplace"))
            mp_info = attrs.get("marketplace") or {}
            seller_name = (
                (mp_info.get("seller_name") or "Parceiro KaBuM!")
                if is_mp and isinstance(mp_info, dict)
                else "KaBuM! (Venda direta)"
            )

            avg_rating = attrs.get("average_of_ratings") or attrs.get("score_of_ratings")
            num_ratings = int(attrs.get("number_of_ratings") or 0)
            rating_val = float(avg_rating) if avg_rating and float(avg_rating) > 0 else None

            free_shipping = bool(attrs.get("has_free_shipping"))
            is_openbox = bool(attrs.get("is_openbox"))
            warranty = (attrs.get("warranty") or "").strip()

            offer = sanitize_offer(
                {
                    "id": make_offer_id("kabum", item_id, offer_url),
                    "external_id": item_id,
                    "provider_id": "kabum",
                    "store": "KaBuM!",
                    "seller_name": seller_name,
                    "is_marketplace": is_mp,
                    "title": title,
                    "brand": brand,
                    "category": attrs.get("menu", "") or "",
                    "condition": "seminovo" if is_openbox else "new",
                    "is_openbox": is_openbox,
                    "price": float(price),
                    "list_price": float(old_price) if old_price and float(old_price) > float(price) else None,
                    "shipping_price": 0.0 if free_shipping else None,
                    "free_shipping": free_shipping,
                    "shipping_note": "Frete grátis informado pela loja" if free_shipping else "Calcular frete na loja",
                    "delivery_days": None,
                    "delivery_label": "Consultar prazo na loja",
                    "pickup_available": False,
                    "pickup_only": False,
                    "distance_km": None,
                    "distance_is_approximate": True,
                    "seller_location": "",
                    "seller_rating": rating_val,
                    "rating_count": num_ratings,
                    "warranty": warranty,
                    "image_url": image_url,
                    "url": offer_url,
                    "source_real": True,
                }
            )
            if offer:
                offers.append(offer)
        return offers
