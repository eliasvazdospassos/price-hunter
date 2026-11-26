"""Serviço de autenticação, sessões seguras, recuperação de senha e perfil do usuário."""

from __future__ import annotations

import time
from typing import Any

from .config import DEFAULT_NEW_WEIGHTS, DEFAULT_USED_WEIGHTS, settings
from .db import DuplicateEmailError
from .security import (
    format_cep,
    generate_token,
    hash_password,
    hash_token,
    normalize_cep,
    sanitize_text,
    validate_email,
    validate_password,
    verify_password,
)

SESSION_TTL_SECONDS = 30 * 24 * 3600  # 30 dias
RESET_TTL_SECONDS = 30 * 60  # 30 minutos


class AuthError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class AuthService:
    """Autenticação segura (scrypt + cookies HttpOnly), compatível com SQLite local e Google Cloud Firestore."""

    def __init__(self, db: Any) -> None:
        self.db = db

    def _serialize_user(self, user: dict[str, Any]) -> dict[str, Any]:
        prefs = user.get("preferences") or {}
        cep = user.get("preferred_cep") or ""
        return {
            "id": str(user["id"]),
            "email": user["email"],
            "name": user["name"],
            "preferred_cep": cep,
            "preferred_cep_formatted": format_cep(cep) if cep else "",
            "preferences": {
                "default_mode": prefs.get("default_mode", "all"),
                "default_sort": prefs.get("default_sort", "opportunity"),
                "max_budget": prefs.get("max_budget"),
                "custom_weights": prefs.get("custom_weights") or None,
            },
            "created_at": int(user["created_at"]),
            "auth_backend": getattr(self.db, "backend_name", "sqlite"),
        }

    def register(
        self,
        email: str,
        password: str,
        name: str,
        preferred_cep: str = "",
    ) -> tuple[dict[str, Any], str]:
        try:
            clean_email = validate_email(email)
            validate_password(password)
        except ValueError as exc:
            raise AuthError(str(exc), 400) from exc

        clean_name = sanitize_text(name, 80)
        if len(clean_name) < 2:
            raise AuthError("Informe seu nome (mínimo 2 caracteres).", 400)

        clean_cep = ""
        if preferred_cep and preferred_cep.strip():
            clean_cep = normalize_cep(preferred_cep)
            if not clean_cep:
                raise AuthError("CEP preferencial inválido. Use 8 dígitos.", 400)

        now = int(time.time())
        pw_hash = hash_password(password)

        try:
            user = self.db.create_user(
                email=clean_email,
                name=clean_name,
                password_hash=pw_hash,
                preferred_cep=clean_cep,
                now=now,
            )
        except DuplicateEmailError as exc:
            raise AuthError("Já existe uma conta cadastrada com este e-mail.", 409) from exc

        token = self._create_session(user["id"])
        return self._serialize_user(user), token

    def login(self, email: str, password: str) -> tuple[dict[str, Any], str]:
        try:
            clean_email = validate_email(email)
        except ValueError as exc:
            raise AuthError("E-mail ou senha inválidos.", 401) from exc

        if not password:
            raise AuthError("E-mail ou senha inválidos.", 401)

        user = self.db.get_user_by_email(clean_email)
        if not user or not verify_password(password, user["password_hash"]):
            raise AuthError("E-mail ou senha inválidos.", 401)

        token = self._create_session(user["id"])
        return self._serialize_user(user), token

    def _create_session(self, user_id: str) -> str:
        token = generate_token(32)
        now = int(time.time())
        self.db.create_session(
            user_id=user_id,
            token_hash=hash_token(token),
            expires_at=now + SESSION_TTL_SECONDS,
            now=now,
        )
        return token

    def get_user_by_token(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        user = self.db.get_user_by_session_hash(hash_token(token), int(time.time()))
        return self._serialize_user(user) if user else None

    def logout(self, token: str | None) -> None:
        if not token:
            return
        self.db.delete_session(hash_token(token))

    def update_profile(self, user_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        user = self.db.get_user_by_id(user_id)
        if not user:
            raise AuthError("Usuário não encontrado.", 404)

        name = user["name"]
        if "name" in updates and updates["name"] is not None:
            clean_name = sanitize_text(str(updates["name"]), 80)
            if len(clean_name) < 2:
                raise AuthError("O nome deve ter pelo menos 2 caracteres.", 400)
            name = clean_name

        cep = user.get("preferred_cep") or ""
        if "preferred_cep" in updates:
            raw_cep = str(updates.get("preferred_cep") or "").strip()
            if raw_cep == "":
                cep = ""
            else:
                norm = normalize_cep(raw_cep)
                if not norm:
                    raise AuthError("CEP preferencial inválido. Informe 8 dígitos.", 400)
                cep = norm

        prefs = dict(user.get("preferences") or {})
        if isinstance(updates.get("preferences"), dict):
            in_prefs = updates["preferences"]
            if in_prefs.get("default_mode") in ("all", "new", "used"):
                prefs["default_mode"] = in_prefs["default_mode"]
            if in_prefs.get("default_sort") in (
                "opportunity",
                "price_asc",
                "final_price_asc",
                "delivery_asc",
                "distance_asc",
                "rating_desc",
            ):
                prefs["default_sort"] = in_prefs["default_sort"]
            if "max_budget" in in_prefs:
                mb = in_prefs["max_budget"]
                if mb in (None, ""):
                    prefs["max_budget"] = None
                else:
                    try:
                        mb_val = float(mb)
                        if mb_val < 0:
                            raise ValueError
                        prefs["max_budget"] = round(mb_val, 2)
                    except (TypeError, ValueError) as exc:
                        raise AuthError("Orçamento máximo inválido.", 400) from exc
            if "custom_weights" in in_prefs:
                cw = in_prefs["custom_weights"]
                if cw is None:
                    prefs["custom_weights"] = None
                elif isinstance(cw, dict):
                    cleaned_w: dict[str, int] = {}
                    for k in DEFAULT_NEW_WEIGHTS:
                        if k in cw:
                            val = int(round(float(cw[k])))
                            if 0 <= val <= 100:
                                cleaned_w[k] = val
                    prefs["custom_weights"] = cleaned_w if sum(cleaned_w.values()) > 0 else None

        updated = self.db.update_user(
            user_id=user_id,
            name=name,
            preferred_cep=cep,
            preferences=prefs,
            now=int(time.time()),
        )
        if not updated:
            raise AuthError("Usuário não encontrado.", 404)
        return self._serialize_user(updated)

    def request_password_reset(self, email: str) -> dict[str, Any]:
        try:
            clean_email = validate_email(email)
        except ValueError as exc:
            raise AuthError(str(exc), 400) from exc

        dev_token: str | None = None
        now = int(time.time())
        user = self.db.get_user_by_email(clean_email)
        if user:
            token = generate_token(24)
            self.db.create_password_reset(
                user_id=user["id"],
                token_hash=hash_token(token),
                expires_at=now + RESET_TTL_SECONDS,
                now=now,
            )
            if not settings.is_production:
                dev_token = token

        result: dict[str, Any] = {
            "message": "Se o e-mail estiver cadastrado, um código de recuperação válido por 30 minutos foi gerado.",
        }
        if dev_token:
            result["dev_reset_token"] = dev_token
            result["dev_note"] = (
                "Ambiente local sem servidor SMTP configurado: use este código temporário para concluir a redefinição."
            )
        return result

    def confirm_password_reset(self, token: str, new_password: str) -> dict[str, Any]:
        if not token or not token.strip():
            raise AuthError("Informe o código de recuperação.", 400)
        try:
            validate_password(new_password)
        except ValueError as exc:
            raise AuthError(str(exc), 400) from exc

        now = int(time.time())
        user_id = self.db.consume_password_reset(hash_token(token.strip()), now)
        if not user_id:
            raise AuthError("Código de recuperação inválido ou expirado.", 400)

        self.db.update_user_password(user_id, hash_password(new_password), now)
        self.db.delete_user_sessions(user_id)
        return {"message": "Senha redefinida com sucesso. Faça login com sua nova senha."}


def get_default_weights_info() -> dict[str, Any]:
    return {
        "new": DEFAULT_NEW_WEIGHTS,
        "used": DEFAULT_USED_WEIGHTS,
    }
