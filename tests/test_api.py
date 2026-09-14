"""Tests de l'API REST, de l'interface web et de la protection par jeton."""

from __future__ import annotations

import base64


def test_page_accueil_et_assets(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "TradeVision" in page.text
    assert "window.APP_CONFIG" in page.text
    assert client.get("/static/style.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/api/docs").status_code == 200
    assert client.get("/healthz").json()["status"] == "ok"


def test_health_et_symbols(client):
    sante = client.get("/api/health").json()
    assert sante["status"] == "ok"
    assert "llm" in sante and "marche" in sante and "connaissances" in sante

    symboles = client.get("/api/symbols").json()
    assert len(symboles["symboles"]) >= 10
    assert "1d" in symboles["intervalles"]


def test_donnees_de_marche_et_rapport(client):
    donnees = client.get("/api/market/AAPL?period=6mo&interval=1d&limit=90").json()
    assert len(donnees["candles"]) == 90
    assert set(donnees["overlays"]) >= {"ema20", "ema50", "sma200"}
    assert len(donnees["rsi14"]) == 90
    assert -100 <= donnees["summary"]["score"] <= 100
    assert donnees["levels"]["period_high"] >= donnees["levels"]["period_low"]

    rapport = client.get("/api/market/AAPL/report")
    assert rapport.status_code == 200
    assert "### Synthèse technique" in rapport.text


def test_symbole_invalide(client):
    reponse = client.get("/api/market/@@invalide@@")
    assert reponse.status_code in {400, 502}


def test_analyse_json_et_export(client):
    reponse = client.post(
        "/api/analyze",
        json={"symbol": "BTC-USD", "period": "6mo", "interval": "1d", "question": "Analyse rapide"},
    )
    assert reponse.status_code == 200, reponse.text
    donnees = reponse.json()
    assert donnees["mode"] == "demo"
    assert donnees["analysis"]["instrument"]["symbol"] in {"BTC-USD", "BTCUSD=X"}
    assert donnees["sources"]
    assert donnees["report_id"]

    detail = client.get(f"/api/reports/{donnees['report_id']}").json()
    assert detail["symbol"]
    export = client.get(f"/api/reports/{donnees['report_id']}/export")
    assert export.status_code == 200 and "Réponse complète" in export.text


def test_analyse_image_multipart_et_json(client):
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
    )
    reponse = client.post(
        "/api/analyze/upload",
        files={"file": ("graphique.png", png, "image/png")},
        data={"symbol": "EURUSD=X", "period": "6mo", "interval": "1d"},
    )
    assert reponse.status_code == 200, reponse.text
    donnees = reponse.json()
    assert donnees["observation"]["available"] is False  # pas de LLM en test
    assert donnees["analysis"]

    en_base64 = client.post(
        "/api/analyze",
        json={"symbol": "", "image_base64": base64.b64encode(png).decode()},
    )
    assert en_base64.status_code == 200


def test_analyse_sans_entree_refusee(client):
    assert client.post("/api/analyze", json={"symbol": ""}).status_code == 400


def test_chat_api(client):
    reponse = client.post("/api/chat", json={"question": "Qu'est-ce que le RSI ?", "top_k": 3})
    assert reponse.status_code == 200
    assert reponse.json()["sources"]


def test_gestion_de_la_base_de_connaissances(client):
    depots = client.post(
        "/api/knowledge/text",
        json={"title": "Règles perso", "content": "Je ne trade que le CAC 40 en D1, stop sous le creux, risque 1 %." * 3},
    )
    assert depots.status_code == 200
    doc_id = depots.json()["resultat"]["doc_id"]

    liste = client.get("/api/knowledge").json()
    assert any(doc["doc_id"] == doc_id for doc in liste["documents"])

    recherche = client.get("/api/knowledge/search?q=stop sous le creux&top_k=3").json()
    assert recherche["resultats"]

    export = client.get(f"/api/knowledge/{doc_id}/export")
    assert export.status_code == 200 and "chunks" in export.text

    suppression = client.delete(f"/api/knowledge/{doc_id}")
    assert suppression.status_code == 200
    assert client.delete(f"/api/knowledge/{doc_id}").status_code == 404

    reindex = client.post("/api/knowledge/reindex", json={"force": True})
    assert reindex.status_code == 200


def test_import_de_fichier_texte(client):
    fichiers = {"files": ("notes.md", b"## Ma fiche\n\nLe RSI mesure le momentum. " * 5, "text/markdown")}
    reponse = client.post("/api/knowledge/upload", files=fichiers)
    assert reponse.status_code == 200
    resultats = reponse.json()["resultats"]
    assert resultats[0]["status"] == "ingested"


def test_historique_des_analyses(client):
    client.post("/api/analyze", json={"symbol": "AAPL"})
    liste = client.get("/api/reports?limit=5").json()
    assert liste["analyses"]
    assert liste["statistiques"]["total"] >= 1

    identifiant = liste["analyses"][0]["report_id"]
    assert client.delete(f"/api/reports/{identifiant}").status_code == 200
    assert client.get(f"/api/reports/{identifiant}").status_code == 404


def test_protection_par_jeton(client, monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    settings.api_access_token = "secret-test"

    assert client.get("/api/health").status_code == 401
    assert client.get("/api/health", headers={"X-API-Token": "secret-test"}).status_code == 200
    assert client.get("/api/health", params={"token": "secret-test"}).status_code == 200
    assert client.get("/").status_code == 200  # l'interface reste publique

    settings.api_access_token = ""


def _pdf_minimal(texte: str) -> bytes:
    """Construit un petit PDF valide contenant du texte (pour tester l'import)."""
    contenu = f"BT /F1 12 Tf 72 720 Td ({texte}) Tj ET".encode("ascii", "replace")
    objets = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(contenu)).encode() + b" >>\nstream\n" + contenu + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    sortie = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, objet in enumerate(objets, start=1):
        offsets.append(len(sortie))
        sortie += f"{index} 0 obj\n".encode() + objet + b"\nendobj\n"
    debut_xref = len(sortie)
    sortie += f"xref\n0 {len(objets) + 1}\n".encode()
    sortie += b"0000000000 65535 f \n"
    for offset in offsets:
        sortie += f"{offset:010d} 00000 n \n".encode()
    sortie += (
        f"trailer\n<< /Size {len(objets) + 1} /Root 1 0 R >>\nstartxref\n{debut_xref}\n%%EOF\n"
    ).encode()
    return bytes(sortie)


def test_import_pdf_glisser_depose_puis_analyse(client):
    """Reproduit le parcours de l'onglet Analyse : PDF déposé → indexé → analyse."""
    pdf = _pdf_minimal("Ma methode de trading sur le CAC 40 en journalier avec stop structure")

    depot = client.post(
        "/api/knowledge/upload",
        files={"files": ("mon-cours.pdf", pdf, "application/pdf")},
    )
    assert depot.status_code == 200, depot.text
    resultat = depot.json()["resultats"][0]
    assert resultat["status"] == "ingested", resultat
    assert resultat["chunks"] >= 1
    assert resultat["kind"] == "pdf"

    # Le PDF importé est immédiatement interrogeable (chat + analyse)
    recherche = client.get(
        "/api/knowledge/search",
        params={"q": "methode de trading CAC 40 journalier stop structure", "top_k": 3},
    ).json()
    titres = [source["title"].replace("-", " ") for source in recherche["resultats"]]
    assert any("mon cours" in titre for titre in titres), titres
    assert titres[0].startswith("mon cours"), "le PDF déposé doit être le mieux classé"

    # ... et il fait partie de la base utilisée par l'analyse RAG
    liste = client.get("/api/knowledge").json()["documents"]
    assert any("mon cours" in doc["title"].replace("-", " ") for doc in liste)
    analyse = client.post(
        "/api/analyze",
        json={"symbol": "AAPL", "interval": "1d", "question": "Quelle est ma méthode de trading ?"},
    ).json()
    assert analyse["sources"], "l'analyse doit toujours injecter des extraits de cours"
