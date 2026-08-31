"""Small fail-closed bearer-token authenticator with role checks.

Tokens come from deployment secrets. Only their SHA-256 digests are retained after startup.
"""

import hashlib
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


@dataclass(frozen=True, slots=True)
class Principal:
    actor_id: str
    roles: frozenset[str]


class TokenAuthenticator:
    def __init__(self, token_config_json: str, *, environment: str) -> None:
        try:
            raw: Any = json.loads(token_config_json)
        except json.JSONDecodeError as exc:
            raise ValueError("DECISIONFLOW_AUTH_TOKENS_JSON must be valid JSON") from exc
        if not isinstance(raw, dict):
            raise ValueError("Auth token config must be a JSON object")
        principals: dict[str, Principal] = {}
        for token, value in raw.items():
            if not isinstance(token, str) or len(token) < 16:
                raise ValueError("Every bearer token must contain at least 16 characters")
            if not isinstance(value, dict):
                raise ValueError("Every token entry must be an object")
            actor_id = value.get("actor_id")
            roles = value.get("roles")
            if not isinstance(actor_id, str) or not actor_id or len(actor_id) > 160:
                raise ValueError("Every token needs a valid actor_id")
            if not isinstance(roles, list) or not roles:
                raise ValueError("Every token needs at least one role")
            role_set = frozenset(str(role) for role in roles)
            if not role_set.issubset({"operator", "approver", "auditor"}):
                raise ValueError("Unknown auth role")
            digest = hashlib.sha256(token.encode()).hexdigest()
            principals[digest] = Principal(actor_id=actor_id, roles=role_set)
        if environment.lower() in {"production", "staging"} and not principals:
            raise ValueError("Production and staging require configured bearer tokens")
        self._principals = principals

    def authenticate(self, token: str) -> Principal:
        supplied = hashlib.sha256(token.encode()).hexdigest()
        for digest, principal in self._principals.items():
            if secrets.compare_digest(digest, supplied):
                return principal
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


bearer = HTTPBearer(auto_error=False)


def principal_dependency(authenticator: TokenAuthenticator) -> Callable[..., Principal]:
    def dependency(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer)],
    ) -> Principal:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Bearer authentication is required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return authenticator.authenticate(credentials.credentials)

    return dependency


def require_roles(*allowed: str) -> Callable[[Principal], Principal]:
    allowed_set = frozenset(allowed)

    def check(principal: Principal) -> Principal:
        if not principal.roles.intersection(allowed_set):
            raise HTTPException(status_code=403, detail="Role is not authorized for this operation")
        return principal

    return check
