"""Camada de persistência Google Cloud Firestore (serverless, 100% Google, sem índices compostos obrigatórios)."""

from __future__ import annotations

import hashlib
import time
from typing import Any

from .config import settings
from .db import DuplicateEmailError, summarize_price_points


def _hash_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


class FirestoreDatabase:
    """
    Repositório persistente sobre Google Cloud Firestore (Native Mode).
    Usa Application Default Credentials (ADC) automaticamente no Google Cloud Run,
    sem chaves estáticas no código.
    """

    backend_name = "firestore"

    def __init__(self, client: Any | None = None) -> None:
        if client is not None:
            self._client = client
        else:
            from google.cloud import firestore  # type: ignore[import-not-found]

            kwargs: dict[str, Any] = {}
            if settings.gcp_project:
                kwargs["project"] = settings.gcp_project
            if settings.firestore_database and settings.firestore_database != "(default)":
                kwargs["database"] = settings.firestore_database
            self._client = firestore.Client(**kwargs)

    # --- Usuários, sessões e recuperação de senha ---

    def create_user(
        self,
        email: str,
        name: str,
        password_hash: str,
        preferred_cep: str,
        now: int,
    ) -> dict[str, Any]:
        uid = _hash_id(email.lower())
        doc_ref = self._client.collection("users").document(uid)
        snap = doc_ref.get()
        if snap.exists:
            raise DuplicateEmailError("Já existe uma conta cadastrada com este e-mail.")
        payload = {
            "id": uid,
            "email": email,
            "name": name,
            "password_hash": password_hash,
            "preferred_cep": preferred_cep,
            "preferences": {},
            "created_at": now,
            "updated_at": now,
        }
        doc_ref.set(payload)
        return dict(payload)

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        uid = _hash_id(email.lower())
        snap = self._client.collection("users").document(uid).get()
        if not snap.exists:
            return None
        data = snap.to_dict() or {}
        data.setdefault("id", snap.id)
        return data

    def get_user_by_id(self, user_id: str | int) -> dict[str, Any] | None:
        snap = self._client.collection("users").document(str(user_id)).get()
        if not snap.exists:
            return None
        data = snap.to_dict() or {}
        data.setdefault("id", snap.id)
        return data

    def update_user(
        self,
        user_id: str | int,
        name: str,
        preferred_cep: str,
        preferences: dict[str, Any],
        now: int,
    ) -> dict[str, Any] | None:
        doc_ref = self._client.collection("users").document(str(user_id))
        snap = doc_ref.get()
        if not snap.exists:
            return None
        doc_ref.update(
            {
                "name": name,
                "preferred_cep": preferred_cep,
                "preferences": preferences,
                "updated_at": now,
            }
        )
        return self.get_user_by_id(user_id)

    def update_user_password(self, user_id: str | int, password_hash: str, now: int) -> None:
        self._client.collection("users").document(str(user_id)).update(
            {"password_hash": password_hash, "updated_at": now}
        )

    def create_session(self, user_id: str | int, token_hash: str, expires_at: int, now: int) -> None:
        self._client.collection("sessions").document(token_hash).set(
            {
                "user_id": str(user_id),
                "token_hash": token_hash,
                "expires_at": expires_at,
                "created_at": now,
            }
        )

    def get_user_by_session_hash(self, token_hash: str, now: int) -> dict[str, Any] | None:
        snap = self._client.collection("sessions").document(token_hash).get()
        if not snap.exists:
            return None
        sess = snap.to_dict() or {}
        if int(sess.get("expires_at", 0)) <= now:
            self.delete_session(token_hash)
            return None
        return self.get_user_by_id(sess.get("user_id", ""))

    def delete_session(self, token_hash: str) -> None:
        self._client.collection("sessions").document(token_hash).delete()

    def delete_user_sessions(self, user_id: str | int) -> None:
        docs = self._client.collection("sessions").where("user_id", "==", str(user_id)).stream()
        for doc in docs:
            doc.reference.delete()

    def create_password_reset(self, user_id: str | int, token_hash: str, expires_at: int, now: int) -> None:
        self._client.collection("password_resets").document(token_hash).set(
            {
                "user_id": str(user_id),
                "token_hash": token_hash,
                "expires_at": expires_at,
                "used": False,
                "created_at": now,
            }
        )

    def consume_password_reset(self, token_hash: str, now: int) -> str | None:
        doc_ref = self._client.collection("password_resets").document(token_hash)
        snap = doc_ref.get()
        if not snap.exists:
            return None
        rec = snap.to_dict() or {}
        if rec.get("used") or int(rec.get("expires_at", 0)) <= now:
            return None
        doc_ref.update({"used": True})
        return str(rec.get("user_id"))

    # --- Histórico de preços ---

    def record_price_snapshots(self, offers: list[dict[str, Any]], cep: str = "") -> None:
        if not offers:
            return
        now = int(time.time())
        bucket = now // 1800  # 1 snapshot por oferta a cada 30 minutos
        cep_prefix = cep[:5] if len(cep) >= 5 else ""
        col = self._client.collection("price_history")
        for offer in offers[:25]:
            url = offer.get("url")
            price = offer.get("price")
            product_key = offer.get("product_key")
            if not url or not price or float(price) <= 0 or not product_key:
                continue
            doc_id = f"{_hash_id(url)}_{bucket}"
            col.document(doc_id).set(
                {
                    "product_key": product_key,
                    "offer_url": url,
                    "url_hash": _hash_id(url),
                    "store": offer.get("store", ""),
                    "condition": offer.get("condition", "new"),
                    "price": round(float(price), 2),
                    "shipping_price": float(offer["shipping_price"]) if offer.get("shipping_price") is not None else None,
                    "final_price": float(offer["final_price"]) if offer.get("final_price") is not None else None,
                    "cep_prefix": cep_prefix,
                    "observed_at": now,
                }
            )

    def get_price_history_stats(
        self,
        product_key: str,
        current_price: float | None = None,
        offer_url: str | None = None,
    ) -> dict[str, Any]:
        col = self._client.collection("price_history")
        points: list[dict[str, Any]] = []
        if offer_url:
            for doc in col.where("url_hash", "==", _hash_id(offer_url)).stream():
                d = doc.to_dict() or {}
                if d.get("price") is not None:
                    points.append(
                        {
                            "price": round(float(d["price"]), 2),
                            "observed_at": int(d.get("observed_at", 0)),
                            "store": d.get("store", ""),
                        }
                    )
        if len(points) < 2 and product_key:
            points = []
            for doc in col.where("product_key", "==", product_key).stream():
                d = doc.to_dict() or {}
                if d.get("price") is not None:
                    points.append(
                        {
                            "price": round(float(d["price"]), 2),
                            "observed_at": int(d.get("observed_at", 0)),
                            "store": d.get("store", ""),
                        }
                    )
        return summarize_price_points(points, current_price)

    # --- Pesquisas recentes ---

    def log_search(
        self,
        query: str,
        normalized_query: str,
        cep: str,
        mode: str,
        result_count: int,
        best_price: float | None,
        user_id: str | int | None = None,
    ) -> None:
        now = int(time.time())
        scope = str(user_id) if user_id is not None else "public"
        doc_id = _hash_id(f"{scope}:{query.lower()}:{mode}")
        self._client.collection("search_history").document(doc_id).set(
            {
                "user_id": str(user_id) if user_id is not None else None,
                "query": query,
                "normalized_query": normalized_query,
                "cep": cep,
                "mode": mode,
                "result_count": result_count,
                "best_price": best_price,
                "last_at": now,
            }
        )

    def get_recent_searches(self, user_id: str | int | None = None, limit: int = 8) -> list[dict[str, Any]]:
        col = self._client.collection("search_history")
        if user_id is not None:
            docs = col.where("user_id", "==", str(user_id)).stream()
        else:
            docs = col.stream()
        items: list[dict[str, Any]] = []
        for doc in docs:
            d = doc.to_dict() or {}
            if user_id is None and int(d.get("result_count", 0)) <= 0:
                continue
            items.append(
                {
                    "query": d.get("query", ""),
                    "cep": d.get("cep", ""),
                    "mode": d.get("mode", "all"),
                    "result_count": int(d.get("result_count", 0)),
                    "best_price": d.get("best_price"),
                    "last_at": int(d.get("last_at", 0)),
                }
            )
        items.sort(key=lambda x: x["last_at"], reverse=True)
        return items[:limit]

    # --- Favoritos ---

    def list_favorites(self, user_id: str | int) -> list[dict[str, Any]]:
        uid = str(user_id)
        docs = self._client.collection("favorites").where("user_id", "==", uid).stream()
        items: list[dict[str, Any]] = []
        for doc in docs:
            d = doc.to_dict() or {}
            d["id"] = doc.id
            url = d.get("original_url", "")
            latest_price = d.get("observed_price")
            latest_at = d.get("created_at")
            if url:
                hist_docs = self._client.collection("price_history").where("url_hash", "==", _hash_id(url)).stream()
                latest_hist = None
                for h in hist_docs:
                    hd = h.to_dict() or {}
                    if latest_hist is None or int(hd.get("observed_at", 0)) > int(latest_hist.get("observed_at", 0)):
                        latest_hist = hd
                if latest_hist and latest_hist.get("price") is not None:
                    latest_price = round(float(latest_hist["price"]), 2)
                    latest_at = int(latest_hist.get("observed_at", latest_at or 0))
            d["latest_observed_price"] = latest_price
            d["latest_observed_at"] = latest_at
            d["price_delta"] = round(float(latest_price) - float(d["observed_price"]), 2)
            items.append(d)
        items.sort(key=lambda x: int(x.get("created_at", 0)), reverse=True)
        return items

    def add_favorite(self, user_id: str | int, data: dict[str, Any]) -> dict[str, Any]:
        uid = str(user_id)
        now = int(time.time())
        fav_id = f"{uid[:12]}_{_hash_id(data['original_url'])[:16]}"
        payload = {
            "id": fav_id,
            "user_id": uid,
            "offer_id": data["offer_id"],
            "product_key": data["product_key"],
            "product_title": data["product_title"],
            "brand": data.get("brand", ""),
            "model": data.get("model", ""),
            "store": data["store"],
            "observed_price": float(data["observed_price"]),
            "shipping_price": float(data["shipping_price"]) if data.get("shipping_price") is not None else None,
            "final_price": float(data["final_price"]) if data.get("final_price") is not None else None,
            "original_url": data["original_url"],
            "image_url": data.get("image_url", ""),
            "condition": data.get("condition", "new"),
            "cep_used": data.get("cep_used", ""),
            "opportunity_score": int(data["opportunity_score"]) if data.get("opportunity_score") is not None else None,
            "created_at": now,
        }
        self._client.collection("favorites").document(fav_id).set(payload)
        return payload

    def delete_favorite(self, user_id: str | int, favorite_id: str | int) -> bool:
        doc_ref = self._client.collection("favorites").document(str(favorite_id))
        snap = doc_ref.get()
        if not snap.exists:
            return False
        data = snap.to_dict() or {}
        if str(data.get("user_id")) != str(user_id):
            return False
        doc_ref.delete()
        return True

    def get_recent_observed_highlights(self, limit: int = 4) -> list[dict[str, Any]]:
        latest_by_url: dict[str, dict[str, Any]] = {}
        for doc in self._client.collection("price_history").stream():
            d = doc.to_dict() or {}
            url = d.get("offer_url")
            if not url:
                continue
            prev = latest_by_url.get(url)
            if prev is None or int(d.get("observed_at", 0)) > int(prev.get("observed_at", 0)):
                latest_by_url[url] = {
                    "product_key": d.get("product_key", ""),
                    "offer_url": url,
                    "store": d.get("store", ""),
                    "condition": d.get("condition", "new"),
                    "price": d.get("price"),
                    "final_price": d.get("final_price"),
                    "observed_at": int(d.get("observed_at", 0)),
                }
        ordered = sorted(latest_by_url.values(), key=lambda x: x["observed_at"], reverse=True)
        return ordered[:limit]
