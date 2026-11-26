"""Adaptadores preparados para fontes que exigem credenciais comerciais ou não possuem API pública aberta."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

from ..config import settings
from ..location import LocationInfo
from .base import BaseProvider, ProviderInfo, make_offer_id, sanitize_offer

USER_AGENT = "PriceHunter/1.0"


class GoogleShoppingSerpApiProvider(BaseProvider):
    """Integração legítima com Google Shopping BR via SerpAPI quando SERPAPI_API_KEY estiver configurada."""

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = (api_key if api_key is not None else settings.serpapi_api_key).strip()

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="google_shopping",
            name="Google Shopping (BR)",
            status="active" if self._api_key else "requires_credentials",
            supports_new=True,
            supports_used=True,
            supports_cep_shipping=False,
            supports_pickup=False,
            auth_required="Chave de API SerpAPI (Google Shopping engine)",
            env_vars=["SERPAPI_API_KEY"],
            rate_limit_notes="Conforme plano contratado na SerpAPI.",
            limitations="Requer SERPAPI_API_KEY no arquivo .env para ativar resultados do Google Shopping Brasil.",
            docs_url="https://serpapi.com/google-shopping-api",
        )

    def search(
        self,
        query: str,
        location: LocationInfo | None = None,
        mode: str = "all",
        limit: int = 10,
        timeout: float = 10.0,
    ) -> list[dict[str, Any]]:
        if not self._api_key:
            return []
        loc_str = f"{location.city}, {location.state}, Brazil" if location else "Sao Paulo, Brazil"
        params = urllib.parse.urlencode(
            {
                "engine": "google_shopping",
                "q": query,
                "gl": "br",
                "hl": "pt",
                "location": loc_str,
                "api_key": self._api_key,
            }
        )
        req = urllib.request.Request(
            f"https://serpapi.com/search.json?{params}",
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))

        offers: list[dict[str, Any]] = []
        for item in (data.get("shopping_results") or [])[:limit]:
            price = item.get("extracted_price")
            link = item.get("product_link") or item.get("link") or ""
            delivery_txt = (item.get("delivery") or "").lower()
            free_ship = "grátis" in delivery_txt or "gratis" in delivery_txt or "free" in delivery_txt
            offer = sanitize_offer(
                {
                    "id": make_offer_id("google_shopping", str(item.get("product_id") or item.get("position")), link),
                    "external_id": str(item.get("product_id") or ""),
                    "provider_id": "google_shopping",
                    "store": item.get("source") or "Google Shopping",
                    "seller_name": item.get("source") or "Google Shopping",
                    "is_marketplace": True,
                    "title": item.get("title") or "",
                    "brand": "",
                    "category": "",
                    "condition": "used" if "usado" in (item.get("second_hand_condition") or "").lower() else "new",
                    "price": price,
                    "list_price": item.get("extracted_old_price"),
                    "shipping_price": 0.0 if free_ship else None,
                    "free_shipping": free_ship,
                    "shipping_note": item.get("delivery") or "Consultar na loja",
                    "delivery_days": None,
                    "delivery_label": item.get("delivery") or "Consultar na loja",
                    "pickup_available": False,
                    "pickup_only": False,
                    "distance_km": None,
                    "distance_is_approximate": True,
                    "seller_location": "",
                    "seller_rating": item.get("rating"),
                    "rating_count": int(item.get("reviews") or 0),
                    "warranty": "",
                    "image_url": item.get("thumbnail") or "",
                    "url": link,
                    "source_real": True,
                }
            )
            if offer:
                offers.append(offer)
        return offers


class PreparedStubProvider(BaseProvider):
    """Adaptador preparado para integração futura sem scraping irregular ou dados falsos."""

    def __init__(self, metadata: ProviderInfo) -> None:
        self._meta = metadata

    def info(self) -> ProviderInfo:
        return self._meta

    def search(
        self,
        query: str,
        location: LocationInfo | None = None,
        mode: str = "all",
        limit: int = 12,
        timeout: float = 10.0,
    ) -> list[dict[str, Any]]:
        return []


def create_prepared_providers() -> list[BaseProvider]:
    return [
        GoogleShoppingSerpApiProvider(),
        PreparedStubProvider(
            ProviderInfo(
                id="amazon_br",
                name="Amazon Brasil",
                status="requires_credentials",
                supports_new=True,
                supports_used=True,
                supports_cep_shipping=False,
                supports_pickup=False,
                auth_required="Amazon Product Advertising API (PA-API 5.0) com assinatura AWS SigV4",
                env_vars=["AMAZON_ACCESS_KEY", "AMAZON_SECRET_KEY", "AMAZON_PARTNER_TAG"],
                rate_limit_notes="Limite inicial de 1 req/s, escalado conforme volume de vendas qualificadas no Programa de Associados.",
                limitations="Exige conta aprovada no Programa de Associados Amazon Brasil e vendas iniciais qualificadas para liberação das chaves PA-API.",
                docs_url="https://webservices.amazon.com/paapi5/documentation/",
            )
        ),
        PreparedStubProvider(
            ProviderInfo(
                id="shopee_br",
                name="Shopee Brasil",
                status="requires_credentials",
                supports_new=True,
                supports_used=False,
                supports_cep_shipping=False,
                supports_pickup=False,
                auth_required="Shopee Affiliate Open Platform (App ID + Secret Key com assinatura SHA256)",
                env_vars=["SHOPEE_APP_ID", "SHOPEE_APP_SECRET"],
                rate_limit_notes="Cotas controladas por App ID na Shopee Open Platform.",
                limitations="Requer aprovação prévia no Programa de Afiliados Shopee Brasil e geração de credenciais de API.",
                docs_url="https://open.shopee.com/",
            )
        ),
        PreparedStubProvider(
            ProviderInfo(
                id="magalu",
                name="Magazine Luiza",
                status="requires_credentials",
                supports_new=True,
                supports_used=False,
                supports_cep_shipping=True,
                supports_pickup=True,
                auth_required="Magalu Partners / Open API homologada",
                env_vars=["MAGALU_API_KEY"],
                rate_limit_notes="Definido em contrato de parceria técnica Magalu.",
                limitations="Magazine Luiza não disponibiliza API aberta pública de busca de catálogo para terceiros sem homologação comercial.",
                docs_url="https://developers.magalu.com/",
            )
        ),
        PreparedStubProvider(
            ProviderInfo(
                id="casas_bahia",
                name="Casas Bahia",
                status="requires_credentials",
                supports_new=True,
                supports_used=False,
                supports_cep_shipping=False,
                supports_pickup=True,
                auth_required="Rede de Afiliados Awin (Product Datafeed / Publisher API)",
                env_vars=["AWIN_API_TOKEN", "AWIN_PUBLISHER_ID"],
                rate_limit_notes="Feeds atualizados periodicamente via API da Awin.",
                limitations="Afiliação e catálogo das Casas Bahia são distribuídos via rede Awin; configure AWIN_API_TOKEN para habilitar.",
                docs_url="https://developer.awin.com/",
            )
        ),
        PreparedStubProvider(
            ProviderInfo(
                id="olx_br",
                name="OLX Brasil (Classificados Locais)",
                status="unavailable_api",
                supports_new=False,
                supports_used=True,
                supports_cep_shipping=False,
                supports_pickup=True,
                auth_required="Sem API pública de busca de anúncios de terceiros",
                env_vars=[],
                rate_limit_notes="N/A",
                limitations=(
                    "A OLX Brasil não oferece API oficial para consulta de anúncios de terceiros "
                    "(a API OLX Pro é restrita à gestão do estoque próprio de revendedores). "
                    "O Price Hunter não realiza scraping irregular nem burla proteções da plataforma."
                ),
                docs_url="https://www.olx.com.br/",
            )
        ),
    ]
