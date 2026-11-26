"""Testes automatizados unitários e de integração da API do Price Hunter."""

from __future__ import annotations

import http.cookiejar
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

os.environ["PRICE_HUNTER_ENV"] = "test"

from price_hunter.db import Database
from price_hunter.firestore_db import FirestoreDatabase
from price_hunter.location import LocationInfo, LocationService, haversine_km, sanitize_seller_location
from price_hunter.normalizer import analyze_query, enrich_and_deduplicate_offers
from price_hunter.providers.base import BaseProvider, ProviderInfo
from price_hunter.providers.registry import ProviderRegistry
from price_hunter.scoring import normalize_weights, score_and_rank_offers
from price_hunter.security import (
    SlidingWindowRateLimiter,
    hash_password,
    is_safe_http_url,
    normalize_cep,
    verify_password,
)
from price_hunter.server import PriceHunterApp, create_server


class FakeLocationService(LocationService):
    def lookup_cep(self, raw_cep: str) -> LocationInfo:
        cep = normalize_cep(raw_cep)
        if not cep or cep == "00000000":
            raise ValueError("CEP inválido ou não encontrado.")
        return LocationInfo(
            cep=cep,
            cep_formatted=f"{cep[:5]}-{cep[5:]}",
            street="Avenida Paulista",
            neighborhood="Bela Vista",
            city="São Paulo",
            state="SP",
            ibge="3550308",
            lat=-23.5632,
            lng=-46.6542,
            coordinates_source="TestFixture",
            coordinates_approximate=True,
            address_source="TestFixture",
        )


class StubGoodProvider(BaseProvider):
    def info(self) -> ProviderInfo:
        return ProviderInfo(id="stub_good", name="Loja Teste A", status="active", supports_new=True, supports_used=True, supports_cep_shipping=True)

    def search(self, query, location=None, mode="all", limit=12, timeout=10.0):
        return [
            {
                "id": "stub_1",
                "provider_id": "stub_good",
                "store": "Loja Teste A",
                "seller_name": "Loja Teste A (Venda direta)",
                "is_marketplace": False,
                "title": "Processador AMD Ryzen 5 5600G 3.9GHz AM4",
                "brand": "AMD",
                "category": "Componentes de Informática",
                "condition": "new",
                "price": 789.90,
                "shipping_price": 0.0,
                "free_shipping": True,
                "delivery_days": 2,
                "delivery_label": "2 dias úteis",
                "pickup_available": True,
                "pickup_only": False,
                "distance_km": 4.2,
                "seller_rating": 4.8,
                "rating_count": 42,
                "warranty": "12 meses de garantia",
                "image_url": "",
                "url": "https://loja-a.example.com/ryzen-5-5600g",
            },
            {
                "id": "stub_2",
                "provider_id": "stub_good",
                "store": "Loja Teste B",
                "seller_name": "Parceiro B",
                "is_marketplace": True,
                "title": "Processador AMD Ryzen 5 5600G Box",
                "brand": "AMD",
                "category": "Componentes de Informática",
                "condition": "new",
                "price": 749.90,
                "shipping_price": 29.90,
                "free_shipping": False,
                "delivery_days": 7,
                "delivery_label": "7 dias úteis",
                "pickup_available": False,
                "pickup_only": False,
                "distance_km": None,
                "seller_rating": 4.5,
                "rating_count": 15,
                "warranty": "12 meses",
                "image_url": "",
                "url": "https://loja-b.example.com/ryzen-5-5600g",
            },
        ]


class StubFailingProvider(BaseProvider):
    def info(self) -> ProviderInfo:
        return ProviderInfo(id="stub_fail", name="Loja Falha", status="active", supports_new=True, supports_used=True)

    def search(self, query, location=None, mode="all", limit=12, timeout=10.0):
        raise TimeoutError("Simulated provider timeout")


class UnitTests(unittest.TestCase):
    def test_security_helpers(self):
        self.assertEqual(normalize_cep("01310-100"), "01310100")
        self.assertEqual(normalize_cep("1234"), "")
        self.assertTrue(is_safe_http_url("https://www.kabum.com.br/produto/123"))
        self.assertFalse(is_safe_http_url("javascript:alert(1)"))
        self.assertFalse(is_safe_http_url("http://localhost"))

        pw_hash = hash_password("SenhaForte123")
        self.assertNotIn("SenhaForte123", pw_hash)
        self.assertTrue(verify_password("SenhaForte123", pw_hash))
        self.assertFalse(verify_password("OutraSenha123", pw_hash))

        limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=60)
        self.assertTrue(limiter.allow("1.2.3.4")[0])
        self.assertTrue(limiter.allow("1.2.3.4")[0])
        self.assertFalse(limiter.allow("1.2.3.4")[0])

    def test_location_distance_and_privacy(self):
        # SP Paulista -> SP Centro (~3 km)
        dist = haversine_km(-23.5632, -46.6542, -23.5505, -46.6333)
        self.assertGreater(dist, 1.5)
        self.assertLess(dist, 5.0)
        self.assertEqual(sanitize_seller_location(city="São Paulo", state="SP", neighborhood="Bela Vista"), "Bela Vista, São Paulo, SP")

    def test_normalizer_equivalence_and_accessories(self):
        analysis = analyze_query("Ryzen 5 5600G")
        raw_offers = [
            {"id": "1", "store": "KaBuM!", "title": "Processador AMD Ryzen 5 5600G 3.9GHz", "price": 800.0, "url": "https://a.com/1"},
            {"id": "2", "store": "KaBuM!", "title": "Processador AMD Ryzen 5 5600G 3.9GHz", "price": 800.0, "url": "https://a.com/1"},  # duplicado
            {"id": "3", "store": "KaBuM!", "title": "Processador AMD Ryzen 5 3600", "price": 599.0, "url": "https://a.com/2"},  # modelo diferente!
            {"id": "4", "store": "Carrefour", "title": "Pasta Térmica para Ryzen 5 5600G", "price": 29.9, "url": "https://a.com/3"},  # acessório
        ]
        offers, _ = enrich_and_deduplicate_offers(raw_offers, analysis)
        self.assertEqual(len(offers), 2)  # duplicado e acessório removidos
        by_id = {o["id"]: o for o in offers}
        self.assertEqual(by_id["1"]["equivalence_status"], "exact")
        self.assertEqual(by_id["3"]["equivalence_status"], "variant")
        self.assertTrue(len(by_id["3"]["model_differences"]) >= 1)

    def test_opportunity_score_rules_and_tradeoff(self):
        w_new = normalize_weights(None, is_used_profile=False)
        w_used = normalize_weights(None, is_used_profile=True)
        self.assertAlmostEqual(sum(w_new.values()), 100.0, places=0)
        self.assertGreater(w_used["proximity"], w_new["proximity"])

        offers = [
            {
                "id": "fast",
                "store": "Carrefour Brasil",
                "seller_name": "Carrefour Brasil (Venda direta)",
                "is_marketplace": False,
                "title": "Ryzen 5 5600G",
                "condition": "new",
                "price": 780.0,
                "shipping_price": 0.0,
                "free_shipping": True,
                "delivery_days": 2,
                "seller_rating": 4.8,
                "rating_count": 30,
                "warranty": "12 meses",
                "equivalence_status": "exact",
            },
            {
                "id": "cheap_slow",
                "store": "Parceiro X",
                "seller_name": "Parceiro X",
                "is_marketplace": True,
                "title": "Ryzen 5 5600G",
                "condition": "new",
                "price": 720.0,
                "shipping_price": 20.0,
                "free_shipping": False,
                "delivery_days": 8,
                "seller_rating": 2.0,  # baixa reputação: não pode virar 'Excelente oportunidade'!
                "rating_count": 20,
                "warranty": "Sem garantia",
                "equivalence_status": "exact",
            },
        ]
        ranked, meta = score_and_rank_offers(offers, mode="new")
        by_id = {o["id"]: o for o in ranked}
        self.assertEqual(ranked[0]["id"], "fast")
        self.assertNotEqual(by_id["cheap_slow"]["opportunity_tier"], "excellent")
        self.assertIsNotNone(meta["tradeoff_summary"])
        self.assertEqual(meta["tradeoff_summary"]["type"], "speed_vs_price")

    def test_price_history_never_fabricated(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "test.db")
            db.record_price_snapshots(
                [{"product_key": "amd-5600g", "url": "https://loja.com/p1", "store": "KaBuM!", "condition": "new", "price": 800.0}],
                cep="01310100",
            )
            stats = db.get_price_history_stats("amd-5600g", current_price=800.0, offer_url="https://loja.com/p1")
            self.assertEqual(stats["status"], "forming")

            # Insere segunda observação real em timestamp anterior (>= 10 min atrás)
            with db.connection() as conn:
                conn.execute(
                    "INSERT INTO price_history (product_key, offer_url, store, condition, price, observed_at) VALUES (?, ?, ?, ?, ?, ?)",
                    ("amd-5600g", "https://loja.com/p1", "KaBuM!", "new", 880.0, int(time.time()) - 3600),
                )
            stats2 = db.get_price_history_stats("amd-5600g", current_price=800.0, offer_url="https://loja.com/p1")
            self.assertEqual(stats2["status"], "available")
            self.assertEqual(stats2["min_price"], 800.0)
            self.assertEqual(stats2["max_price"], 880.0)


class ApiIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db = Database(Path(self.tmp.name) / "api.db")
        registry = ProviderRegistry(providers=[StubGoodProvider(), StubFailingProvider()])
        app = PriceHunterApp(db=db, location_service=FakeLocationService(), registry=registry)
        self.server = create_server(host="127.0.0.1", port=0, app=app)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def _make_client(self):
        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

        def call(method: str, path: str, body: dict | None = None):
            data = json.dumps(body).encode("utf-8") if body is not None else None
            headers = {"Accept": "application/json"}
            if data is not None:
                headers["Content-Type"] = "application/json"
            req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, headers=headers, method=method)
            try:
                with opener.open(req, timeout=5) as resp:
                    return resp.status, json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read().decode("utf-8"))

        return call

    def test_search_provider_isolation_and_invalid_cep(self):
        call = self._make_client()
        st, err = call("GET", "/api/search?q=ryzen+5+5600g&cep=123")
        self.assertEqual(st, 400)
        self.assertIn("CEP", err["error"])

        st, res = call("GET", "/api/search?q=ryzen+5+5600g&cep=01310-100")
        self.assertEqual(st, 200)
        self.assertEqual(len(res["offers"]), 2)
        self.assertTrue(res["providers"]["partial"])
        self.assertEqual(len(res["providers"]["failed"]), 1)
        self.assertTrue(any("parciais" in w.lower() for w in res["warnings"]))

    def test_auth_profile_reset_and_favorites_isolation(self):
        c1 = self._make_client()
        c2 = self._make_client()

        # Rota protegida exige login
        st, _ = c1("GET", "/api/favorites")
        self.assertEqual(st, 401)

        # Registro + sessão
        st, reg = c1("POST", "/api/auth/register", {
            "name": "Maria Silva",
            "email": "maria@example.com",
            "password": "Password123",
            "preferred_cep": "01310-100",
        })
        self.assertEqual(st, 201)
        self.assertEqual(reg["user"]["preferred_cep"], "01310100")

        # Perfil
        st, prof = c1("PATCH", "/api/auth/profile", {
            "name": "Maria S.",
            "preferences": {"default_mode": "new", "max_budget": 1500},
        })
        self.assertEqual(st, 200)
        self.assertEqual(prof["user"]["preferences"]["max_budget"], 1500.0)

        # Favoritos
        st, fav = c1("POST", "/api/favorites", {
            "offer_id": "stub_1",
            "product_key": "amd-5600g",
            "product_title": "Processador AMD Ryzen 5 5600G",
            "store": "Loja Teste A",
            "observed_price": 789.90,
            "original_url": "https://loja-a.example.com/ryzen-5-5600g",
            "cep_used": "01310100",
        })
        self.assertEqual(st, 201)
        fav_id = fav["favorite"]["id"]

        # Usuário 2 não pode excluir favorito do Usuário 1
        st, _ = c2("POST", "/api/auth/register", {
            "name": "João Costa",
            "email": "joao@example.com",
            "password": "Password456",
        })
        self.assertEqual(st, 201)
        st, _ = c2("DELETE", f"/api/favorites/{fav_id}")
        self.assertEqual(st, 404)

        # Recuperação de senha completa
        st, forgot = c1("POST", "/api/auth/forgot-password", {"email": "maria@example.com"})
        self.assertEqual(st, 200)
        self.assertIn("dev_reset_token", forgot)

        st, reset = c1("POST", "/api/auth/reset-password", {
            "token": forgot["dev_reset_token"],
            "new_password": "NovaSenha999",
        })
        self.assertEqual(st, 200)

        st, _ = c1("POST", "/api/auth/login", {"email": "maria@example.com", "password": "NovaSenha999"})
        self.assertEqual(st, 200)


class _FakeDocSnapshot:
    def __init__(self, doc_id: str, data: dict | None, ref) -> None:
        self.id = doc_id
        self._data = dict(data) if data is not None else None
        self.reference = ref

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class _FakeDocRef:
    def __init__(self, store: dict[str, dict], doc_id: str) -> None:
        self._store = store
        self.id = doc_id

    def get(self):
        return _FakeDocSnapshot(self.id, self._store.get(self.id), self)

    def set(self, data: dict):
        self._store[self.id] = dict(data)

    def update(self, data: dict):
        self._store[self.id].update(data)

    def delete(self):
        self._store.pop(self.id, None)


class _FakeCollection:
    def __init__(self, store: dict[str, dict], filters=None) -> None:
        self._store = store
        self._filters = list(filters or [])

    def document(self, doc_id: str):
        return _FakeDocRef(self._store, doc_id)

    def where(self, field: str, op: str, value):
        return _FakeCollection(self._store, [*self._filters, (field, op, value)])

    def stream(self):
        for doc_id, data in list(self._store.items()):
            ok = True
            for field, op, val in self._filters:
                if op == "==" and data.get(field) != val:
                    ok = False
                    break
            if ok:
                yield _FakeDocSnapshot(doc_id, data, _FakeDocRef(self._store, doc_id))


class _FakeFirestoreClient:
    def __init__(self) -> None:
        self._collections: dict[str, dict[str, dict]] = {}

    def collection(self, name: str):
        return _FakeCollection(self._collections.setdefault(name, {}))


class FirestoreIntegrationTests(unittest.TestCase):
    def test_full_api_flow_on_firestore_backend(self):
        fs_db = FirestoreDatabase(client=_FakeFirestoreClient())
        registry = ProviderRegistry(providers=[StubGoodProvider()])
        app = PriceHunterApp(db=fs_db, location_service=FakeLocationService(), registry=registry)
        server = create_server(host="127.0.0.1", port=0, app=app)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            cj = http.cookiejar.CookieJar()
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

            def call(method: str, path: str, body: dict | None = None):
                data = json.dumps(body).encode("utf-8") if body is not None else None
                headers = {"Accept": "application/json"}
                if data is not None:
                    headers["Content-Type"] = "application/json"
                req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, headers=headers, method=method)
                try:
                    with opener.open(req, timeout=5) as resp:
                        return resp.status, json.loads(resp.read().decode("utf-8"))
                except urllib.error.HTTPError as e:
                    return e.code, json.loads(e.read().decode("utf-8"))

            st, health = call("GET", "/api/health")
            self.assertEqual(st, 200)
            self.assertEqual(health["db_backend"], "firestore")

            st, reg = call("POST", "/api/auth/register", {
                "name": "Cloud User",
                "email": "cloud@example.com",
                "password": "Password123",
                "preferred_cep": "01310-100",
            })
            self.assertEqual(st, 201)
            self.assertEqual(reg["user"]["auth_backend"], "firestore")

            st, search = call("GET", "/api/search?q=ryzen+5+5600g&cep=01310-100")
            self.assertEqual(st, 200)
            self.assertEqual(len(search["offers"]), 2)

            st, fav = call("POST", "/api/favorites", {
                "offer_id": "stub_1",
                "product_key": "amd-5600g",
                "product_title": "Processador AMD Ryzen 5 5600G",
                "store": "Loja Teste A",
                "observed_price": 789.90,
                "original_url": "https://loja-a.example.com/ryzen-5-5600g",
                "cep_used": "01310100",
            })
            self.assertEqual(st, 201)
            fav_id = fav["favorite"]["id"]

            st, favs = call("GET", "/api/favorites")
            self.assertEqual(st, 200)
            self.assertEqual(len(favs["favorites"]), 1)

            st, deleted = call("DELETE", f"/api/favorites/{fav_id}")
            self.assertEqual(st, 200)
            self.assertTrue(deleted["deleted"])
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
