#!/usr/bin/env python3
"""Point de lancement universel.

Fonctionne partout (local, Docker, Render, Railway, Hugging Face Spaces…) :

    python run.py

Variables d'environnement reconnues :

* ``PORT`` (défaut 8000 ; Hugging Face Spaces impose 7860)
* ``HOST`` (défaut 0.0.0.0)
* ``WEB_CONCURRENCY`` (défaut 1)
"""

from __future__ import annotations

import os
import sys

import uvicorn


def main() -> int:
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "0.0.0.0")
    workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
    reload_flag = os.environ.get("RELOAD", "").lower() in {"1", "true", "yes"}

    print("=" * 68)
    print("  TradeVision IA — analyse & prédiction de graphiques de trading")
    print("=" * 68)
    print(f"  Interface web  : http://localhost:{port}")
    print(f"  API (Swagger)  : http://localhost:{port}/api/docs")
    print(f"  Diagnostic     : http://localhost:{port}/api/health")
    print("-" * 68)

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        workers=1 if reload_flag else workers,
        reload=reload_flag,
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
