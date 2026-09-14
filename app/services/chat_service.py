"""Chat documentaire : questions/réponses sur vos cours (RAG pur)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from ..config import Settings, get_settings
from ..llm import LLMError, get_llm
from ..prompts import CHAT_SYSTEM, DISCLAIMER, build_chat_prompt
from ..retriever import build_context, search_multi
from ..textutils import truncate


@dataclass
class ChatRequest:
    """Paramètres d'une question au chat documentaire."""

    question: str
    top_k: int = 5
    history: list[dict[str, str]] = field(default_factory=list)


@dataclass
class ChatResponse:
    """Réponse du chat documentaire."""

    answer: str
    mode: str
    provider: str = ""
    model: str = ""
    sources: list[dict[str, Any]] = field(default_factory=list)
    rag_mode: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "mode": self.mode,
            "provider": self.provider,
            "model": self.model,
            "sources": self.sources,
            "rag_mode": self.rag_mode,
            "warnings": self.warnings,
        }


_MODE_LABELS = {
    "hybride": "hybride : vecteurs + mots-clés",
    "vecteurs": "vectorielle (embeddings)",
    "bm25": "mots-clés (BM25)",
    "vide": "indisponible (base vide)",
}


def _demo_answer(question: str, sources: Sequence[dict[str, Any]], rag_mode: str) -> str:
    """Réponse sans LLM : extraits classés de la base de connaissances."""
    lines: list[str] = [
        "> ⚙️ **Mode démo (sans clé LLM)** : voici les passages de vos cours les plus "
        "pertinents pour votre question, classés par pertinence — recherche "
        f"{_MODE_LABELS.get(rag_mode, rag_mode)}. Configurez une clé LLM pour obtenir "
        "une réponse rédigée.\n"
    ]
    if not sources:
        lines.append(
            "Aucun document n'est indexé pour le moment. Ouvrez l'onglet "
            "« Base de connaissances » et importez vos cours (PDF, Markdown, texte) : "
            "le chat les utilisera immédiatement."
        )
        return "\n".join(lines)

    lines.append(f"### Réponse documentaire pour : « {truncate(question, 160)} »\n")
    for index, source in enumerate(sources, start=1):
        reference = source.get("title", "document")
        section = source.get("section") or ""
        page = f", p.{source['page']}" if source.get("page") else ""
        lines.append(
            f"**[Source {index}] {reference}"
            f"{f' › {section}' if section else ''}{page}** "
            f"(score {source.get('score', 0):.2f})"
        )
        lines.append(f"> {source.get('extract', '')}\n")
    lines.append(
        "**Comment lire ces extraits** : les scores combinent la proximité sémantique "
        "(embeddings) et la correspondance de mots-clés (BM25). Les 3 premiers extraits "
        "sont généralement ceux qui répondent le mieux à la question posée."
    )
    lines.append(f"\n---\n{DISCLAIMER}")
    return "\n".join(lines)


def run_chat(request: ChatRequest, *, settings: Settings | None = None) -> ChatResponse:
    """Répond à une question à partir de la base de connaissances."""
    settings = settings or get_settings()
    warnings: list[str] = []
    question = (request.question or "").strip()
    if not question:
        return ChatResponse(
            answer="Posez une question sur vos cours (ex : « Qu'est-ce qu'une épaule-tête-épaule ? »).",
            mode="demo",
            warnings=["Question vide."],
        )

    queries = [question]
    for message in request.history[-2:]:
        content = (message.get("content") or "").strip()
        if content:
            queries.append(content)
    retrieval = search_multi(queries, top_k=request.top_k, settings=settings)
    sources = retrieval.sources
    context = build_context(retrieval.hits, settings.max_context_chars)
    llm = get_llm(settings)

    if not sources:
        warnings.append(
            "Aucun extrait trouvé : la base de connaissances est peut-être vide ou "
            "la question utilise un vocabulaire absent des documents."
        )

    answer = ""
    mode = "ia" if llm is not None else "demo"
    provider = getattr(llm, "provider", "") if llm else ""
    model = getattr(llm, "text_model", "") if llm else ""

    if llm is not None:
        messages: list[dict[str, str]] = []
        for message in request.history[-4:]:
            role = "assistant" if message.get("role") == "assistant" else "user"
            messages.append({"role": role, "content": str(message.get("content", ""))})
        messages.append(
            {
                "role": "user",
                "content": build_chat_prompt(course_context=context, question=question),
            }
        )
        try:
            response = llm.generate(
                system=CHAT_SYSTEM, messages=messages, use_vision=False
            )
            answer = response.text.strip()
            model = response.model
            if not answer:
                raise LLMError("Réponse vide.")
        except LLMError as exc:
            warnings.append(f"Appel LLM impossible ({exc}) — repli sur les extraits bruts.")
            mode = "demo"
        except Exception as exc:  # pragma: no cover
            warnings.append(f"Erreur LLM inattendue ({exc}).")
            mode = "demo"

    if not answer:
        answer = _demo_answer(question, sources, retrieval.mode)
        mode = "demo"

    return ChatResponse(
        answer=answer,
        mode=mode,
        provider=provider,
        model=model,
        sources=sources,
        rag_mode=retrieval.mode,
        warnings=warnings,
    )
