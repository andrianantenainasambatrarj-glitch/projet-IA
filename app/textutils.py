"""Utilitaires texte : normalisation, tokenisation, stopwords, découpage (chunking).

Aucune dépendance externe : tout fonctionne hors-ligne.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# --------------------------------------------------------------------- #
#  Normalisation / tokenisation
# --------------------------------------------------------------------- #

_STOPWORDS_FR = {
    "au", "aux", "avec", "ce", "ces", "dans", "de", "des", "du", "elle", "en", "et",
    "eux", "il", "ils", "je", "la", "le", "les", "leur", "lui", "ma", "mais", "me",
    "mes", "moi", "mon", "ne", "nos", "notre", "nous", "on", "ou", "par", "pas",
    "pour", "qu", "que", "qui", "sa", "se", "ses", "son", "sur", "ta", "te", "tes",
    "toi", "ton", "tu", "un", "une", "vos", "votre", "vous", "y", "a", "à", "est",
    "sont", "être", "été", "c", "d", "l", "s", "n", "j", "m", "t", "plus", "moins",
    "aussi", "cette", "cet", "donc", "alors", "si", "comme", "tout", "tous", "toute",
    "toutes", "peut", "peuvent", "fait", "faire", "cela", "celui", "celle", "dont",
    "où", "quel", "quelle", "quels", "quelles", "même", "entre", "sans", "sous",
    "vers", "chez", "lors", "lorsque", "quand", "ainsi", "encore", "déjà", "très",
    "bien", "ici", "là", "oui", "non", "il", "elle", "on",
}

_STOPWORDS_EN = {
    "the", "a", "an", "and", "or", "but", "if", "then", "than", "of", "to", "in",
    "on", "at", "by", "for", "with", "from", "as", "is", "are", "was", "were",
    "be", "been", "being", "this", "that", "these", "those", "it", "its", "his",
    "her", "their", "our", "your", "my", "you", "we", "they", "he", "she", "not",
    "no", "do", "does", "did", "so", "such", "can", "could", "should", "would",
    "may", "might", "must", "will", "shall", "there", "here", "about", "into",
    "over", "after", "before", "between", "up", "down", "out", "more", "most",
    "some", "any", "all", "each", "other", "which", "who", "whom", "what", "when",
    "where", "why", "how",
}

STOPWORDS = _STOPWORDS_FR | _STOPWORDS_EN

_ACCENTS = str.maketrans(
    "àâäáãåçèéêëìíîïñòóôöõùúûüýÿœæ",
    "aaaaaaceeeeiiiinooooouuuuyyae",
)


def strip_accents(text: str) -> str:
    """Retire les accents (utile pour des recherches tolérantes)."""
    lowered = text.translate(_ACCENTS)
    return "".join(
        ch for ch in unicodedata.normalize("NFD", lowered) if unicodedata.category(ch) != "Mn"
    )


def normalize(text: str) -> str:
    """Minuscule + sans accents + espaces harmonisés."""
    return re.sub(r"\s+", " ", strip_accents((text or "").lower())).strip()


def simple_stem(token: str) -> str:
    """Racinisation légère française/anglaise (suffixes les plus fréquents)."""
    if len(token) <= 4:
        return token
    for suffix in ("issements", "issement", "ations", "ation", "ements", "ement",
                   "euses", "euse", "iques", "ique", "istes", "iste", "trices",
                   "trice", "ments", "ment", "eaux", "aux", "ées", "ee", "es",
                   "s", "e", "x"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            return token[: -len(suffix)]
    return token


def tokenize(text: str, *, keep_stopwords: bool = False, stem: bool = True) -> list[str]:
    """Tokenise un texte en mots normalisés."""
    tokens = re.findall(r"[a-z0-9]+", normalize(text))
    out: list[str] = []
    for tok in tokens:
        if not keep_stopwords and tok in STOPWORDS and not tok.isdigit():
            continue
        if len(tok) < 2 and not tok.isdigit():
            continue
        out.append(simple_stem(tok) if stem else tok)
    return out


def split_sentences(text: str) -> list[str]:
    """Découpe en phrases (approximatif mais robuste)."""
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        return []
    parts = re.split(r"(?<=[.!?…])\s+(?=[A-ZÀ-ÖØ-Þ0-9«\"(])", cleaned)
    return [p.strip() for p in parts if p.strip()]


def truncate(text: str, max_chars: int, ellipsis: str = " …") -> str:
    """Tronque proprement un texte."""
    text = text or ""
    if len(text) <= max_chars:
        return text
    cut = text[: max(0, max_chars - len(ellipsis))]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut + ellipsis


# --------------------------------------------------------------------- #
#  Découpage en chunks (chunking)
# --------------------------------------------------------------------- #

@dataclass
class Chunk:
    """Morceau de document destiné à l'indexation."""

    text: str
    index: int
    section: str = ""
    page: int = 0
    words: int = 0

    def as_metadata(self) -> dict:
        return {
            "chunk_index": self.index,
            "section": self.section,
            "page": self.page,
            "words": self.words,
        }


_HEADING_PATTERNS = [
    re.compile(r"^\s*(?:chapitre|partie|section|module|le[çc]on)\s+[\dIVXLC]+", re.I),
    re.compile(r"^\s*\d+(?:\.\d+)*[.)]?\s+[A-ZÀ-Ý][^.!?]{2,80}$"),
    re.compile(r"^\s*#{1,6}\s+\S"),
    re.compile(r"^\s*[A-ZÀ-Ý0-9 ,'’\-:()/]{6,70}$"),
]


def looks_like_heading(line: str) -> bool:
    """Heuristique : cette ligne ressemble-t-elle à un titre de section ?"""
    stripped = line.strip()
    if not (3 <= len(stripped) <= 90):
        return False
    if stripped.endswith((".", ",", ";", ":", "?")):
        # Un titre peut finir par ':' mais rarement par '.'
        if not stripped.endswith(":"):
            return False
    words = stripped.split()
    if len(words) > 12:
        return False
    if any(p.match(stripped) for p in _HEADING_PATTERNS):
        return True
    # majoritairement en majuscules et sans point final
    letters = [c for c in stripped if c.isalpha()]
    if letters and sum(1 for c in letters if c.isupper()) / len(letters) > 0.75:
        return True
    return False


def split_sections(text: str) -> list[tuple[str, str]]:
    """Découpe un texte en (titre_de_section, contenu)."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    sections: list[tuple[str, list[str]]] = []
    current_title = "Introduction"
    current_lines: list[str] = []

    for line in lines:
        if looks_like_heading(line):
            if "".join(current_lines).strip():
                sections.append((current_title, current_lines))
            current_title = line.strip().lstrip("#").strip()[:120]
            current_lines = []
        else:
            current_lines.append(line)
    if "".join(current_lines).strip():
        sections.append((current_title, current_lines))

    if not sections:
        return [("Document", text or "")]

    return [
        (title, re.sub(r"\n{3,}", "\n\n", "\n".join(body)).strip())
        for title, body in sections
    ]


def chunk_text(
    text: str,
    *,
    chunk_size_words: int = 420,
    overlap_words: int = 90,
    min_words: int = 40,
    start_index: int = 0,
    section: str = "",
    page: int = 0,
) -> list[Chunk]:
    """Découpe un texte en chunks de ~`chunk_size_words` mots avec chevauchement."""
    words = (text or "").split()
    if not words:
        return []
    chunk_size_words = max(60, int(chunk_size_words))
    overlap_words = max(0, min(int(overlap_words), chunk_size_words - 20))
    step = max(1, chunk_size_words - overlap_words)

    chunks: list[Chunk] = []
    position = 0
    index = start_index
    last_end = 0  # nombre de mots déjà couverts par les chunks produits
    while position < len(words):
        window = words[position : position + chunk_size_words]
        if len(window) < min_words and chunks:
            # Fusionne la fin trop courte avec le chunk précédent, sans dupliquer
            # les mots déjà présents (zone de chevauchement).
            tail = words[last_end:]
            if tail:
                chunks[-1].text = (chunks[-1].text + " " + " ".join(tail)).strip()
                chunks[-1].words = len(chunks[-1].text.split())
            break
        chunks.append(
            Chunk(
                text=" ".join(window).strip(),
                index=index,
                section=section,
                page=page,
                words=len(window),
            )
        )
        index += 1
        last_end = position + len(window)
        if last_end >= len(words):
            break
        position += step
    return chunks


def chunk_document(
    text: str,
    *,
    chunk_size_words: int = 420,
    overlap_words: int = 90,
    min_words: int = 40,
    page: int = 0,
) -> list[Chunk]:
    """Découpe un document en respectant les sections (1 chunk = 1 idée)."""
    chunks: list[Chunk] = []
    for title, body in split_sections(text):
        if len(body.split()) <= chunk_size_words * 1.15:
            if len(body.split()) >= min_words or not chunks:
                chunks.append(
                    Chunk(
                        text=body.strip(),
                        index=len(chunks),
                        section=title,
                        page=page,
                        words=len(body.split()),
                    )
                )
            continue
        new_chunks = chunk_text(
            body,
            chunk_size_words=chunk_size_words,
            overlap_words=overlap_words,
            min_words=min_words,
            start_index=len(chunks),
            section=title,
            page=page,
        )
        chunks.extend(new_chunks)
    return [c for c in chunks if c.text.strip()]


def keywords(text: str, limit: int = 12) -> list[str]:
    """Mots-clés principaux d'un texte, renvoyés sous leur forme lisible.

    Les variantes fléchies (« supports », « support ») sont regroupées par racine,
    mais c'est la forme la plus fréquente — et non la racine — qui est renvoyée,
    afin de rester lisible dans l'interface.
    """
    forms: dict[str, dict[str, int]] = {}
    for surface in re.findall(r"[A-Za-zÀ-ÿ0-9]+", text or ""):
        stem = simple_stem(normalize(surface))
        if not stem or len(stem) < 3 or stem in STOPWORDS:
            continue
        bucket = forms.setdefault(stem, {})
        bucket[surface.lower()] = bucket.get(surface.lower(), 0) + 1

    ranked: list[tuple[str, int]] = []
    for bucket in forms.values():
        meilleure_forme, meilleure_frequence = max(
            bucket.items(), key=lambda item: (item[1], -len(item[0]))
        )
        total = sum(bucket.values())
        ranked.append((meilleure_forme, total if total > meilleure_frequence else meilleure_frequence))
    ranked.sort(key=lambda item: (-item[1], item[0]))
    return [word for word, _ in ranked[:limit]]
