"""Servidor HTTP + API REST do Price Hunter (sem dependências externas obrigatórias)."""

from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .auth_service import AuthError, AuthService, get_default_weights_info
from .config import BASE_DIR, settings
from .db import Database
from .location import LocationService
from .providers import ProviderRegistry
from .search_service import SearchService
from .security import SlidingWindowRateLimiter, is_safe_http_url, normalize_cep

STATIC_DIR = BASE_DIR / "static"
SESSION_COOKIE_NAME = "ph_session"


def create_database() -> Any:
    """Seleciona Google Cloud Firestore em produção/Cloud Run ou SQLite localmente."""
    if settings.db_backend == "firestore":
        from .firestore_db import FirestoreDatabase

        return FirestoreDatabase()
    return Database()


class PriceHunterApp:
    """Container de dependências da aplicação para facilitar testes e injeção de mocks."""

    def __init__(
        self,
        db: Any | None = None,
        location_service: LocationService | None = None,
        registry: ProviderRegistry | None = None,
    ) -> None:
        self.db = db or create_database()
        self.location_service = location_service or LocationService()
        self.registry = registry or ProviderRegistry()
        self.auth = AuthService(self.db)
        self.search = SearchService(self.db, self.location_service, self.registry)
        self.rate_limiter = SlidingWindowRateLimiter(
            max_requests=settings.rate_limit_per_min,
            window_seconds=60,
        )


def make_handler(app: PriceHunterApp):
    class PriceHunterHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            if settings.env != "test":
                super().log_message(format, *args)

        def _client_ip(self) -> str:
            forwarded = self.headers.get("X-Forwarded-For")
            if forwarded:
                return forwarded.split(",")[0].strip()
            return self.client_address[0] if self.client_address else "unknown"

        def _session_token(self) -> str | None:
            raw_cookie = self.headers.get("Cookie")
            if not raw_cookie:
                return None
            c = SimpleCookie()
            try:
                c.load(raw_cookie)
            except Exception:
                return None
            morsel = c.get(SESSION_COOKIE_NAME)
            return morsel.value if morsel else None

        def _current_user(self) -> dict[str, Any] | None:
            return app.auth.get_user_by_token(self._session_token())

        def _send_security_headers(self) -> None:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' https: data:; style-src 'self' 'unsafe-inline'; "
                "script-src 'self'; connect-src 'self'; font-src 'self' data:; base-uri 'self'; form-action 'self';",
            )

        def _send_json(
            self,
            payload: dict[str, Any],
            status: int = HTTPStatus.OK,
            extra_headers: list[tuple[str, str]] | None = None,
        ) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self._send_security_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            for k, v in extra_headers or []:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _read_json_body(self, max_bytes: int = 65536) -> dict[str, Any]:
            length_str = self.headers.get("Content-Length", "0")
            try:
                length = int(length_str)
            except ValueError as exc:
                raise ValueError("Cabeçalho Content-Length inválido.") from exc
            if length <= 0:
                return {}
            if length > max_bytes:
                raise ValueError("Payload excede o tamanho máximo permitido.")
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
            except Exception as exc:
                raise ValueError("JSON malformado no corpo da requisição.") from exc
            if not isinstance(data, dict):
                raise ValueError("Corpo JSON deve ser um objeto.")
            return data

        def _cookie_header(self, token: str, clear: bool = False) -> tuple[str, str]:
            secure_flag = "; Secure" if settings.is_production else ""
            if clear:
                return (
                    "Set-Cookie",
                    f"{SESSION_COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0{secure_flag}",
                )
            return (
                "Set-Cookie",
                f"{SESSION_COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={30*24*3600}{secure_flag}",
            )

        def _check_rate_limit(self) -> bool:
            allowed, retry_after = app.rate_limiter.allow(self._client_ip())
            if not allowed:
                self._send_json(
                    {
                        "error": "Limite de requisições atingido. Aguarde alguns segundos e tente novamente.",
                        "retry_after": retry_after,
                    },
                    status=HTTPStatus.TOO_MANY_REQUESTS,
                    extra_headers=[("Retry-After", str(retry_after))],
                )
                return False
            return True

        def _serve_static(self, path: str) -> None:
            rel = path.lstrip("/") or "index.html"
            # Evita path traversal
            candidate = (STATIC_DIR / rel).resolve()
            static_root = STATIC_DIR.resolve()
            if not str(candidate).startswith(str(static_root)) or not candidate.is_file():
                # Fallback SPA para rotas amigáveis (ex.: /favoritos)
                candidate = static_root / "index.html"
                if not candidate.is_file():
                    self._send_json({"error": "Arquivo estático não encontrado."}, HTTPStatus.NOT_FOUND)
                    return

            data = candidate.read_bytes()
            mime, _ = mimetypes.guess_type(str(candidate))
            if not mime:
                mime = "application/octet-stream"
            if mime.startswith("text/") or mime in ("application/javascript", "application/json"):
                mime = f"{mime}; charset=utf-8"

            self.send_response(HTTPStatus.OK)
            self._send_security_headers()
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)

        # --- Roteamento HTTP ---

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            if not path.startswith("/api/"):
                self._serve_static(path)
                return

            if not self._check_rate_limit():
                return

            qs = parse_qs(parsed.query)
            try:
                if path == "/api/health":
                    self._send_json(
                        {
                            "status": "ok",
                            "service": "Price Hunter",
                            "db_backend": getattr(app.db, "backend_name", "sqlite"),
                        }
                    )
                    return

                if path == "/api/providers":
                    self._send_json(
                        {
                            "providers": app.registry.list_providers(),
                            "default_weights": get_default_weights_info(),
                        }
                    )
                    return

                if path == "/api/home":
                    user = self._current_user()
                    data = app.search.get_home_data(user_id=user["id"] if user else None)
                    data["default_weights"] = get_default_weights_info()
                    self._send_json(data)
                    return

                if path.startswith("/api/cep/"):
                    raw_cep = path.split("/api/cep/", 1)[1]
                    loc = app.location_service.lookup_cep(raw_cep)
                    self._send_json({"location": loc.to_dict()})
                    return

                if path == "/api/search":
                    user = self._current_user()
                    q = (qs.get("q") or [""])[0]
                    cep = (qs.get("cep") or [""])[0]
                    if not cep and user and user.get("preferred_cep"):
                        cep = user["preferred_cep"]
                    mode = (qs.get("mode") or ["all"])[0]

                    custom_weights = None
                    if qs.get("weights"):
                        try:
                            custom_weights = json.loads(qs["weights"][0])
                        except Exception:
                            custom_weights = None
                    elif user and user.get("preferences", {}).get("custom_weights"):
                        custom_weights = user["preferences"]["custom_weights"]

                    res = app.search.search(
                        raw_query=q,
                        raw_cep=cep,
                        mode=mode,
                        custom_weights=custom_weights,
                        user_id=user["id"] if user else None,
                    )
                    self._send_json(res)
                    return

                if path == "/api/price-history":
                    product_key = (qs.get("product_key") or [""])[0]
                    offer_url = (qs.get("url") or [""])[0] or None
                    stats = app.db.get_price_history_stats(product_key=product_key, offer_url=offer_url)
                    self._send_json({"price_history": stats})
                    return

                if path == "/api/auth/me":
                    user = self._current_user()
                    self._send_json({"user": user, "authenticated": bool(user)})
                    return

                if path == "/api/favorites":
                    user = self._current_user()
                    if not user:
                        self._send_json({"error": "Faça login para acessar seus favoritos."}, HTTPStatus.UNAUTHORIZED)
                        return
                    items = app.db.list_favorites(user["id"])
                    self._send_json(
                        {
                            "favorites": items,
                            "monitoring_notice": (
                                "Os preços comparados refletem a última observação feita em pesquisas reais. "
                                "Monitoramento contínuo automático em segundo plano está planejado para a Fase 5."
                            ),
                        }
                    )
                    return

                self._send_json({"error": "Endpoint não encontrado."}, HTTPStatus.NOT_FOUND)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except AuthError as exc:
                self._send_json({"error": str(exc)}, exc.status_code)
            except Exception:
                self._send_json(
                    {"error": "Erro interno ao processar a solicitação. Tente novamente."},
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                )

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            if not path.startswith("/api/"):
                self._send_json({"error": "Método não permitido."}, HTTPStatus.METHOD_NOT_ALLOWED)
                return
            if not self._check_rate_limit():
                return

            try:
                body = self._read_json_body()

                if path == "/api/auth/register":
                    user, token = app.auth.register(
                        email=body.get("email", ""),
                        password=body.get("password", ""),
                        name=body.get("name", ""),
                        preferred_cep=body.get("preferred_cep", ""),
                    )
                    self._send_json(
                        {"user": user, "message": "Conta criada com sucesso."},
                        status=HTTPStatus.CREATED,
                        extra_headers=[self._cookie_header(token)],
                    )
                    return

                if path == "/api/auth/login":
                    user, token = app.auth.login(
                        email=body.get("email", ""),
                        password=body.get("password", ""),
                    )
                    self._send_json(
                        {"user": user, "message": "Login realizado com sucesso."},
                        extra_headers=[self._cookie_header(token)],
                    )
                    return

                if path == "/api/auth/logout":
                    app.auth.logout(self._session_token())
                    self._send_json(
                        {"message": "Sessão encerrada com sucesso."},
                        extra_headers=[self._cookie_header("", clear=True)],
                    )
                    return

                if path == "/api/auth/forgot-password":
                    res = app.auth.request_password_reset(body.get("email", ""))
                    self._send_json(res)
                    return

                if path == "/api/auth/reset-password":
                    res = app.auth.confirm_password_reset(
                        token=body.get("token", ""),
                        new_password=body.get("new_password", ""),
                    )
                    self._send_json(res)
                    return

                if path == "/api/favorites":
                    user = self._current_user()
                    if not user:
                        self._send_json({"error": "Faça login para salvar favoritos."}, HTTPStatus.UNAUTHORIZED)
                        return
                    original_url = str(body.get("original_url") or "").strip()
                    if not is_safe_http_url(original_url):
                        raise ValueError("Link original da oferta inválido.")
                    title = str(body.get("product_title") or "").strip()
                    store = str(body.get("store") or "").strip()
                    price = body.get("observed_price")
                    if not title or not store or price is None or float(price) <= 0:
                        raise ValueError("Dados obrigatórios da oferta ausentes.")
                    fav = app.db.add_favorite(
                        user["id"],
                        {
                            "offer_id": str(body.get("offer_id") or "")[:64],
                            "product_key": str(body.get("product_key") or "")[:120],
                            "product_title": title[:220],
                            "brand": str(body.get("brand") or "")[:80],
                            "model": str(body.get("model") or "")[:80],
                            "store": store[:80],
                            "observed_price": float(price),
                            "shipping_price": body.get("shipping_price"),
                            "final_price": body.get("final_price"),
                            "original_url": original_url,
                            "image_url": str(body.get("image_url") or "") if is_safe_http_url(body.get("image_url")) else "",
                            "condition": str(body.get("condition") or "new")[:32],
                            "cep_used": normalize_cep(body.get("cep_used")),
                            "opportunity_score": body.get("opportunity_score"),
                        },
                    )
                    self._send_json({"favorite": fav}, HTTPStatus.CREATED)
                    return

                self._send_json({"error": "Endpoint não encontrado."}, HTTPStatus.NOT_FOUND)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except AuthError as exc:
                self._send_json({"error": str(exc)}, exc.status_code)
            except Exception:
                self._send_json(
                    {"error": "Erro interno ao processar a solicitação."},
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                )

        def do_PATCH(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if not self._check_rate_limit():
                return
            try:
                if parsed.path == "/api/auth/profile":
                    user = self._current_user()
                    if not user:
                        self._send_json({"error": "Autenticação necessária."}, HTTPStatus.UNAUTHORIZED)
                        return
                    body = self._read_json_body()
                    updated = app.auth.update_profile(user["id"], body)
                    self._send_json({"user": updated, "message": "Perfil atualizado com sucesso."})
                    return
                self._send_json({"error": "Endpoint não encontrado."}, HTTPStatus.NOT_FOUND)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except AuthError as exc:
                self._send_json({"error": str(exc)}, exc.status_code)
            except Exception:
                self._send_json({"error": "Erro interno ao atualizar perfil."}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def do_DELETE(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if not self._check_rate_limit():
                return
            try:
                if parsed.path.startswith("/api/favorites/"):
                    user = self._current_user()
                    if not user:
                        self._send_json({"error": "Autenticação necessária."}, HTTPStatus.UNAUTHORIZED)
                        return
                    raw_id = parsed.path.split("/api/favorites/", 1)[1].strip()
                    if not raw_id or len(raw_id) > 64:
                        raise ValueError("ID de favorito inválido.")
                    deleted = app.db.delete_favorite(user["id"], raw_id)
                    if not deleted:
                        self._send_json({"error": "Favorito não encontrado."}, HTTPStatus.NOT_FOUND)
                        return
                    self._send_json({"deleted": True, "id": raw_id})
                    return
                self._send_json({"error": "Endpoint não encontrado."}, HTTPStatus.NOT_FOUND)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except Exception:
                self._send_json({"error": "Erro interno ao remover favorito."}, HTTPStatus.INTERNAL_SERVER_ERROR)

    return PriceHunterHandler


def create_server(
    host: str | None = None,
    port: int | None = None,
    app: PriceHunterApp | None = None,
) -> ThreadingHTTPServer:
    instance = app or PriceHunterApp()
    bind_host = host or settings.host
    bind_port = port if port is not None else settings.port
    server = ThreadingHTTPServer((bind_host, bind_port), make_handler(instance))
    server.daemon_threads = True
    return server


def main() -> None:
    server = create_server()
    host, port = server.server_address
    print(f"Price Hunter rodando em http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrando Price Hunter...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
