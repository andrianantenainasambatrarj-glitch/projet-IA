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
DEFAULT_MODELS: dict[str, dict[str, str]] = {
    "gemini": {"vision": "gemini-2.5-flash", "text": "gemini-2.5-flash"},
    "openai": {"vision": "gpt-4o-mini", "text": "gpt-4o-mini"},
    "anthropic": {"vision": "claude-sonnet-4-5", "text": "claude-sonnet-4-5"},
    "openrouter": {
        "vision": "qwen/qwen2.5-vl-72b-instruct:free",
        "text": "meta-llama/llama-3.3-70b-instruct:free",
    },
    "ollama": {"vision": "qwen2.5vl:7b", "text": "qwen2.5vl:7b"},
}

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
    provider = "gemini"

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
            raise LLMError("Clé GEMINI_API_KEY absente.")
        use_images = self.supports_vision if use_vision is None else use_vision
        model = self.vision_model if (use_images and images) else (self.text_model or self.vision_model)

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
                    url, params={"key": self.api_key}, json=payload
                )
        except httpx.HTTPError as exc:
            raise LLMError(f"Réseau indisponible vers Gemini : {exc}") from exc

        if response.status_code >= 400:
            raise LLMError(f"Gemini ({response.status_code}) : {response.text[:300]}")
        data = response.json()
        candidates = data.get("candidates") or []
        if not candidates:
            feedback = data.get("promptFeedback") or {}
            raise LLMError(f"Gemini n'a renvoyé aucun texte (feedback : {feedback}).")
        parts = ((candidates[0].get("content") or {}).get("parts")) or []
        text = "\n".join(part.get("text", "") for part in parts).strip()
        return LLMResponse(
            text=text,
            provider=self.provider,
            model=model,
            usage=data.get("usageMetadata") or {},
        )


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
    """État de la configuration LLM (affiché dans l'interface)."""
    settings = settings or get_settings()
    configured = {
        "gemini": bool(settings.gemini_api_key),
        "openai": bool(settings.openai_api_key),
        "anthropic": bool(settings.anthropic_api_key),
        "openrouter": bool(settings.openrouter_api_key),
        "ollama": bool(settings.ollama_base_url),
    }
    active = _resolve_provider(settings)
    llm = get_llm(settings)
    return {
        "provider_configure": settings.llm_provider,
        "provider_actif": active,
        "mode_demo": llm is None,
        "vision_disponible": bool(llm is not None and llm.supports_vision),
        "modele_vision": getattr(llm, "vision_model", "") if llm else "",
        "modele_texte": getattr(llm, "text_model", "") if llm else "",
        "cles_detectees": configured,
        "libelles": PROVIDER_LABELS,
        "aide": (
            "Aucune clé API détectée : l'application fonctionne en mode démo "
            "(moteur technique + vos cours indexés), sans analyse d'image par IA. "
            "Ajoutez GEMINI_API_KEY (gratuit) pour activer la vision."
        )
        if llm is None
        else "Fournisseur LLM actif : l'analyse d'image par IA est disponible.",
    }
