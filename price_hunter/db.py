"""Camada de persistência SQLite (usuários, sessões, favoritos, histórico de preços e buscas)."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import settings

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    preferred_cep TEXT DEFAULT '',
    preferences_json TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token_hash);

CREATE TABLE IF NOT EXISTS password_resets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at INTEGER NOT NULL,
    used INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS favorites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    offer_id TEXT NOT NULL,
    product_key TEXT NOT NULL,
    product_title TEXT NOT NULL,
    brand TEXT DEFAULT '',
    model TEXT DEFAULT '',
    store TEXT NOT NULL,
    observed_price REAL NOT NULL,
    shipping_price REAL,
    final_price REAL,
    original_url TEXT NOT NULL,
    image_url TEXT DEFAULT '',
    condition TEXT NOT NULL DEFAULT 'new',
    cep_used TEXT DEFAULT '',
    opportunity_score INTEGER,
    created_at INTEGER NOT NULL,
    UNIQUE(user_id, original_url),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_favorites_user ON favorites(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_key TEXT NOT NULL,
    offer_url TEXT NOT NULL,
    store TEXT NOT NULL,
    condition TEXT NOT NULL,
    price REAL NOT NULL,
    shipping_price REAL,
    final_price REAL,
    cep_prefix TEXT DEFAULT '',
    observed_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_price_history_key ON price_history(product_key, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_price_history_url ON price_history(offer_url, observed_at DESC);

CREATE TABLE IF NOT EXISTS search_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    query TEXT NOT NULL,
    normalized_query TEXT NOT NULL,
    cep TEXT DEFAULT '',
    mode TEXT NOT NULL DEFAULT 'all',
    result_count INTEGER NOT NULL DEFAULT 0,
    best_price REAL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_search_history_created ON search_history(created_at DESC);
"""


class DuplicateEmailError(Exception):
    """Lançada quando já existe usuário cadastrado com o e-mail informado."""


class Database:
    """Repositório SQLite thread-safe com a mesma interface do FirestoreDatabase."""

    backend_name = "sqlite"

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or settings.db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=15, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = self._connect()
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _init_schema(self) -> None:
        with self.connection() as conn:
            conn.executescript(SCHEMA_SQL)

    @staticmethod
    def _row_to_user(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "id": str(row["id"]),
            "email": row["email"],
            "name": row["name"],
            "password_hash": row["password_hash"],
            "preferred_cep": row["preferred_cep"] or "",
            "preferences": parse_json_safe(row["preferences_json"], {}),
            "created_at": int(row["created_at"]),
            "updated_at": int(row["updated_at"]),
        }

    # --- Usuários, sessões e recuperação de senha ---

    def create_user(
        self,
        email: str,
        name: str,
        password_hash: str,
        preferred_cep: str,
        now: int,
    ) -> dict[str, Any]:
        try:
            with self.connection() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO users (email, name, password_hash, preferred_cep, preferences_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, '{}', ?, ?)
                    """,
                    (email, name, password_hash, preferred_cep, now, now),
                )
                row = conn.execute("SELECT * FROM users WHERE id = ?", (int(cur.lastrowid),)).fetchone()
        except sqlite3.IntegrityError as exc:
            raise DuplicateEmailError("Já existe uma conta cadastrada com este e-mail.") from exc
        return self._row_to_user(row)  # type: ignore[return-value]

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return self._row_to_user(row)

    def get_user_by_id(self, user_id: str | int) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
        return self._row_to_user(row)

    def update_user(
        self,
        user_id: str | int,
        name: str,
        preferred_cep: str,
        preferences: dict[str, Any],
        now: int,
    ) -> dict[str, Any] | None:
        with self.connection() as conn:
            conn.execute(
                """
                UPDATE users
                SET name = ?, preferred_cep = ?, preferences_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (name, preferred_cep, json.dumps(preferences, ensure_ascii=False), now, int(user_id)),
            )
            row = conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
        return self._row_to_user(row)

    def update_user_password(self, user_id: str | int, password_hash: str, now: int) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                (password_hash, now, int(user_id)),
            )

    def create_session(self, user_id: str | int, token_hash: str, expires_at: int, now: int) -> None:
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO sessions (user_id, token_hash, expires_at, created_at) VALUES (?, ?, ?, ?)",
                (int(user_id), token_hash, expires_at, now),
            )
            conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))

    def get_user_by_session_hash(self, token_hash: str, now: int) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT u.* FROM users u
                INNER JOIN sessions s ON s.user_id = u.id
                WHERE s.token_hash = ? AND s.expires_at > ?
                LIMIT 1
                """,
                (token_hash, now),
            ).fetchone()
        return self._row_to_user(row)

    def delete_session(self, token_hash: str) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def delete_user_sessions(self, user_id: str | int) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (int(user_id),))

    def create_password_reset(self, user_id: str | int, token_hash: str, expires_at: int, now: int) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO password_resets (user_id, token_hash, expires_at, used, created_at)
                VALUES (?, ?, ?, 0, ?)
                """,
                (int(user_id), token_hash, expires_at, now),
            )

    def consume_password_reset(self, token_hash: str, now: int) -> str | None:
        with self.connection() as conn:
            rec = conn.execute(
                """
                SELECT id, user_id FROM password_resets
                WHERE token_hash = ? AND used = 0 AND expires_at > ?
                LIMIT 1
                """,
                (token_hash, now),
            ).fetchone()
            if not rec:
                return None
            conn.execute("UPDATE password_resets SET used = 1 WHERE id = ?", (int(rec["id"]),))
            return str(rec["user_id"])

    # --- Histórico de preços ---

    def record_price_snapshots(self, offers: list[dict[str, Any]], cep: str = "") -> None:
        if not offers:
            return
        now = int(time.time())
        recent_cutoff = now - 1800
        cep_prefix = cep[:5] if len(cep) >= 5 else ""
        with self.connection() as conn:
            for offer in offers:
                url = offer.get("url")
                price = offer.get("price")
                product_key = offer.get("product_key")
                if not url or not price or price <= 0 or not product_key:
                    continue
                exists = conn.execute(
                    """
                    SELECT 1 FROM price_history
                    WHERE offer_url = ? AND observed_at >= ? AND ABS(price - ?) < 0.01
                    LIMIT 1
                    """,
                    (url, recent_cutoff, float(price)),
                ).fetchone()
                if exists:
                    continue
                conn.execute(
                    """
                    INSERT INTO price_history
                    (product_key, offer_url, store, condition, price, shipping_price, final_price, cep_prefix, observed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        product_key,
                        url,
                        offer.get("store", ""),
                        offer.get("condition", "new"),
                        float(price),
                        float(offer["shipping_price"]) if offer.get("shipping_price") is not None else None,
                        float(offer["final_price"]) if offer.get("final_price") is not None else None,
                        cep_prefix,
                        now,
                    ),
                )

    def get_price_history_stats(
        self,
        product_key: str,
        current_price: float | None = None,
        offer_url: str | None = None,
    ) -> dict[str, Any]:
        with self.connection() as conn:
            if offer_url:
                rows = conn.execute(
                    """
                    SELECT price, observed_at, store FROM price_history
                    WHERE offer_url = ?
                    ORDER BY observed_at ASC
                    LIMIT 120
                    """,
                    (offer_url,),
                ).fetchall()
            else:
                rows = []
            if len(rows) < 2 and product_key:
                rows = conn.execute(
                    """
                    SELECT price, observed_at, store FROM price_history
                    WHERE product_key = ?
                    ORDER BY observed_at ASC
                    LIMIT 120
                    """,
                    (product_key,),
                ).fetchall()

        points = [
            {"price": round(float(r["price"]), 2), "observed_at": int(r["observed_at"]), "store": r["store"]}
            for r in rows
        ]
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
        uid = int(user_id) if user_id is not None else None
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO search_history
                (user_id, query, normalized_query, cep, mode, result_count, best_price, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (uid, query, normalized_query, cep, mode, result_count, best_price, now),
            )

    def get_recent_searches(self, user_id: str | int | None = None, limit: int = 8) -> list[dict[str, Any]]:
        with self.connection() as conn:
            if user_id is not None:
                rows = conn.execute(
                    """
                    SELECT query, cep, mode, result_count, best_price, MAX(created_at) AS last_at
                    FROM search_history
                    WHERE user_id = ?
                    GROUP BY LOWER(query), mode
                    ORDER BY last_at DESC
                    LIMIT ?
                    """,
                    (int(user_id), limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT query, cep, mode, result_count, best_price, MAX(created_at) AS last_at
                    FROM search_history
                    WHERE result_count > 0
                    GROUP BY LOWER(query), mode
                    ORDER BY last_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        return [
            {
                "query": r["query"],
                "cep": r["cep"],
                "mode": r["mode"],
                "result_count": r["result_count"],
                "best_price": r["best_price"],
                "last_at": r["last_at"],
            }
            for r in rows
        ]

    # --- Favoritos ---

    def list_favorites(self, user_id: str | int) -> list[dict[str, Any]]:
        uid = int(user_id)
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM favorites WHERE user_id = ? ORDER BY created_at DESC",
                (uid,),
            ).fetchall()
        items = []
        for r in rows:
            d = dict(r)
            d["id"] = str(d["id"])
            d["user_id"] = str(d["user_id"])
            with self.connection() as conn:
                latest = conn.execute(
                    """
                    SELECT price, observed_at FROM price_history
                    WHERE offer_url = ?
                    ORDER BY observed_at DESC
                    LIMIT 1
                    """,
                    (r["original_url"],),
                ).fetchone()
            if latest:
                d["latest_observed_price"] = round(float(latest["price"]), 2)
                d["latest_observed_at"] = int(latest["observed_at"])
                d["price_delta"] = round(float(latest["price"]) - float(r["observed_price"]), 2)
            else:
                d["latest_observed_price"] = d["observed_price"]
                d["latest_observed_at"] = d["created_at"]
                d["price_delta"] = 0.0
            items.append(d)
        return items

    def add_favorite(self, user_id: str | int, data: dict[str, Any]) -> dict[str, Any]:
        uid = int(user_id)
        now = int(time.time())
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO favorites
                (user_id, offer_id, product_key, product_title, brand, model, store,
                 observed_price, shipping_price, final_price, original_url, image_url,
                 condition, cep_used, opportunity_score, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, original_url) DO UPDATE SET
                    observed_price = excluded.observed_price,
                    shipping_price = excluded.shipping_price,
                    final_price = excluded.final_price,
                    cep_used = excluded.cep_used,
                    opportunity_score = excluded.opportunity_score,
                    created_at = excluded.created_at
                """,
                (
                    uid,
                    data["offer_id"],
                    data["product_key"],
                    data["product_title"],
                    data.get("brand", ""),
                    data.get("model", ""),
                    data["store"],
                    float(data["observed_price"]),
                    float(data["shipping_price"]) if data.get("shipping_price") is not None else None,
                    float(data["final_price"]) if data.get("final_price") is not None else None,
                    data["original_url"],
                    data.get("image_url", ""),
                    data.get("condition", "new"),
                    data.get("cep_used", ""),
                    int(data["opportunity_score"]) if data.get("opportunity_score") is not None else None,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM favorites WHERE user_id = ? AND original_url = ?",
                (uid, data["original_url"]),
            ).fetchone()
        out = dict(row)
        out["id"] = str(out["id"])
        out["user_id"] = str(out["user_id"])
        return out

    def delete_favorite(self, user_id: str | int, favorite_id: str | int) -> bool:
        try:
            fid = int(favorite_id)
        except (TypeError, ValueError):
            return False
        with self.connection() as conn:
            cur = conn.execute(
                "DELETE FROM favorites WHERE id = ? AND user_id = ?",
                (fid, int(user_id)),
            )
            return cur.rowcount > 0

    def get_recent_observed_highlights(self, limit: int = 4) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT ph.product_key, ph.offer_url, ph.store, ph.condition,
                       ph.price, ph.final_price, ph.observed_at
                FROM price_history ph
                INNER JOIN (
                    SELECT offer_url, MAX(observed_at) AS max_at
                    FROM price_history
                    GROUP BY offer_url
                ) latest ON ph.offer_url = latest.offer_url AND ph.observed_at = latest.max_at
                ORDER BY ph.observed_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


def summarize_price_points(
    points: list[dict[str, Any]],
    current_price: float | None = None,
) -> dict[str, Any]:
    """Estatísticas reais de histórico compartilhadas entre SQLite e Firestore (nunca inventa dados)."""
    ordered = sorted(points, key=lambda p: int(p["observed_at"]))
    distinct_times = len({int(p["observed_at"]) // 300 for p in ordered})
    if len(ordered) < 2 or distinct_times < 2:
        return {
            "status": "forming",
            "message": "Histórico em formação — novos registros são coletados a cada pesquisa real.",
            "observations": len(ordered),
            "current_price": round(current_price, 2)
            if current_price is not None
            else (ordered[-1]["price"] if ordered else None),
            "min_price": None,
            "max_price": None,
            "avg_price": None,
            "variation_percent": None,
            "points": ordered,
        }

    prices = [float(p["price"]) for p in ordered]
    cur = round(current_price, 2) if current_price is not None else prices[-1]
    min_p = min(prices)
    max_p = max(prices)
    avg_p = round(sum(prices) / len(prices), 2)
    variation = round(((cur - avg_p) / avg_p) * 100, 1) if avg_p > 0 else 0.0
    return {
        "status": "available",
        "message": f"Baseado em {len(ordered)} observações reais registradas pelo Price Hunter.",
        "observations": len(ordered),
        "current_price": cur,
        "min_price": min_p,
        "max_price": max_p,
        "avg_price": avg_p,
        "variation_percent": variation,
        "points": ordered[-30:],
    }


def parse_json_safe(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default
