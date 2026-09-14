"""Dépendances FastAPI partagées (sécurité optionnelle)."""

from __future__ import annotations

from fastapi import Header, HTTPException, Query, status

from ..config import get_settings


def require_token(
    x_api_token: str | None = Header(default=None, alias="X-API-Token"),
    token: str | None = Query(default=None),
) -> None:
    """Contrôle d'accès optionnel.

    Si ``API_ACCESS_TOKEN`` est défini dans l'environnement, toutes les routes
    ``/api/*`` exigent l'en-tête ``X-API-Token`` (ou ``?token=``), ce qui permet
    de publier l'application sans la laisser totalement ouverte.

    NB : ne jamais déclarer ici un paramètre typé par un modèle Pydantic
    (ex. ``settings: Settings``) — FastAPI l'interpréterait comme un corps de
    requête et emboîterait tous les payloads JSON des routes (erreur 422).
    """
    expected = (get_settings().api_access_token or "").strip()
    if not expected:
        return
    provided = (x_api_token or token or "").strip()
    if provided != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Accès refusé : jeton manquant ou invalide. "
                "Fournissez l'en-tête X-API-Token."
            ),
        )
