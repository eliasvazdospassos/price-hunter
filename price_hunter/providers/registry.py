"""Registro e orquestração paralela dos provedores de pesquisa com isolamento de falhas."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from ..config import settings
from ..location import LocationInfo
from .base import BaseProvider, ProviderResult
from .kabum import KabumProvider
from .mercadolivre import MercadoLivreProvider
from .stubs import create_prepared_providers
from .vtex import create_bemol_provider, create_carrefour_provider, create_electrolux_provider


class ProviderRegistry:
    """Mantém todos os provedores registrados e executa consultas paralelas com tolerância a falhas."""

    def __init__(self, providers: list[BaseProvider] | None = None) -> None:
        if providers is not None:
            self._providers = list(providers)
        else:
            self._providers = [
                KabumProvider(),
                create_carrefour_provider(),
                create_bemol_provider(),
                create_electrolux_provider(),
                MercadoLivreProvider(),
                *create_prepared_providers(),
            ]

    def list_providers(self) -> list[dict[str, Any]]:
        return [p.info().to_dict() for p in self._providers]

    def active_providers(self, mode: str = "all") -> list[BaseProvider]:
        result: list[BaseProvider] = []
        for p in self._providers:
            meta = p.info()
            if meta.status != "active":
                continue
            if mode == "used" and not meta.supports_used:
                continue
            if mode == "new" and not meta.supports_new:
                continue
            result.append(p)
        return result

    def search_all(
        self,
        query: str,
        location: LocationInfo | None = None,
        mode: str = "all",
        limit_per_provider: int = 12,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        eff_timeout = timeout or settings.provider_timeout
        active = self.active_providers(mode=mode)
        inactive = [p.info().to_dict() for p in self._providers if p not in active]

        results: list[ProviderResult] = []
        all_offers: list[dict[str, Any]] = []

        def _run_one(provider: BaseProvider) -> ProviderResult:
            meta = provider.info()
            t0 = time.perf_counter()
            try:
                items = provider.search(
                    query=query,
                    location=location,
                    mode=mode,
                    limit=limit_per_provider,
                    timeout=eff_timeout,
                )
                elapsed = int((time.perf_counter() - t0) * 1000)
                return ProviderResult(
                    provider_id=meta.id,
                    provider_name=meta.name,
                    ok=True,
                    offers=items,
                    elapsed_ms=elapsed,
                )
            except TimeoutError:
                elapsed = int((time.perf_counter() - t0) * 1000)
                return ProviderResult(
                    provider_id=meta.id,
                    provider_name=meta.name,
                    ok=False,
                    elapsed_ms=elapsed,
                    error="Tempo limite excedido ao consultar a fonte.",
                )
            except Exception as exc:
                elapsed = int((time.perf_counter() - t0) * 1000)
                return ProviderResult(
                    provider_id=meta.id,
                    provider_name=meta.name,
                    ok=False,
                    elapsed_ms=elapsed,
                    error=f"Falha ao consultar {meta.name}: {type(exc).__name__}",
                )

        if active:
            with ThreadPoolExecutor(max_workers=min(6, len(active))) as pool:
                futures = [pool.submit(_run_one, p) for p in active]
                for fut in as_completed(futures):
                    res = fut.result()
                    results.append(res)
                    if res.ok:
                        all_offers.extend(res.offers)

        succeeded = [
            {
                "id": r.provider_id,
                "name": r.provider_name,
                "count": len(r.offers),
                "elapsed_ms": r.elapsed_ms,
            }
            for r in results
            if r.ok
        ]
        failed = [
            {
                "id": r.provider_id,
                "name": r.provider_name,
                "error": r.error,
                "elapsed_ms": r.elapsed_ms,
            }
            for r in results
            if not r.ok
        ]

        return {
            "offers": all_offers,
            "queried": [p.info().to_dict() for p in active],
            "succeeded": succeeded,
            "failed": failed,
            "inactive": inactive,
            "partial": bool(failed),
        }
