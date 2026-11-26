"""Serviço de CEP, geolocalização aproximada e cálculo de distância para o Price Hunter."""

from __future__ import annotations

import json
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any

from .security import format_cep, normalize_cep

USER_AGENT = "PriceHunter/1.0 (Comparador de Precos e Oportunidades por CEP)"

# Coordenadas aproximadas de capitais/centros estaduais apenas como fallback de distância aproximada
STATE_APPROX_COORDS: dict[str, tuple[float, float]] = {
    "AC": (-9.97499, -67.8243),
    "AL": (-9.66599, -35.735),
    "AM": (-3.11903, -60.0217),
    "AP": (0.03493, -51.0694),
    "BA": (-12.9714, -38.5014),
    "CE": (-3.71722, -38.5433),
    "DF": (-15.7939, -47.8828),
    "ES": (-20.3155, -40.3128),
    "GO": (-16.6869, -49.2648),
    "MA": (-2.53073, -44.3068),
    "MG": (-19.9167, -43.9345),
    "MS": (-20.4697, -54.6201),
    "MT": (-15.6014, -56.0979),
    "PA": (-1.45583, -48.5039),
    "PB": (-7.1195, -34.845),
    "PE": (-8.04756, -34.877),
    "PI": (-5.09194, -42.8034),
    "PR": (-25.4284, -49.2733),
    "RJ": (-22.9068, -43.1729),
    "RN": (-5.79448, -35.211),
    "RO": (-8.76194, -63.9039),
    "RR": (2.82351, -60.6758),
    "RS": (-30.0346, -51.2177),
    "SC": (-27.5954, -48.548),
    "SE": (-10.9472, -37.0731),
    "SP": (-23.5505, -46.6333),
    "TO": (-10.184, -48.3336),
}


@dataclass
class LocationInfo:
    cep: str
    cep_formatted: str
    street: str
    neighborhood: str
    city: str
    state: str
    ibge: str = ""
    lat: float | None = None
    lng: float | None = None
    coordinates_source: str = "unavailable"
    coordinates_approximate: bool = True
    address_source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LocationService:
    """Consulta CEP brasileiro via ViaCEP/BrasilAPI e coordenadas aproximadas via AwesomeAPI/Nominatim."""

    def __init__(self, timeout: float = 6.0) -> None:
        self.timeout = timeout
        self._cache: dict[str, tuple[float, LocationInfo]] = {}
        self._lock = threading.Lock()
        self._ttl = 86400  # 24 horas

    def _http_json(self, url: str) -> dict[str, Any] | list[Any] | None:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status != 200:
                    return None
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            return None

    def lookup_cep(self, raw_cep: str) -> LocationInfo:
        cep = normalize_cep(raw_cep)
        if not cep:
            raise ValueError("CEP inválido. Informe um CEP brasileiro com 8 dígitos (ex.: 01310-100).")

        now = time.time()
        with self._lock:
            cached = self._cache.get(cep)
            if cached and (now - cached[0]) < self._ttl:
                return cached[1]

        info: LocationInfo | None = None

        # 1. ViaCEP (primário para dados postais brasileiros)
        viacep = self._http_json(f"https://viacep.com.br/ws/{cep}/json/")
        if isinstance(viacep, dict) and not viacep.get("erro") and viacep.get("localidade"):
            info = LocationInfo(
                cep=cep,
                cep_formatted=format_cep(cep),
                street=viacep.get("logradouro", "") or "",
                neighborhood=viacep.get("bairro", "") or "",
                city=viacep.get("localidade", "") or "",
                state=(viacep.get("uf", "") or "").upper(),
                ibge=viacep.get("ibge", "") or "",
                address_source="ViaCEP",
            )

        # 2. Fallback: BrasilAPI v2 (endereço + possíveis coordenadas)
        if info is None:
            bapi = self._http_json(f"https://brasilapi.com.br/api/cep/v2/{cep}")
            if isinstance(bapi, dict) and bapi.get("city"):
                coords = (bapi.get("location") or {}).get("coordinates") or {}
                lat = _safe_float(coords.get("latitude"))
                lng = _safe_float(coords.get("longitude"))
                info = LocationInfo(
                    cep=cep,
                    cep_formatted=format_cep(cep),
                    street=bapi.get("street", "") or "",
                    neighborhood=bapi.get("neighborhood", "") or "",
                    city=bapi.get("city", "") or "",
                    state=(bapi.get("state", "") or "").upper(),
                    ibge=str((bapi.get("ibge") or {}).get("city", "")),
                    lat=lat,
                    lng=lng,
                    coordinates_source="BrasilAPI" if lat is not None and lng is not None else "unavailable",
                    coordinates_approximate=True,
                    address_source="BrasilAPI",
                )

        if info is None:
            raise ValueError(f"CEP {format_cep(cep)} não encontrado nas bases oficiais (ViaCEP/BrasilAPI).")

        # 3. Coordenadas aproximadas via AwesomeAPI CEP (nunca assume que CEP dá ponto exato)
        if info.lat is None or info.lng is None:
            aw = self._http_json(f"https://cep.awesomeapi.com.br/json/{cep}")
            if isinstance(aw, dict):
                lat = _safe_float(aw.get("lat"))
                lng = _safe_float(aw.get("lng"))
                if lat is not None and lng is not None:
                    info.lat = lat
                    info.lng = lng
                    info.coordinates_source = "AwesomeAPI CEP (aproximado)"
                    info.coordinates_approximate = True

        # 4. Fallback Nominatim (OpenStreetMap)
        if info.lat is None or info.lng is None:
            q = urllib.parse.urlencode({"postalcode": info.cep_formatted, "country": "Brazil", "format": "json", "limit": 1})
            nom = self._http_json(f"https://nominatim.openstreetmap.org/search?{q}")
            if isinstance(nom, list) and nom:
                lat = _safe_float(nom[0].get("lat"))
                lng = _safe_float(nom[0].get("lon"))
                if lat is not None and lng is not None:
                    info.lat = lat
                    info.lng = lng
                    info.coordinates_source = "OpenStreetMap Nominatim (aproximado)"
                    info.coordinates_approximate = True

        with self._lock:
            self._cache[cep] = (now, info)
        return info


def _safe_float(val: Any) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Distância aproximada em linha reta (km) entre duas coordenadas geográficas."""
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlng / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return round(r * c, 1)


def sanitize_seller_location(
    city: str | None = None,
    state: str | None = None,
    neighborhood: str | None = None,
) -> str:
    """Exibe apenas região pública (bairro/cidade/UF), nunca endereço completo de vendedor particular."""
    parts = [p.strip() for p in (neighborhood, city, state) if p and str(p).strip()]
    return ", ".join(parts[:3])
