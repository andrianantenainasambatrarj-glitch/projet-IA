"""Étape « vision » : décrire objectivement une capture de graphique.

Le LLM vision ne fait pas le travail d'analyse *tout seul* : il est utilisé
pour **transcrire** l'image en une description structurée (tendance, figures,
niveaux, bougies notables, unité de temps estimée). Cette description sert :

1. de **requête de recherche** dans votre base de connaissances (RAG) ;
2. de **contexte** pour la réponse finale, appuyée sur la méthode de vos cours.
"""

from __future__ import annotations

import base64
import io
import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .config import Settings, get_settings
from .llm import BaseLLM, LLMError

#: Taille maximale d'une image envoyée au LLM (côté le plus long, en pixels).
MAX_IMAGE_SIDE = 1600


def decode_image_payload(raw: str | bytes) -> bytes:
    """Décode une image fournie en base64 (avec ou sans préfixe ``data:``)."""
    if isinstance(raw, bytes):
        return raw
    payload = (raw or "").strip()
    if not payload:
        raise ValueError("Image vide.")
    match = re.match(r"^data:image/[a-zA-Z0-9.+-]+;base64,(.*)$", payload, re.S)
    if match:
        payload = match.group(1)
    try:
        return base64.b64decode(payload, validate=False)
    except Exception as exc:
        raise ValueError("Image illisible : base64 invalide.") from exc


def validate_image(data: bytes, settings: Settings | None = None) -> bytes:
    """Vérifie la taille et le format, et réduit l'image si nécessaire."""
    settings = settings or get_settings()
    if not data:
        raise ValueError("Image vide.")
    if len(data) > settings.max_upload_bytes:
        raise ValueError(
            f"Image trop lourde ({len(data) / 1024 / 1024:.1f} Mo) — "
            f"limite {settings.max_upload_mb} Mo."
        )
    signatures = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")
    if data[:4] == b"RIFF" or data.startswith(signatures):
        return _maybe_downscale(data)
    raise ValueError("Format d'image non reconnu (PNG, JPEG, WEBP ou GIF attendus).")


def _maybe_downscale(data: bytes) -> bytes:
    """Réduit l'image si Pillow est disponible (accélère et allège l'appel LLM)."""
    try:
        from PIL import Image  # type: ignore
    except Exception:
        return data
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        width, height = image.size
        longest = max(width, height)
        if longest > MAX_IMAGE_SIDE:
            ratio = MAX_IMAGE_SIDE / float(longest)
            image = image.resize((int(width * ratio), int(height * ratio)), Image.LANCZOS)
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        optimized = buffer.getvalue()
        return optimized if len(optimized) < len(data) else data
    except Exception:
        return data


# --------------------------------------------------------------------- #
#  Description structurée
# --------------------------------------------------------------------- #

DESCRIPTION_SYSTEM_PROMPT = """Tu es un analyste technique senior. Ton rôle ici est de \
TRANSCRIRE fidèlement une capture de graphique de trading en description structurée, \
sans inventer de données.

Règles strictes :
- Décris uniquement ce qui est visible (axes, bougies, figures, indicateurs affichés).
- Si une information n'est pas lisible (échelle, symbole, unité de temps), écris "non lisible".
- Estime les niveaux de prix en lisant l'axe vertical ; indique une fourchette si l'échelle est floue.
- Utilise le vocabulaire chartiste classique (support, résistance, tendance, tête-épaules,
  double sommet, triangle, canal, doji, marteau, avalement, divergence...).
- Réponds STRICTEMENT en JSON valide, sans texte avant ni après, en français."""

DESCRIPTION_USER_PROMPT = """Décris ce graphique de trading sous forme de JSON avec ce format :

{
  "type_de_graphique": "chandeliers japonais | barres | ligne | autre",
  "marche_ou_symbole_estime": "symbole ou 'non lisible'",
  "unite_de_temps_estimee": "M1/M5/M15/H1/H4/D1/W1 ou 'non lisible'",
  "tendance": "haussière | baissière | neutre/range, avec une phrase de justification",
  "fourchette_de_prix": "prix bas et haut lisibles sur l'axe, ex: 1.0720 - 1.0980",
  "dernier_prix_estime": "valeur la plus à droite ou 'non lisible'",
  "figures": ["liste des figures chartistes et chandeliers visibles"],
  "niveaux": ["supports et résistances avec leurs prix estimés"],
  "indicateurs_visibles": ["indicateurs affichés sous le graphique et leurs valeurs approximatives"],
  "bougies_notables": ["bougies marquantes avec leur date/position"],
  "volume": "comportement du volume si visible",
  "resume": "3 à 5 phrases résumant objectivement la situation",
  "elements_incertains": ["ce qui n'est pas lisible ou ambigu"],
  "confiance": 0.0
}"""


@dataclass
class ChartObservation:
    """Description structurée d'une capture de graphique."""

    available: bool = False
    provider: str = ""
    model: str = ""
    chart_type: str = ""
    symbol_guess: str = ""
    timeframe: str = ""
    trend: str = ""
    price_range: str = ""
    last_price: str = ""
    patterns: list[str] = field(default_factory=list)
    levels: list[str] = field(default_factory=list)
    indicators: list[str] = field(default_factory=list)
    notable_candles: list[str] = field(default_factory=list)
    volume: str = ""
    summary: str = ""
    uncertainties: list[str] = field(default_factory=list)
    confidence: float = 0.0
    raw: str = ""
    error: str = ""

    @property
    def rag_query(self) -> str:
        """Requête de recherche construite à partir de la description."""
        parts = [
            self.symbol_guess if self.symbol_guess and "non lisible" not in self.symbol_guess.lower() else "",
            self.timeframe if self.timeframe and "non lisible" not in self.timeframe.lower() else "",
            self.trend,
            " ".join(self.patterns[:6]),
            " ".join(self.levels[:5]),
            self.summary,
        ]
        return " | ".join(part.strip() for part in parts if part and part.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "provider": self.provider,
            "model": self.model,
            "chart_type": self.chart_type,
            "symbol_guess": self.symbol_guess,
            "timeframe": self.timeframe,
            "trend": self.trend,
            "price_range": self.price_range,
            "last_price": self.last_price,
            "patterns": self.patterns,
            "levels": self.levels,
            "indicators": self.indicators,
            "notable_candles": self.notable_candles,
            "volume": self.volume,
            "summary": self.summary,
            "uncertainties": self.uncertainties,
            "confidence": round(self.confidence, 2),
            "raw": self.raw,
            "error": self.error,
        }

    def render(self) -> str:
        """Rendu texte de l'observation, injecté dans le prompt final."""
        if not self.available:
            return (
                "Aucune description automatique de l'image n'est disponible "
                f"({self.error or 'fournisseur vision non configuré'})."
            )
        lines = [
            f"- Type de graphique : {self.chart_type or 'non lisible'}",
            f"- Marché / symbole estimé : {self.symbol_guess or 'non lisible'}",
            f"- Unité de temps estimée : {self.timeframe or 'non lisible'}",
            f"- Tendance observée : {self.trend or 'non lisible'}",
            f"- Fourchette de prix lisible : {self.price_range or 'non lisible'}",
            f"- Dernier prix estimé : {self.last_price or 'non lisible'}",
            f"- Figures relevées : {', '.join(self.patterns) if self.patterns else 'aucune nette'}",
            f"- Niveaux relevés : {'; '.join(self.levels) if self.levels else 'aucun net'}",
            f"- Indicateurs visibles : {', '.join(self.indicators) if self.indicators else 'aucun'}",
            f"- Bougies notables : {'; '.join(self.notable_candles) if self.notable_candles else 'aucune'}",
            f"- Volume : {self.volume or 'non lisible'}",
            f"- Résumé : {self.summary}",
            f"- Incertitudes : {', '.join(self.uncertainties) if self.uncertainties else 'aucune'}",
            f"- Confiance de la description : {self.confidence:.0%}",
        ]
        return "\n".join(lines)


def parse_json_block(text: str) -> dict[str, Any]:
    """Extrait un objet JSON d'une réponse LLM (tolérant aux ``` et au bruit)."""
    cleaned = (text or "").strip()
    if not cleaned:
        return {}
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.S)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        candidate = cleaned[start : end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            # Tentative de réparation légère (virgules finales)
            repaired = re.sub(r",\s*([}\]])", r"\1", candidate)
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                return {}
    return {}


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[;\n•]|,(?![^(]*\))", value) if item.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value)]


def read_chart(
    image: bytes,
    *,
    llm: Optional[BaseLLM] = None,
    settings: Settings | None = None,
    extra_instruction: str = "",
) -> ChartObservation:
    """Décrit une image de graphique via le LLM vision (ou renvoie une erreur)."""
    settings = settings or get_settings()
    if llm is None:
        return ChartObservation(
            available=False,
            error=(
                "Aucun fournisseur vision configuré. Ajoutez GEMINI_API_KEY, "
                "OPENAI_API_KEY, ANTHROPIC_API_KEY ou OPENROUTER_API_KEY pour "
                "activer l'analyse d'image par IA (le reste de l'analyse "
                "technique et la recherche dans vos cours fonctionnent déjà)."
            ),
        )
    if not llm.supports_vision:
        return ChartObservation(available=False, error="Ce modèle ne gère pas les images.")

    prompt = DESCRIPTION_USER_PROMPT
    if extra_instruction:
        prompt += f"\n\nConsigne supplémentaire : {extra_instruction.strip()}"

    try:
        response = llm.generate(
            system=DESCRIPTION_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            images=[image],
            use_vision=True,
            max_tokens=min(1800, settings.llm_max_tokens),
            temperature=0.1,
            json_mode=True,
        )
    except LLMError as exc:
        return ChartObservation(available=False, error=str(exc))
    except Exception as exc:  # pragma: no cover
        return ChartObservation(available=False, error=f"Échec vision : {exc}")

    payload = parse_json_block(response.text)
    if not payload:
        return ChartObservation(
            available=True,
            provider=response.provider,
            model=response.model,
            summary=response.text.strip(),
            raw=response.text,
            error="Réponse non structurée : description brute conservée.",
            confidence=0.4,
        )

    try:
        confidence = float(payload.get("confiance") or 0.5)
    except (TypeError, ValueError):
        confidence = 0.5

    return ChartObservation(
        available=True,
        provider=response.provider,
        model=response.model,
        chart_type=str(payload.get("type_de_graphique", "")),
        symbol_guess=str(payload.get("marche_ou_symbole_estime", "")),
        timeframe=str(payload.get("unite_de_temps_estimee", "")),
        trend=str(payload.get("tendance", "")),
        price_range=str(payload.get("fourchette_de_prix", "")),
        last_price=str(payload.get("dernier_prix_estime", "")),
        patterns=_as_list(payload.get("figures")),
        levels=_as_list(payload.get("niveaux")),
        indicators=_as_list(payload.get("indicateurs_visibles")),
        notable_candles=_as_list(payload.get("bougies_notables")),
        volume=str(payload.get("volume", "")),
        summary=str(payload.get("resume", "")).strip(),
        uncertainties=_as_list(payload.get("elements_incertains")),
        confidence=max(0.0, min(1.0, confidence)),
        raw=response.text,
    )
