"""Adaptador real reutilizável para lojas brasileiras na plataforma VTEX (Catálogo + Simulação de Frete por CEP)."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from ..location import LocationInfo, haversine_km, sanitize_seller_location
from .base import BaseProvider, ProviderInfo, make_offer_id, sanitize_offer

USER_AGENT = "PriceHunter/1.0 (Comparador de Precos e Oportunidades por CEP)"
ESTIMATE_RE = re.compile(r"(\d+)\s*(bd|d|h)", re.IGNORECASE)


def parse_vtex_estimate_days(estimate: str | None) -> int | None:
    if not estimate:
        return None
    m = ESTIMATE_RE.search(str(estimate))
    if not m:
        return None
    val = int(m.group(1))
    unit = m.group(2).lower()
    if unit == "h":
        return max(1, (val + 23) // 24)
    return val


class VtexStoreProvider(BaseProvider):
    """
    Integração legítima com a Storefront Catalog API pública VTEX e com o endpoint
    oficial de simulação logística por CEP (/api/checkout/pub/orderForms/simulation).
    """

    def __init__(
        self,
        provider_id: str,
        store_name: str,
        base_url: str,
        limitations: str = "",
        max_shipping_sims: int = 6,
    ) -> None:
        self._id = provider_id
        self._name = store_name
        self._base_url = base_url.rstrip("/")
        self._limitations = limitations
        self._max_shipping_sims = max_shipping_sims

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id=self._id,
            name=self._name,
            status="active",
            supports_new=True,
            supports_used=False,
            supports_cep_shipping=True,
            supports_pickup=True,
            auth_required="Nenhuma (VTEX Public Storefront Catalog & Shipping Simulation API)",
            env_vars=[],
            rate_limit_notes="Consultas paginadas limitadas + simulação de frete por CEP nos itens mais relevantes.",
            limitations=self._limitations
            or "Simulação de frete em tempo real executada para os resultados principais; avaliações individuais de produto não são expostas na rota de catálogo VTEX.",
            docs_url=self._base_url,
        )

    def _simulate_shipping(
        self,
        item_id: str,
        seller_id: str,
        cep: str,
        location: LocationInfo | None,
        timeout: float,
    ) -> dict[str, Any] | None:
        url = f"{self._base_url}/api/checkout/pub/orderForms/simulation"
        body = json.dumps(
            {
                "items": [{"id": str(item_id), "quantity": 1, "seller": str(seller_id)}],
                "postalCode": cep,
                "country": "BRA",
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=min(timeout, 6.0)) as resp:
                sim = json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception:
            return None

        logistics = sim.get("logisticsInfo") or []
        if not logistics:
            return None
        slas = logistics[0].get("slas") or []
        if not slas:
            return None

        best_delivery: dict[str, Any] | None = None
        best_pickup: dict[str, Any] | None = None

        for sla in slas:
            channel = (sla.get("deliveryChannel") or "").lower()
            price_reais = round(float(sla.get("price") or 0) / 100.0, 2)
            days = parse_vtex_estimate_days(sla.get("shippingEstimate"))
            entry = {
                "name": sla.get("name") or "Entrega",
                "price": price_reais,
                "days": days,
                "channel": channel,
                "pickup_info": sla.get("pickupStoreInfo") or {},
                "pickup_distance": sla.get("pickupDistance"),
            }
            if "pickup" in channel:
                if best_pickup is None or (entry["price"], entry["days"] or 99) < (
                    best_pickup["price"],
                    best_pickup["days"] or 99,
                ):
                    best_pickup = entry
            else:
                if best_delivery is None or (entry["price"], entry["days"] or 99) < (
                    best_delivery["price"],
                    best_delivery["days"] or 99,
                ):
                    best_delivery = entry

        distance_km: float | None = None
        pickup_location = ""
        if best_pickup:
            pinfo = best_pickup["pickup_info"]
            addr = pinfo.get("address") or {}
            pickup_location = sanitize_seller_location(
                city=addr.get("city"),
                state=addr.get("state"),
                neighborhood=addr.get("neighborhood"),
            )
            if best_pickup.get("pickup_distance") is not None:
                try:
                    distance_km = round(float(best_pickup["pickup_distance"]), 1)
                except (TypeError, ValueError):
                    pass
            elif location and location.lat is not None and location.lng is not None:
                geo = addr.get("geoCoordinates") or []
                if isinstance(geo, list) and len(geo) == 2:
                    # VTEX geoCoordinates geralmente é [lng, lat]
                    try:
                        p_lng, p_lat = float(geo[0]), float(geo[1])
                        if -35 <= p_lat <= 6 and -75 <= p_lng <= -30:
                            distance_km = haversine_km(location.lat, location.lng, p_lat, p_lng)
                    except (TypeError, ValueError):
                        pass

        return {
            "delivery": best_delivery,
            "pickup": best_pickup,
            "distance_km": distance_km,
            "pickup_location": pickup_location,
        }

    def search(
        self,
        query: str,
        location: LocationInfo | None = None,
        mode: str = "all",
        limit: int = 12,
        timeout: float = 10.0,
    ) -> list[dict[str, Any]]:
        if mode == "used":
            return []

        encoded = urllib.parse.quote(query.strip())
        to_idx = min(max(limit - 1, 3), 15)
        url = f"{self._base_url}/api/catalog_system/pub/products/search/{encoded}?_from=0&_to={to_idx}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            products = json.loads(resp.read().decode("utf-8", errors="replace"))

        if not isinstance(products, list):
            return []

        offers: list[dict[str, Any]] = []
        sim_targets: list[tuple[int, str, str]] = []

        for prod in products:
            items = prod.get("items") or []
            if not items:
                continue
            item = items[0]
            sellers = item.get("sellers") or []
            if not sellers:
                continue

            # Escolhe o vendedor disponível com menor preço
            chosen_seller = None
            chosen_price = None
            for sel in sellers:
                co = sel.get("commertialOffer") or {}
                p = co.get("Price")
                avail = co.get("IsAvailable")
                qty = co.get("AvailableQuantity", 0)
                if avail and p and float(p) > 0 and (qty is None or qty > 0):
                    if chosen_price is None or float(p) < chosen_price:
                        chosen_seller = sel
                        chosen_price = float(p)
            if not chosen_seller or chosen_price is None:
                continue

            co = chosen_seller.get("commertialOffer") or {}
            list_price = co.get("ListPrice")
            seller_id = str(chosen_seller.get("sellerId") or "1")
            seller_name = (chosen_seller.get("sellerName") or self._name).strip()
            is_mp = seller_id != "1" and self._name.lower().split()[0] not in seller_name.lower()

            img_list = item.get("images") or []
            image_url = img_list[0].get("imageUrl", "") if img_list else ""
            categories = prod.get("categories") or []
            category = categories[0] if categories else ""

            link = (prod.get("link") or "").strip()
            if link and link.startswith("/"):
                link = f"{self._base_url}{link}"

            offer = sanitize_offer(
                {
                    "id": make_offer_id(self._id, str(prod.get("productId") or item.get("itemId")), link),
                    "external_id": str(item.get("itemId") or prod.get("productId") or ""),
                    "provider_id": self._id,
                    "store": self._name,
                    "seller_name": seller_name if is_mp else f"{self._name} (Venda direta)",
                    "is_marketplace": is_mp,
                    "title": (prod.get("productName") or item.get("name") or "").strip(),
                    "brand": (prod.get("brand") or "").strip(),
                    "category": category,
                    "condition": "new",
                    "price": chosen_price,
                    "list_price": float(list_price) if list_price and float(list_price) > chosen_price else None,
                    "shipping_price": None,
                    "free_shipping": False,
                    "shipping_note": "Informe o CEP para simular frete" if not location else "Simulação de frete indisponível para este item",
                    "delivery_days": None,
                    "delivery_label": "Consultar na loja",
                    "pickup_available": False,
                    "pickup_only": False,
                    "distance_km": None,
                    "distance_is_approximate": True,
                    "seller_location": "",
                    "seller_rating": None,
                    "rating_count": 0,
                    "warranty": "",
                    "image_url": image_url,
                    "url": link,
                    "source_real": True,
                }
            )
            if offer:
                idx = len(offers)
                offers.append(offer)
                if location and len(sim_targets) < self._max_shipping_sims:
                    sim_targets.append((idx, str(item.get("itemId")), seller_id))

        # Simulação paralela de frete e retirada por CEP via API oficial VTEX
        if location and sim_targets:
            with ThreadPoolExecutor(max_workers=min(4, len(sim_targets))) as pool:
                future_map = {
                    pool.submit(self._simulate_shipping, item_id, seller_id, location.cep, location, timeout): idx
                    for idx, item_id, seller_id in sim_targets
                }
                for fut in as_completed(future_map):
                    idx = future_map[fut]
                    try:
                        sim_res = fut.result()
                    except Exception:
                        sim_res = None
                    if not sim_res:
                        continue
                    offer = offers[idx]
                    delivery = sim_res.get("delivery")
                    pickup = sim_res.get("pickup")
                    if delivery:
                        offer["shipping_price"] = delivery["price"]
                        offer["free_shipping"] = delivery["price"] == 0.0
                        offer["shipping_note"] = (
                            f"Frete grátis para {location.cep_formatted}"
                            if delivery["price"] == 0.0
                            else f"{delivery['name']} para {location.cep_formatted}"
                        )
                        if delivery["days"] is not None:
                            offer["delivery_days"] = delivery["days"]
                            offer["delivery_label"] = f"{delivery['days']} dia(s) útil(eis)"
                    if pickup:
                        offer["pickup_available"] = True
                        if sim_res.get("pickup_location"):
                            offer["seller_location"] = f"Retirada: {sim_res['pickup_location']}"
                        if sim_res.get("distance_km") is not None:
                            offer["distance_km"] = sim_res["distance_km"]
                        if not delivery:
                            offer["pickup_only"] = True
                            offer["shipping_price"] = pickup["price"]
                            offer["free_shipping"] = pickup["price"] == 0.0
                            offer["shipping_note"] = "Apenas retirada presencial disponível para este CEP"
                            if pickup["days"] is not None:
                                offer["delivery_days"] = pickup["days"]
                                offer["delivery_label"] = f"Retirada em {pickup['days']} dia(s)"

        return offers


def create_carrefour_provider() -> VtexStoreProvider:
    return VtexStoreProvider(
        provider_id="carrefour",
        store_name="Carrefour Brasil",
        base_url="https://www.carrefour.com.br",
        limitations="Catálogo e frete em tempo real via API oficial VTEX Storefront; cobre eletrônicos, eletrodomésticos, móveis e marketplace.",
        max_shipping_sims=6,
    )


def create_bemol_provider() -> VtexStoreProvider:
    return VtexStoreProvider(
        provider_id="bemol",
        store_name="Bemol",
        base_url="https://www.bemol.com.br",
        limitations="Catálogo e simulação de frete/retirada via API oficial VTEX; entrega para regiões atendidas pela Bemol.",
        max_shipping_sims=4,
    )


def create_electrolux_provider() -> VtexStoreProvider:
    return VtexStoreProvider(
        provider_id="electrolux",
        store_name="Loja Electrolux",
        base_url="https://loja.electrolux.com.br",
        limitations="Loja oficial de eletrodomésticos e portáteis Electrolux com simulação real de frete por CEP.",
        max_shipping_sims=4,
    )
