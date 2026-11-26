"""Orquestrador principal de pesquisa de ofertas do Price Hunter."""

from __future__ import annotations

import time
from typing import Any

from .db import Database
from .location import LocationInfo, LocationService
from .normalizer import analyze_query, enrich_and_deduplicate_offers
from .providers import ProviderRegistry
from .scoring import score_and_rank_offers
from .security import normalize_cep, sanitize_query

POPULAR_CATEGORIES = [
    {"id": "smartphones", "label": "Smartphones", "query": "iPhone 15", "icon": "smartphone"},
    {"id": "hardware", "label": "Componentes de PC", "query": "Ryzen 5 5600", "icon": "cpu"},
    {"id": "notebooks", "label": "Notebooks", "query": "Notebook i5 16GB", "icon": "laptop"},
    {"id": "tvs", "label": "Televisores 4K", "query": "Smart TV 55 4K", "icon": "tv"},
    {"id": "eletro", "label": "Eletrodomésticos", "query": "Geladeira Frost Free", "icon": "fridge"},
    {"id": "moveis", "label": "Móveis", "query": "Cadeira Escritório Ergonômica", "icon": "chair"},
    {"id": "ferramentas", "label": "Ferramentas", "query": "Furadeira Parafusadeira", "icon": "wrench"},
    {"id": "usados", "label": "Usados e OpenBox", "query": "iPhone usado", "icon": "tag", "mode": "used"},
]


class SearchService:
    def __init__(
        self,
        db: Database,
        location_service: LocationService,
        registry: ProviderRegistry,
    ) -> None:
        self.db = db
        self.location_service = location_service
        self.registry = registry

    def search(
        self,
        raw_query: str,
        raw_cep: str,
        mode: str = "all",
        custom_weights: dict[str, Any] | None = None,
        user_id: int | None = None,
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        query = sanitize_query(raw_query)
        if len(query) < 2:
            raise ValueError("Digite pelo menos 2 caracteres para pesquisar um produto.")

        clean_mode = mode if mode in ("all", "new", "used") else "all"

        cep_digits = normalize_cep(raw_cep)
        if not cep_digits:
            raise ValueError("Informe um CEP brasileiro válido (8 dígitos) para calcular frete e proximidade.")

        location: LocationInfo = self.location_service.lookup_cep(cep_digits)
        analysis = analyze_query(query, clean_mode)

        provider_bundle = self.registry.search_all(
            query=query,
            location=location,
            mode=clean_mode,
            limit_per_provider=12,
        )

        raw_offers = provider_bundle["offers"]
        processed, groups = enrich_and_deduplicate_offers(raw_offers, analysis)

        # Filtro estrito por modo (novos vs usados/seminovos/recondicionados)
        if clean_mode == "new":
            processed = [o for o in processed if o.get("condition") == "new"]
        elif clean_mode == "used":
            processed = [
                o for o in processed if o.get("condition") in ("used", "seminovo", "refurbished")
            ]

        scored_offers, score_meta = score_and_rank_offers(
            processed,
            mode=clean_mode,
            custom_weights=custom_weights,
        )

        # Registra preços reais observados e anexa histórico real (sem inventar dados)
        self.db.record_price_snapshots(scored_offers, cep=location.cep)
        for offer in scored_offers:
            offer["price_history"] = self.db.get_price_history_stats(
                product_key=offer["product_key"],
                current_price=offer["price"],
                offer_url=offer["url"],
            )

        best_price = min((o["price"] for o in scored_offers), default=None)
        self.db.log_search(
            query=query,
            normalized_query=analysis.normalized_query,
            cep=location.cep,
            mode=clean_mode,
            result_count=len(scored_offers),
            best_price=best_price,
            user_id=user_id,
        )

        # Facetas para filtros instantâneos no frontend sem recarregar a página
        facets = {
            "stores": sorted({o["store"] for o in scored_offers if o.get("store")}),
            "brands": sorted({o["brand"] for o in scored_offers if o.get("brand")}),
            "categories": sorted({o["category"] for o in scored_offers if o.get("category")}),
            "conditions": sorted({o["condition"] for o in scored_offers if o.get("condition")}),
            "price_range": {
                "min": min((o["price"] for o in scored_offers), default=0),
                "max": max((o["price"] for o in scored_offers), default=0),
            },
            "has_pickup": any(o.get("pickup_available") for o in scored_offers),
            "has_distance": any(o.get("distance_km") is not None for o in scored_offers),
            "has_delivery_days": any(o.get("delivery_days") is not None for o in scored_offers),
        }

        warnings: list[str] = []
        if provider_bundle["partial"]:
            failed_names = ", ".join(f["name"] for f in provider_bundle["failed"])
            warnings.append(
                f"Resultados parciais: não foi possível consultar {failed_names} neste momento. "
                "As demais fontes ativas responderam normalmente."
            )
        if clean_mode == "used":
            warnings.append(
                "Pesquisa de usados: fontes de classificados como OLX Brasil não possuem API pública aberta; "
                "configure MERCADOLIVRE_ACCESS_TOKEN no .env para ampliar resultados de usados locais. "
                "Exibindo ofertas reais de usados/OpenBox encontradas nas fontes ativas."
            )
        if not scored_offers:
            warnings.append(
                "Nenhuma oferta real equivalente foi encontrada nas fontes ativas para este termo/filtro. "
                "Tente termos mais amplos (ex.: marca + modelo) ou altere o modo de condição."
            )

        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        return {
            "query": analysis.to_dict(),
            "mode": clean_mode,
            "location": location.to_dict(),
            "offers": scored_offers,
            "equivalence_groups": groups,
            "facets": facets,
            "scoring": score_meta,
            "providers": {
                "queried": provider_bundle["queried"],
                "succeeded": provider_bundle["succeeded"],
                "failed": provider_bundle["failed"],
                "inactive": provider_bundle["inactive"],
                "partial": provider_bundle["partial"],
            },
            "warnings": warnings,
            "elapsed_ms": elapsed_ms,
        }

    def get_home_data(self, user_id: int | None = None) -> dict[str, Any]:
        return {
            "popular_categories": POPULAR_CATEGORIES,
            "recent_searches": self.db.get_recent_searches(user_id=user_id, limit=8),
            "observed_highlights": self.db.get_recent_observed_highlights(limit=4),
            "providers": self.registry.list_providers(),
        }
