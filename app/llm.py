"""Couche LLM multimodale (vision) : Google Gemini, OpenAI, Anthropic, OpenRouter, Ollama.

L'application reste pleinement fonctionnelle sans clé API : dans ce cas, le
service répond en « mode démo » à partir du moteur technique et des extraits
de vos cours (aucun appel réseau).
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import httpx

from .config import Settings, get_settings

#: Modèles par défaut (surchargables par variables d'environnement).
#: Modèles par défaut (surchargables par variables d'environnement).
#: Pour Gemini, la valeur est vide : le modèle est découvert automatiquement avec votre
#: clé (voir GEMINI_MODEL_PREFERENCES). Google renomme et retire des modèles très
#: régulièrement, ce qui provoquait l'erreur 404 « no longer available to new users ».
DEFAULT_MODELS: dict[str, dict[str, str]] = {
    "gemini": {"vision": "", "text": ""},
    "openai": {"vision": "gpt-4o-mini", "text": "gpt-4o-mini"},
    "anthropic": {"vision": "claude-sonnet-4-5", "text": "claude-sonnet-4-5"},
    "openrouter": {
        "vision": "qwen/qwen2.5-vl-72b-instruct:free",
        "text": "meta-llama/llama-3.3-70b-instruct:free",
    },
    "ollama": {"vision": "qwen2.5vl:7b", "text": "qwen2.5vl:7b"},
}

#: Modèles Gemini essayés dans cet ordre (du plus récent au plus ancien).
GEMINI_MODEL_PREFERENCES: tuple[str, ...] = (
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3-flash",
    "gemini-3.6-pro",
    "gemini-3-pro",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.0-flash",
)

#: Cache de la liste des modèles disponibles, par clé API.
_MODELS_CACHE: dict[str, tuple[float, list[str]]] = {}
_MODELS_TTL = 1800.0  # 30 minutes (découverte réussie)
_MODELS_TTL_ECHEC = 120.0  # 2 minutes (échec réseau : on ne martèle pas l'API)

#: Dernière erreur par fournisseur (affichée dans l'interface pour ne pas mentir).
_LAST_ERRORS: dict[str, dict[str, str]] = {}


def record_llm_error(provider: str, message: str) -> None:
    """Mémorise la dernière erreur d'un fournisseur."""
    from datetime import datetime, timezone

    _LAST_ERRORS[provider or "inconnu"] = {
        "message": (message or "")[:400],
        "horodatage": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def clear_llm_error(provider: str) -> None:
    """Efface l'erreur mémorisée après un appel réussi."""
    _LAST_ERRORS.pop(provider or "inconnu", None)


def last_llm_error(provider: str) -> dict[str, str]:
    """Dernière erreur connue pour un fournisseur (dict vide si aucune)."""
    return dict(_LAST_ERRORS.get(provider, {}))


def _is_model_unavailable(message: str) -> bool:
    """Distingue « modèle retiré/introuvable » (réessayable) d'une erreur de quota."""
    texte = (message or "").lower()
    indices = (
        "no longer available",
        "not_found",
        "is not found",
        "not found",
        "does not exist",
        "unsupported model",
        "not supported for",
        "is not supported",
        "deprecated",
    )
    return any(indice in texte for indice in indices) and "model" in texte


def _should_try_next_model(message: str) -> bool:
    """Faut-il essayer un autre modèle ? (modèle retiré OU simple erreur 404).

    Un 404 sur un endpoint de modèle signifie toujours « ce modèle n'est pas
    disponible pour ce compte » : on tente le suivant plutôt que d'abandonner,
    même si le corps de la réponse est peu explicite.
    """
    return _is_model_unavailable(message) or "(404)" in message


def cached_models(llm: "BaseLLM") -> list[str]:
    """Modèles connus **sans appel réseau** (cache de découverte uniquement)."""
    cle = getattr(llm, "api_key", "")
    cle = cle[-10:] if len(cle) > 10 else cle
    entree = _MODELS_CACHE.get(cle)
    return list(entree[1]) if entree else []


PROVIDER_LABELS: dict[str, str] = {
    "gemini": "Google Gemini (clé gratuite AI Studio)",
    "openai": "OpenAI GPT-4o / GPT-4.1",
    "anthropic": "Anthropic Claude",
    "openrouter": "OpenRouter (modèles gratuits possibles)",
    "ollama": "Ollama (modèle local, hors-ligne)",
    "demo": "Mode démo (moteur technique seul, sans LLM)",
}


class LLMError(RuntimeError):
    """Erreur d'appel au fournisseur LLM."""


@dataclass
class LLMResponse:
    """Réponse normalisée d'un fournisseur."""

    text: str
    provider: str
    model: str
    demo: bool = False
    usage: dict[str, Any] = field(default_factory=dict)
    error: str = ""


# --------------------------------------------------------------------- #
#  Interface
# --------------------------------------------------------------------- #

class BaseLLM:
    """Interface commune : une méthode, plusieurs fournisseurs."""

    provider = "base"
    supports_vision = True

    def __init__(
        self,
        *,
        vision_model: str = "",
        text_model: str = "",
        timeout: float = 120.0,
        temperature: float = 0.25,
        max_tokens: int = 2600,
    ) -> None:
        self.vision_model = vision_model or DEFAULT_MODELS.get(self.provider, {}).get("vision", "")
        self.text_model = text_model or DEFAULT_MODELS.get(self.provider, {}).get("text", "")
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens

    @property
    def ready(self) -> bool:  # pragma: no cover - surchargé
        return False

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.vision_model or self.text_model}"

    def generate(
        self,
        *,
        system: str,
        messages: Sequence[dict[str, str]],
        images: Sequence[bytes] = (),
        use_vision: bool | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:  # pragma: no cover - surchargé
        raise NotImplementedError


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _image_mime(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in {b"GIF87a", b"GIF89a"}:
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


# --------------------------------------------------------------------- #
#  Google Gemini
# --------------------------------------------------------------------- #

class GeminiLLM(BaseLLM):
    """Client Gemini tolérant aux changements de modèles de Google.

    Google renomme et retire régulièrement ses modèles (`gemini-2.5-flash` a été
    retiré aux nouveaux comptes, remplacé par `gemini-3.6-flash`). Ce client :

    1. **découvre** les modèles réellement disponibles pour votre clé (`/v1beta/models`) ;
    2. **choisit** le meilleur modèle disponible selon ``GEMINI_MODEL_PREFERENCES`` ;
    3. **réessaie automatiquement** avec le modèle suivant si Google en retire un ;
    4. accepte les deux formats de clé (`AQ.…` nouvelles clés *Auth* → en-tête
       `x-goog-api-key`, et `AIza…` anciennes → paramètre `?key=`).
    """

    provider = "gemini"

    def __init__(self, api_key: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.api_key = (api_key or "").strip()
        self._resolved_model = ""
        self._resolved_vision_model = ""
        self._resolved_text_model = ""

    @property
    def ready(self) -> bool:
        return bool(self.api_key)

    @property
    def label(self) -> str:
        """Modèle réellement utilisé (les modèles configurés peuvent être vides)."""
        return f"{self.provider}:{self._resolved_model or self.vision_model or 'auto'}"

    @property
    def vision_model_effective(self) -> str:
        return self._resolved_vision_model or self._resolved_model or self.vision_model or "auto"

    # ------------------------------------------------------------ modèles
    @property
    def _headers(self) -> dict[str, str]:
        """En-tête d'authentification (obligatoire pour les clés « AQ. » d'AI Studio)."""
        return {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}

    def list_models(self, *, refresh: bool = False) -> list[str]:
        """Modèles disponibles pour cette clé (cache 30 min, liste vide si échec)."""
        import time

        cle = self.api_key[-10:] if len(self.api_key) > 10 else self.api_key
        maintenant = time.time()
        if not refresh:
            entree = _MODELS_CACHE.get(cle)
            if entree and maintenant - entree[0] < (_MODELS_TTL if entree[1] else _MODELS_TTL_ECHEC):
                return list(entree[1])

        modeles: list[str] = []
        try:
            with httpx.Client(timeout=min(self.timeout, 12.0)) as client:
                response = client.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    params={"key": self.api_key, "pageSize": 200},
                    headers=self._headers,
                )
            if response.status_code < 400:
                for item in response.json().get("models") or []:
                    if "generateContent" not in (item.get("supportedGenerationMethods") or []):
                        continue
                    nom = str(item.get("name", "")).replace("models/", "").strip()
                    if nom:
                        modeles.append(nom)
        except Exception:
            modeles = []

        # Un échec est aussi mémorisé (durée courte) : sans cela, chaque affichage
        # de page relançait une requête réseau bloquante.
        _MODELS_CACHE[cle] = (maintenant, modeles)
        return modeles

    def _candidate_models(self, use_images: bool) -> list[str]:
        """Ordre d'essai : modèle demandé → modèles disponibles (préférences) → secours."""
        disponibles = self.list_models()
        preferes = [nom for nom in GEMINI_MODEL_PREFERENCES if nom in disponibles]
        autres = sorted(
            nom
            for nom in disponibles
            if nom not in preferes
            and "embedding" not in nom
            and not nom.endswith(("-tts", "-image", "-live", "-audio", "-vision"))
        )
        candidats: list[str] = []
        demandes = [
            # Le modèle qui a déjà fonctionné est prioritaire (aucun essai inutile
            # du modèle retiré lors des appels suivants).
            self._resolved_model,
            self.vision_model if use_images else (self.text_model or self.vision_model),
            self.vision_model,
            self.text_model,
        ]
        for nom in [*demandes, *preferes, *autres, *GEMINI_MODEL_PREFERENCES]:
            nom = (nom or "").strip()
            if nom and nom not in candidats:
                candidats.append(nom)
        return candidats[:6]

    # ------------------------------------------------------------ appel HTTP
    def _call(
        self,
        model: str,
        *,
        system: str,
        contents: list[dict[str, Any]],
        max_tokens: int | None,
        temperature: float | None,
        json_mode: bool,
    ) -> tuple[str, dict[str, Any]]:
        payload: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": {
                "temperature": self.temperature if temperature is None else temperature,
                "maxOutputTokens": max_tokens or self.max_tokens,
            },
        }
        if json_mode:
            payload["generationConfig"]["responseMimeType"] = "application/json"

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        )
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    url,
                    params={"key": self.api_key},
                    headers=self._headers,
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise LLMError(f"Réseau indisponible vers Gemini : {exc}") from exc

        if response.status_code >= 400:
            detail = response.text[:400]
            if response.status_code == 429:
                detail += (
                    " | Quota gratuit atteint : patientez quelques minutes ou activez la "
                    "facturation sur Google Cloud."
                )
            raise LLMError(f"Gemini ({response.status_code}) : {detail}")

        data = response.json()
        choices = data.get("candidates") or []
        if not choices:
            feedback = data.get("promptFeedback") or {}
            raise LLMError(f"Gemini n'a renvoyé aucun texte (feedback : {feedback}).")
        parts = ((choices[0].get("content") or {}).get("parts")) or []
        text = "\n".join(part.get("text", "") for part in parts).strip()
        return text, (data.get("usageMetadata") or {})

    # ------------------------------------------------------------ API publique
    def generate(
        self,
        *,
        system: str,
        messages: Sequence[dict[str, str]],
        images: Sequence[bytes] = (),
        use_vision: bool | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        if not self.ready:
            raise LLMError("Clé GEMINI_API_KEY absente.")
        use_images = self.supports_vision if use_vision is None else use_vision

        contents: list[dict[str, Any]] = []
        for index, message in enumerate(messages):
            role = "model" if message.get("role") == "assistant" else "user"
            parts: list[dict[str, Any]] = [{"text": str(message.get("content", ""))}]
            if use_images and images and index == len(messages) - 1 and role == "user":
                for image in images:
                    parts.append(
                        {
                            "inline_data": {
                                "mime_type": _image_mime(image),
                                "data": _b64(image),
                            }
                        }
                    )
            contents.append({"role": role, "parts": parts})

        candidats = self._candidate_models(use_images)
        derniere_erreur: Optional[LLMError] = None

        for position, model in enumerate(candidats):
            try:
                text, usage = self._call(
                    model,
                    system=system,
                    contents=contents,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    json_mode=json_mode,
                )
            except LLMError as exc:
                derniere_erreur = exc
                message = str(exc)
                if position + 1 < len(candidats) and _should_try_next_model(message):
                    continue  # modèle retiré par Google → on essaie le suivant
                if _should_try_next_model(message):
                    raise LLMError(
                        "Aucun modèle Gemini disponible n'a pu répondre. Modèles essayés : "
                        + ", ".join(candidats[:4])
                        + f". Détail : {message} | Forcez un modèle avec la variable "
                        "VISION_MODEL (ex. gemini-3.6-flash)."
                    ) from exc
                raise
            self._resolved_model = model
            if use_images:
                self._resolved_vision_model = model
            else:
                self._resolved_text_model = model
            return LLMResponse(text=text, provider=self.provider, model=model, usage=usage)

        if derniere_erreur is not None:
            raise derniere_erreur
        raise LLMError("Aucun modèle Gemini n'a pu être utilisé.")


# --------------------------------------------------------------------- #
#  OpenAI & compatibles (OpenRouter)
# --------------------------------------------------------------------- #

class OpenAICompatLLM(BaseLLM):
    """Client pour toute API compatible OpenAI (`/chat/completions`)."""

    provider = "openai"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.openai.com/v1",
        extra_headers: Optional[dict[str, str]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.extra_headers = extra_headers or {}

    @property
    def ready(self) -> bool:
        return bool(self.api_key)

    def generate(
        self,
        *,
        system: str,
        messages: Sequence[dict[str, str]],
        images: Sequence[bytes] = (),
        use_vision: bool | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        if not self.ready:
            raise LLMError(f"Clé API absente pour {self.provider}.")
        use_images = self.supports_vision if use_vision is None else use_vision
        model = self.vision_model if (use_images and images) else (self.text_model or self.vision_model)

        payload_messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for index, message in enumerate(messages):
            role = message.get("role", "user")
            content: Any = str(message.get("content", ""))
            if use_images and images and index == len(messages) - 1 and role == "user":
                content = [{"type": "text", "text": content}]
                for image in images:
                    content.append(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{_image_mime(image)};base64,{_b64(image)}"
                            },
                        }
                    )
            payload_messages.append({"role": role, "content": content})

        payload: dict[str, Any] = {
            "model": model,
            "messages": payload_messages,
            "temperature": self.temperature if temperature is None else temperature,
        }
        # Les modèles de raisonnement n'acceptent pas toujours max_tokens.
        if max_tokens or self.max_tokens:
            payload["max_tokens"] = max_tokens or self.max_tokens
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **self.extra_headers,
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    f"{self.base_url}/chat/completions", headers=headers, json=payload
                )
        except httpx.HTTPError as exc:
            raise LLMError(f"Réseau indisponible ({self.provider}) : {exc}") from exc

        if response.status_code >= 400:
            raise LLMError(
                f"{self.provider} ({response.status_code}) : {response.text[:300]}"
            )
        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise LLMError(f"{self.provider} n'a renvoyé aucune réponse.")
        message = choices[0].get("message") or {}
        text = (message.get("content") or "").strip()
        if not text and message.get("reasoning"):
            text = str(message["reasoning"]).strip()
        return LLMResponse(
            text=text,
            provider=self.provider,
            model=model,
            usage=data.get("usage") or {},
        )


class OpenRouterLLM(OpenAICompatLLM):
    provider = "openrouter"


# --------------------------------------------------------------------- #
#  Anthropic Claude
# --------------------------------------------------------------------- #

class AnthropicLLM(BaseLLM):
    provider = "anthropic"

    def __init__(self, api_key: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.api_key = (api_key or "").strip()

    @property
    def ready(self) -> bool:
        return bool(self.api_key)

    def generate(
        self,
        *,
        system: str,
        messages: Sequence[dict[str, str]],
        images: Sequence[bytes] = (),
        use_vision: bool | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        if not self.ready:
            raise LLMError("Clé ANTHROPIC_API_KEY absente.")
        use_images = self.supports_vision if use_vision is None else use_vision
        model = self.vision_model if (use_images and images) else (self.text_model or self.vision_model)

        payload_messages: list[dict[str, Any]] = []
        for index, message in enumerate(messages):
            role = "assistant" if message.get("role") == "assistant" else "user"
            blocks: list[dict[str, Any]] = [{"type": "text", "text": str(message.get("content", ""))}]
            if use_images and images and index == len(messages) - 1 and role == "user":
                for image in images:
                    blocks.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": _image_mime(image),
                                "data": _b64(image),
                            },
                        }
                    )
            payload_messages.append({"role": role, "content": blocks})

        payload = {
            "model": model,
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": self.temperature if temperature is None else temperature,
            "system": system,
            "messages": payload_messages,
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    "https://api.anthropic.com/v1/messages", headers=headers, json=payload
                )
        except httpx.HTTPError as exc:
            raise LLMError(f"Réseau indisponible vers Anthropic : {exc}") from exc

        if response.status_code >= 400:
            raise LLMError(f"Anthropic ({response.status_code}) : {response.text[:300]}")
        data = response.json()
        blocks = data.get("content") or []
        text = "\n".join(
            block.get("text", "") for block in blocks if block.get("type") == "text"
        ).strip()
        return LLMResponse(
            text=text,
            provider=self.provider,
            model=model,
            usage=data.get("usage") or {},
        )


# --------------------------------------------------------------------- #
#  Ollama (local)
# --------------------------------------------------------------------- #

class OllamaLLM(BaseLLM):
    provider = "ollama"

    def __init__(self, base_url: str = "http://localhost:11434", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.base_url = (base_url or "http://localhost:11434").rstrip("/")

    @property
    def ready(self) -> bool:
        return bool(self.base_url)

    def generate(
        self,
        *,
        system: str,
        messages: Sequence[dict[str, str]],
        images: Sequence[bytes] = (),
        use_vision: bool | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        use_images = self.supports_vision if use_vision is None else use_vision
        model = self.vision_model if (use_images and images) else (self.text_model or self.vision_model)
        payload_messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for index, message in enumerate(messages):
            entry: dict[str, Any] = {
                "role": message.get("role", "user"),
                "content": str(message.get("content", "")),
            }
            if use_images and images and index == len(messages) - 1:
                entry["images"] = [_b64(image) for image in images]
            payload_messages.append(entry)

        payload: dict[str, Any] = {
            "model": model,
            "messages": payload_messages,
            "stream": False,
            "options": {
                "temperature": self.temperature if temperature is None else temperature,
                "num_predict": max_tokens or self.max_tokens,
            },
        }
        if json_mode:
            payload["format"] = "json"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(f"{self.base_url}/api/chat", json=payload)
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama injoignable ({self.base_url}) : {exc}") from exc
        if response.status_code >= 400:
            raise LLMError(f"Ollama ({response.status_code}) : {response.text[:300]}")
        data = response.json()
        text = ((data.get("message") or {}).get("content") or "").strip()
        return LLMResponse(text=text, provider=self.provider, model=model)


# --------------------------------------------------------------------- #
#  Fabrique
# --------------------------------------------------------------------- #

def _resolve_provider(settings: Settings) -> str:
    provider = (settings.llm_provider or "auto").strip().lower()
    if provider != "auto":
        return provider
    if settings.gemini_api_key:
        return "gemini"
    if settings.openai_api_key:
        return "openai"
    if settings.anthropic_api_key:
        return "anthropic"
    if settings.openrouter_api_key:
        return "openrouter"
    if settings.ollama_base_url:
        try:
            with httpx.Client(timeout=1.5) as client:
                client.get(f"{settings.ollama_base_url.rstrip('/')}/api/tags")
            return "ollama"
        except Exception:
            pass
    return "demo"


def build_llm(settings: Settings | None = None) -> Optional[BaseLLM]:
    """Construit le client LLM configuré (ou ``None`` si mode démo)."""
    settings = settings or get_settings()
    provider = _resolve_provider(settings)
    common = {
        "vision_model": settings.vision_model,
        "text_model": settings.text_model,
        "timeout": settings.llm_timeout_s,
        "temperature": settings.llm_temperature,
        "max_tokens": settings.llm_max_tokens,
    }
    if provider == "gemini" and settings.gemini_api_key:
        # vision_model / text_model peuvent être vides : GeminiLLM découvre alors
        # automatiquement les modèles disponibles pour la clé.
        return GeminiLLM(settings.gemini_api_key, **common)
    if provider == "openai" and settings.openai_api_key:
        return OpenAICompatLLM(
            settings.openai_api_key, base_url=settings.openai_base_url, **common
        )
    if provider == "anthropic" and settings.anthropic_api_key:
        return AnthropicLLM(settings.anthropic_api_key, **common)
    if provider == "openrouter" and settings.openrouter_api_key:
        return OpenRouterLLM(
            settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
            extra_headers={
                "HTTP-Referer": "https://github.com/andrianantenainasambatrarj-glitch/projet-IA",
                "X-Title": "TradeVision IA",
            },
            **common,
        )
    if provider == "ollama":
        model = settings.vision_model or settings.ollama_vision_model
        return OllamaLLM(
            settings.ollama_base_url,
            **{**common, "vision_model": model, "text_model": settings.text_model or model},
        )
    return None


_LLM_CACHE: dict[str, Optional[BaseLLM]] = {}


def get_llm(settings: Settings | None = None, *, refresh: bool = False) -> Optional[BaseLLM]:
    """Renvoie le client LLM (mis en cache) ou ``None`` en mode démo."""
    settings = settings or get_settings()
    key = "|".join(
        [
            settings.llm_provider,
            settings.vision_model,
            settings.text_model,
            "1" if settings.gemini_api_key else "0",
            "1" if settings.openai_api_key else "0",
            "1" if settings.anthropic_api_key else "0",
            "1" if settings.openrouter_api_key else "0",
            settings.ollama_base_url,
        ]
    )
    if refresh or key not in _LLM_CACHE:
        try:
            _LLM_CACHE[key] = build_llm(settings)
        except Exception:
            _LLM_CACHE[key] = None
    return _LLM_CACHE[key]


def clear_llm_cache() -> None:
    _LLM_CACHE.clear()


def provider_status(settings: Settings | None = None) -> dict[str, Any]:
    """État réel de la configuration LLM (affiché dans l'interface).

    ``vision_disponible`` n'est vrai que si un fournisseur est configuré **et**
    qu'aucune erreur récente n'a été enregistrée : l'interface ne doit jamais
    afficher « Vision active » alors que les appels échouent.
    """
    settings = settings or get_settings()
    configure = {
        "gemini": bool(settings.gemini_api_key),
        "openai": bool(settings.openai_api_key),
        "anthropic": bool(settings.anthropic_api_key),
        "openrouter": bool(settings.openrouter_api_key),
        "ollama": bool(settings.ollama_base_url),
    }
    actif = _resolve_provider(settings)
    llm = get_llm(settings)
    erreur = last_llm_error(actif)
    modele_vision = ""
    modele_texte = ""
    if llm is not None:
        modele_vision = getattr(llm, "vision_model_effective", "") or getattr(llm, "vision_model", "")
        modele_texte = getattr(llm, "text_model", "") or modele_vision
        if isinstance(llm, GeminiLLM):
            # IMPORTANT : on lit uniquement le cache (aucun appel réseau ici).
            # provider_status() est appelé à chaque affichage de page : déclencher la
            # découverte des modèles faisait attendre la page plusieurs secondes.
            connus = cached_models(llm)
            if connus and modele_vision in ("", "auto"):
                modele_vision = connus[0]

    mode_demo = not (
        (actif == "gemini" and settings.gemini_api_key)
        or (actif == "openai" and settings.openai_api_key)
        or (actif == "anthropic" and settings.anthropic_api_key)
        or (actif == "openrouter" and settings.openrouter_api_key)
        or actif == "ollama"
    ) or llm is None
    return {
        "provider_configure": settings.llm_provider,
        "provider_actif": actif,
        "mode_demo": mode_demo,
        "vision_disponible": bool(llm is not None and llm.supports_vision and not erreur),
        "modele_vision": modele_vision,
        "modele_texte": modele_texte,
        "cle_detectee": configure.get(actif, False),
        "cles_detectees": configure,
        "derniere_erreur": erreur,
        "libelles": PROVIDER_LABELS,
        "aide": (
            "Aucune clé API détectée : l'application fonctionne en mode démo "
            "(moteur technique + vos cours indexés), sans analyse d'image par IA. "
            "Ajoutez GEMINI_API_KEY (gratuit) pour activer la vision."
        )
        if llm is None
        else "Fournisseur LLM actif : l'analyse d'image par IA est disponible.",
    }
