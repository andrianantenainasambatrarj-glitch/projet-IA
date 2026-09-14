"""Configuration centralisée de l'application.

Toutes les valeurs peuvent être surchargées par variables d'environnement
(ou via un fichier .env en local). Voir `.env.example`.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Racine du projet : .../projet-IA
BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Réglages applicatifs (surchargeables par l'environnement)."""

    model_config = SettingsConfigDict(
        env_file=os.getenv("ENV_FILE", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------------------------------------------------------- App
    app_name: str = "TradeVision IA"
    app_version: str = "1.0.0"
    app_description: str = (
        "Analyse et prédiction de graphiques de trading : RAG sur vos cours PDF "
        "+ LLM vision + moteur d'indicateurs techniques."
    )
    debug: bool = False

    # Sécurité optionnelle : si défini, les routes /api/* exigent l'en-tête
    # `X-API-Token` (utile si votre déploiement est public).
    api_access_token: str = ""
    cors_origins: str = "*"  # séparés par des virgules, ex: "https://a.com,https://b.com"
    max_upload_mb: int = 12
    max_question_chars: int = 4000

    # ------------------------------------------------- Chat / LLM vision
    # auto | demo | gemini | openai | anthropic | openrouter | ollama
    llm_provider: str = "auto"
    vision_model: str = ""  # vide -> modèle par défaut du provider
    text_model: str = ""  # vide -> modèle par défaut du provider
    llm_temperature: float = 0.25
    llm_max_tokens: int = 2600
    llm_timeout_s: float = 120.0

    gemini_api_key: str = ""
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    anthropic_api_key: str = ""
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    ollama_base_url: str = "http://localhost:11434"
    ollama_vision_model: str = "qwen2.5vl:7b"

    # ------------------------------------------------------- Embeddings
    # auto | hashing | gemini | openai | local
    # `hashing` = 100 % local, instantané, aucune dépendance lourde (défaut gratuit).
    # `gemini` / `openai` = meilleure qualité sémantique (nécessite une clé API).
    # `local` = sentence-transformers (télécharge ~100 Mo au 1er lancement).
    embedding_provider: str = "auto"
    embedding_model: str = ""  # vide -> modèle par défaut du provider
    embedding_dim: int = 512  # utilisé par le provider "hashing" uniquement
    enable_local_embeddings: bool = True  # autoriser sentence-transformers si installé

    # ------------------------------------------------------------- RAG
    chunk_size_words: int = 420
    chunk_overlap_words: int = 90
    min_chunk_words: int = 40
    top_k: int = 5
    hybrid_alpha: float = 0.55  # 1.0 = 100 % vecteurs ; 0.0 = 100 % BM25 (mots-clés)
    max_context_chars: int = 14000

    # --------------------------------------------------- Base vectorielle
    vector_backend: str = "auto"  # auto | sqlite | chroma

    # ------------------------------------------------------ Données marché
    # auto | yfinance | stooq | demo
    market_provider: str = "auto"
    market_timeout_s: float = 20.0
    default_period: str = "6mo"
    default_interval: str = "1d"
    demo_candles: int = 260  # bougies générées en mode démo hors-ligne

    # ----------------------------------------------------------- Chemins
    data_dir: str = str(BASE_DIR / "data")
    knowledge_dir: str = ""  # défaut: <data_dir>/knowledge
    pdf_dir: str = ""  # défaut: <data_dir>/pdfs
    vector_dir: str = ""  # défaut: <data_dir>/index
    reports_db: str = ""  # défaut: <data_dir>/reports.sqlite3
    auto_ingest_on_start: bool = True

    # ------------------------------------------------------------ Divers
    log_level: str = "INFO"

    # -------------------------------------------------------- Propriétés
    @property
    def cors_origin_list(self) -> list[str]:
        raw = (self.cors_origins or "").strip()
        if not raw or raw == "*":
            return ["*"]
        return [origin.strip() for origin in raw.split(",") if origin.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return max(1, self.max_upload_mb) * 1024 * 1024

    # Chemins résolus (avec création automatique à la demande)
    @property
    def data_path(self) -> Path:
        return Path(self.data_dir).expanduser().resolve()

    @property
    def knowledge_path(self) -> Path:
        return Path(self.knowledge_dir or (self.data_path / "knowledge")).expanduser()

    @property
    def pdf_path(self) -> Path:
        return Path(self.pdf_dir or (self.data_path / "pdfs")).expanduser()

    @property
    def vector_path(self) -> Path:
        return Path(self.vector_dir or (self.data_path / "index")).expanduser()

    @property
    def reports_db_path(self) -> Path:
        return Path(self.reports_db or (self.data_path / "reports.sqlite3")).expanduser()

    def ensure_dirs(self) -> None:
        for path in (
            self.data_path,
            self.knowledge_path,
            self.pdf_path,
            self.vector_path,
            self.reports_db_path.parent,
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Renvoie la configuration (mise en cache)."""
    settings = Settings()
    settings.ensure_dirs()
    return settings


def reset_settings_cache() -> None:
    """Vide le cache de configuration (tests)."""
    get_settings.cache_clear()
