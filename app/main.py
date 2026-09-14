"""Point d'entrée FastAPI : API REST + interface web.

Lancement local :
    uvicorn app.main:app --reload --port 8000

Lancement type production :
    python run.py            (utilise la variable d'environnement PORT)
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .api.routes import router as api_router
from .config import BASE_DIR, get_settings
from .knowledge import read_stats, sync_all
from .llm import provider_status

logger = logging.getLogger("tradevision")

#: État de l'indexation initiale (visible dans /api/health).
INDEX_STATUS: dict[str, Any] = {
    "etat": "non_initialise",
    "debut": "",
    "fin": "",
    "details": {},
    "erreur": "",
}


def _startup_indexing() -> None:
    """Indexe le corpus d'exemple et les documents importés (en arrière-plan)."""
    settings = get_settings()
    INDEX_STATUS.update({"etat": "en_cours", "debut": time.strftime("%H:%M:%S")})
    try:
        report = sync_all(settings=settings)
        INDEX_STATUS.update(
            {
                "etat": "termine",
                "fin": time.strftime("%H:%M:%S"),
                "details": {
                    "chunks_indexes": report.get("chunks_indexed", 0),
                    "statistiques": report.get("stats", {}),
                },
            }
        )
        logger.info("Indexation terminée : %s chunks", report.get("chunks_indexed", 0))
    except Exception as exc:  # pragma: no cover - dépend de l'environnement
        INDEX_STATUS.update({"etat": "erreur", "erreur": str(exc)})
        logger.warning("Indexation initiale en échec : %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialisation : dossiers, index de connaissances, diagnostics."""
    settings = get_settings()
    settings.ensure_dirs()
    logging.basicConfig(
        level=getattr(logging, str(settings.log_level).upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )
    llm = provider_status(settings)
    logger.info(
        "%s v%s — LLM : %s (%s) | embeddings : %s | données : %s",
        settings.app_name,
        settings.app_version,
        llm["provider_actif"],
        "mode démo" if llm["mode_demo"] else "actif",
        read_stats(settings).get("embedding_provider"),
        settings.market_provider,
    )
    if settings.auto_ingest_on_start:
        threading.Thread(target=_startup_indexing, name="indexation", daemon=True).start()
    else:
        INDEX_STATUS.update({"etat": "desactive"})
    yield


app = FastAPI(
    title="TradeVision IA",
    description=(
        "Analyse et prédiction de graphiques de trading : RAG sur vos cours PDF, "
        "LLM vision et moteur d'indicateurs techniques."
    ),
    version=get_settings().app_version,
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)

# --------------------------------------------------------------------- #
#  Interface web
# --------------------------------------------------------------------- #

WEB_DIR = BASE_DIR / "web"
STATIC_DIR = WEB_DIR / "static"

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(WEB_DIR)) if (WEB_DIR / "index.html").exists() else None


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def home(request: Request):
    """Page principale : analyse, graphique, chat, base de connaissances."""
    from .market import INTERVALS, PERIODS, POPULAR_SYMBOLS

    settings = get_settings()
    context = {
        "request": request,
        "app_name": settings.app_name,
        "app_version": settings.app_version,
        "llm": provider_status(settings),
        "connaissances": read_stats(settings),
        "max_upload_mb": settings.max_upload_mb,
        "default_period": settings.default_period,
        "default_interval": settings.default_interval,
        "default_symbol": "AAPL",
        "symboles": POPULAR_SYMBOLS,
        "periodes": PERIODS,
        "intervalles": INTERVALS,
        "est_espace_huggingface": bool(
            __import__("os").environ.get("SPACE_ID") or __import__("os").environ.get("SPACE_HOST")
        ),
    }
    if templates is None:  # pragma: no cover - filet de sécurité
        return HTMLResponse(
            "<h1>TradeVision IA</h1><p>L'interface web est introuvable (web/index.html). "
            "L'API reste disponible sur <a href='/api/docs'>/api/docs</a>.</p>"
        )
    # Starlette >= 0.29 : la requête est passée en premier argument.
    return templates.TemplateResponse(request, "index.html", context)


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, Any]:
    """Sonde de disponibilité (Hugging Face Spaces, Render, Docker...)."""
    return {"status": "ok", "indexation": INDEX_STATUS.get("etat")}


@app.get("/api/diagnostic", tags=["analyse"], summary="Diagnostic complet (LLM, marché, index)")
def diagnostic() -> dict[str, Any]:
    settings = get_settings()
    return {
        "application": {"nom": settings.app_name, "version": settings.app_version},
        "llm": provider_status(settings),
        "connaissances": read_stats(settings),
        "indexation_au_demarrage": INDEX_STATUS,
        "stockage": {
            "donnees": str(settings.data_path),
            "index": str(settings.vector_path),
            "documents": str(settings.pdf_path),
            "historique": str(settings.reports_db_path),
        },
    }


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:  # pragma: no cover
    """Erreur inattendue : message clair plutôt qu'une trace brute."""
    logger.exception("Erreur non gérée sur %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Une erreur interne est survenue.",
            "erreur": str(exc)[:300],
            "chemin": request.url.path,
        },
    )
