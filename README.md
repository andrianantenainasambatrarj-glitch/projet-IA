# 📈 TradeVision IA

**Analyse et prédiction de graphiques de trading** — moteur technique déterministe +
**RAG sur vos cours (PDF/notes)** + **LLM vision** (lecture de captures d'écran) +
interface web complète et **API REST**.

> Conçu pour tourner **sans configuration** : sans aucune clé API, l'application analyse
> réellement les marchés (tendance, figures, indicateurs, plan de trading avec stop et
> objectifs, prédiction probabiliste) et cite vos cours. Ajoutez une clé LLM **gratuite**
> pour activer la lecture d'image par IA et la rédaction des réponses.

---

## ✨ Fonctionnalités

| Bloc | Détail |
| --- | --- |
| 🔍 **Analyse d'un marché** | Actions, indices, crypto, devises, matières premières (AAPL, ^FCHI, BTC-USD, EURUSD=X, GC=F…) |
| 🖼️ **Analyse d'une capture** | Vous envoyez un screenshot de graphique (TradingView, MT4/5…) : le LLM vision le transcrit (figure, niveaux, tendance, UT), puis l'analyse est croisée avec les données réelles si le symbole est reconnu |
| 🧮 **Moteur technique** | Tendance et structure de marché, pivots, supports/résistances, figures chartistes (ETE, double sommet/creux, triangles, canaux, ranges), figures de bougies (marteau, avalement, étoile du matin…), divergences prix/RSI, cassures avec volume |
| 📐 **Indicateurs** | RSI, MACD, bandes de Bollinger, ATR, stochastique, Williams %R, ADX/DMI, OBV, volumes, profil de volume |
| 🎯 **Prédiction & plan** | Score directionnel −100 → +100, niveau de confiance, scénarios haussier/baissier chiffrés, entrée, stop (ATR + structure), objectifs, ratio R/R, taille de position (règle du 1 %) |
| 📚 **RAG personnel** | Vos PDF/Markdown/notes sont découpés par sections, vectorisés et cités dans la réponse (`[Source n]`) |
| 💬 **Chat documentaire** | Posez une question sur vos cours ; recherche hybride (embeddings + BM25) |
| 🗂️ **Historique** | Chaque analyse est enregistrée, relisible et exportable en Markdown |
| 🔌 **API REST** | Swagger sur `/api/docs` : utilisable depuis n'importe quelle application (mobile, bot, Excel…) |
| 🔒 **Sécurité** | Protection facultative par jeton (`API_ACCESS_TOKEN`), CORS configurable, limites d'upload |

⚠️ **Outil éducatif.** Ce n'est pas un conseil en investissement ; les marchés comportent un
risque de perte en capital.

---

## 🏗️ Architecture

```
┌────────────┐   ┌───────────────────────┐   ┌──────────────────────┐
│ Vos cours  │──▶│ Découpage par sections│──▶│ Embeddings           │
│ PDF / MD   │   │ (1 chunk = 1 concept) │   │ (hachage local ou API)│
└────────────┘   └───────────────────────┘   └──────────┬───────────┘
                                                        ▼
                                          ┌───────────────────────────┐
                                          │ Base vectorielle SQLite    │
                                          │ (ou ChromaDB, optionnel)   │
                                          └────────────┬──────────────┘
                                                       │
┌────────────────┐   ┌───────────────────┐   ┌─────────▼─────────┐
│ Capture écran  │──▶│ LLM vision        │──▶│ Recherche hybride │
│ ou symbole     │   │ → description JSON│   │ vecteurs + BM25   │
└────────────────┘   └───────────────────┘   └─────────┬─────────┘
                                                       ▼
┌────────────────┐   ┌───────────────────┐   ┌───────────────────┐
│ Moteur technique│──▶│ Prompt structuré │──▶│ Réponse finale    │
│ (déterministe) │   │ (méthode du cours)│   │ + citations + API │
└────────────────┘   └───────────────────┘   └───────────────────┘
```

**Choix techniques (et pourquoi)**

- **Le moteur technique calcule avant le LLM** : tous les chiffres (niveaux, indicateurs,
  stop, objectifs) sont produits par du code déterministe. Le LLM ne fait que *rédiger et
  interpréter*. Résultat : pas d'hallucination de prix, et l'application reste utile sans
  clé API.
- **Embeddings par hachage + BM25 par défaut** : zéro téléchargement, démarrage instantané,
  gratuit. `EMBEDDING_PROVIDER=gemini|openai|local` améliore la recherche sémantique.
- **Base vectorielle SQLite** : un simple fichier, aucun serveur à gérer (ChromaDB en option).
- **Repli en cascade pour les données de marché** : yfinance → API Yahoo → Stooq → séries de
  démonstration clairement signalées. L'application ne tombe jamais en panne d'affichage.
- **Aucune dépendance front externe** : l'interface (HTML/CSS/JS natif) fonctionne même si les
  CDN sont bloqués.

---

## 🚀 Démarrage local

```bash
git clone https://github.com/andrianantenainasambatrarj-glitch/projet-IA.git
cd projet-IA

python -m venv .venv
source .venv/bin/activate          # Windows : .venv\Scripts\activate

pip install -r requirements.txt
python run.py                      # http://localhost:8000
```

Options utiles :

```bash
pip install -r requirements-optional.txt   # yfinance, sentence-transformers, chromadb, pillow
cp .env.example .env                       # puis renseignez GEMINI_API_KEY=…
python run.py
```

Interface : <http://localhost:8000> · API : <http://localhost:8000/api/docs>

---

## 🌍 Déployer gratuitement en ligne

### Option A — Hugging Face Spaces (recommandée, gratuite, HTTPS inclus)

1. Créez un Space : <https://huggingface.co/new-space> → **SDK : Docker**, port **7860**.
2. Créez un token Hugging Face avec droit *write* (Settings → Access Tokens).
3. Dans GitHub → **Settings → Secrets and variables → Actions** :
   - *Secret* `HF_TOKEN` = votre token Hugging Face
   - *Variable* `HF_SPACE` = `votre-pseudo/tradevision-ia`
4. Activez la synchronisation automatique en installant le workflow livré :

   ```bash
   mkdir -p .github/workflows
   cp deploy/github-workflow-sync-hf-space.yml .github/workflows/sync-hf-space.yml
   git add .github/workflows && git commit -m "ci: déploiement Hugging Face" && git push
   ```

   À chaque push sur `main`, le Space est mis à jour automatiquement. Votre app est en ligne
   sur `https://huggingface.co/spaces/<votre-pseudo>/tradevision-ia`.
5. Pour activer la vision : Space → **Settings → Variables and secrets** → secret
   `GEMINI_API_KEY` = votre clé (gratuite sur <https://aistudio.google.com/apikey>).

> Pour pousser manuellement sur le Space :
> ```bash
> git remote add space https://huggingface.co/spaces/<pseudo>/tradevision-ia
> git push space main
> ```

### Option B — Render (gratuit, HTTPS)

1. Poussez ce dépôt sur GitHub.
2. Render → **New → Blueprint** → sélectionnez le dépôt : le fichier `render.yaml` est
   détecté automatiquement.
3. Ajoutez les variables secrètes (`GEMINI_API_KEY`, éventuellement `API_ACCESS_TOKEN`).
4. Déploiement en quelques minutes sur `https://<nom>.onrender.com`.

### Option C — Docker (n'importe où : VPS, Koyeb, Fly.io, Railway, votre machine)

```bash
docker compose up --build      # http://localhost:7860
# ou
docker build -t tradevision .
docker run -p 7860:7860 -e GEMINI_API_KEY=xxx -v tradevision-data:/data tradevision
```

### Recommandations de production

- Ajoutez `API_ACCESS_TOKEN` si votre URL est publique (l'interface reste accessible, l'API
  demande le jeton `X-API-Token`).
- Montez un volume persistant sur `DATA_DIR` pour conserver l'index et l'historique.
- Les plans gratuits s'endorment après inactivité : le premier appel peut prendre ~30 s.

---

## 🔑 Fournisseurs LLM supportés

| Fournisseur | Variable | Modèle par défaut | Notes |
| --- | --- | --- | --- |
| **Google Gemini** ⭐ | `GEMINI_API_KEY` | `gemini-2.5-flash` | Clé gratuite, vision + texte, excellent rapport qualité/prix |
| OpenAI | `OPENAI_API_KEY` | `gpt-4o-mini` | `OPENAI_BASE_URL` pour tout endpoint compatible |
| Anthropic | `ANTHROPIC_API_KEY` | `claude-sonnet-4-5` | Vision + texte |
| OpenRouter | `OPENROUTER_API_KEY` | modèles gratuits | Accès à de nombreux modèles |
| Ollama (local) | `OLLAMA_BASE_URL` | `qwen2.5vl:7b` | 100 % hors-ligne si le modèle est installé |
| **Démo** | *(aucune)* | — | Moteur technique + RAG, sans rédaction IA |

Forcez un fournisseur avec `LLM_PROVIDER=gemini|openai|anthropic|openrouter|ollama|demo`,
et choisissez les modèles via `VISION_MODEL` / `TEXT_MODEL`.

---

## 📚 Utiliser vos propres cours

1. Onglet **Base de connaissances** → importez vos PDF/Markdown/notes (ou collez du texte).
2. L'indexation découpe par sections, crée les vecteurs et met tout à disposition du chat et
   de l'analyse ; le bouton **Réindexer tout** relance la synchronisation après un import.
3. Le corpus d'exemple fourni (`knowledge/`, 5 chapitres en français) sert de démonstration :
   supprimez-le ou remplacez-le par vos supports.

> 💡 Le RAG ne « mange » pas les PDF scannés (images) : il faut du texte sélectionnable
> (passez par un OCR au préalable si besoin).

---

## 🔌 Exemples d'appels API

```bash
# Analyse d'un symbole avec question
curl -X POST http://localhost:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"symbol":"AAPL","period":"6mo","interval":"1d",
       "question":"Où placer mon stop et mes objectifs ?"}'

# Analyse d'une capture de graphique
curl -X POST http://localhost:8000/api/analyze/upload \
  -F "file=@ma_capture.png" -F "symbol=BTC-USD" -F "interval=4h"

# Chat sur vos cours
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"question":"Comment détecter une divergence RSI ?"}'

# Données + séries pour vos propres graphiques
curl "http://localhost:8000/api/market/EURUSD=X?period=6mo&interval=1d&limit=200"

# Importer un PDF dans la base de connaissances
curl -X POST http://localhost:8000/api/knowledge/upload -F "files=@mon_cours.pdf"
```

Si `API_ACCESS_TOKEN` est défini, ajoutez l'en-tête `-H "X-API-Token: votre_jeton"`.

| Méthode | Route | Rôle |
| --- | --- | --- |
| `POST` | `/api/analyze` | Analyse complète (symbole et/ou image base64) |
| `POST` | `/api/analyze/upload` | Analyse depuis un fichier image |
| `POST` | `/api/chat` | Question sur vos cours (RAG) |
| `GET` | `/api/market/{symbole}` | Bougies, moyennes, Bollinger, RSI, niveaux, résumé |
| `GET` | `/api/market/{symbole}/report` | Rapport technique brut (Markdown) |
| `GET`/`POST`/`DELETE` | `/api/knowledge…` | Liste, import, suppression, export, réindexation, recherche |
| `GET`/`DELETE` | `/api/reports…` | Historique des analyses (+ export Markdown) |
| `GET` | `/api/health`, `/api/diagnostic` | Diagnostic complet (LLM, marché, index) |

---

## 🧪 Tests

```bash
pip install pytest
python -m pytest tests -q
```

57 tests couvrent : indicateurs techniques, découpage/ingestion, base vectorielle,
recherche hybride (y compris le repli BM25), moteur d'analyse, couche vision, services
d'orchestration, API REST, protection par jeton et interface web. Aucun test ne nécessite
Internet ni clé API.

---

## ⚙️ Configuration (extrait)

Toutes les variables sont listées et commentées dans `.env.example`.

| Variable | Défaut | Rôle |
| --- | --- | --- |
| `LLM_PROVIDER` | `auto` | `auto`, `demo`, `gemini`, `openai`, `anthropic`, `openrouter`, `ollama` |
| `EMBEDDING_PROVIDER` | `auto` | `hashing` (local instantané), `gemini`, `openai`, `local` |
| `VECTOR_BACKEND` | `auto` | `sqlite` (défaut) ou `chroma` |
| `MARKET_PROVIDER` | `auto` | `auto`, `yfinance`, `yahoo`, `stooq`, `demo` |
| `CHUNK_SIZE_WORDS` / `CHUNK_OVERLAP_WORDS` | `420` / `90` | Taille des extraits indexés |
| `TOP_K` | `5` | Nombre d'extraits de cours injectés dans le prompt |
| `HYBRID_ALPHA` | `0.55` | Poids des vecteurs dans la recherche (1 = vecteurs seuls) |
| `DATA_DIR` | `./data` | Index, documents importés, historique |
| `API_ACCESS_TOKEN` | *(vide)* | Si défini, protège `/api/*` |

---

## 📁 Structure du projet

```
projet-IA/
├─ app/
│  ├─ main.py             # application FastAPI + interface web
│  ├─ config.py           # configuration (variables d'environnement)
│  ├─ analysis.py         # moteur technique (tendance, figures, plan, score)
│  ├─ indicators.py       # indicateurs en Python pur (RSI, MACD, ATR, ADX…)
│  ├─ market.py           # données de marché + repli démo hors-ligne
│  ├─ vision.py           # description structurée des captures de graphiques
│  ├─ llm.py              # Gemini / OpenAI / Anthropic / OpenRouter / Ollama
│  ├─ knowledge.py        # ingestion PDF/Markdown, embeddings, index
│  ├─ retriever.py        # recherche hybride (vecteurs + BM25) et contexte
│  ├─ vectorstore.py      # base vectorielle SQLite (ou ChromaDB)
│  ├─ embeddings.py       # embeddings locaux (hachage) ou API
│  ├─ bm25.py             # recherche lexicale
│  ├─ reports.py          # historique des analyses
│  ├─ prompts.py          # prompts d'analyse et de chat (méthode, citations)
│  ├─ api/                # routes REST + sécurité
│  └─ services/           # orchestration analyse et chat
├─ web/                   # interface (HTML + CSS + JS natif)
├─ knowledge/             # cours d'exemple indexés au démarrage
├─ tests/                 # 57 tests hors-ligne
├─ Dockerfile, docker-compose.yml, render.yaml, Procfile
├─ deploy/github-workflow-sync-hf-space.yml   # modèle de workflow (à copier dans .github/workflows/)
└─ hf_space/README.md                         # en-tête du Space Hugging Face
```

---

## 🛠️ Dépannage

| Symptôme | Cause probable / solution |
| --- | --- |
| « Mode démo (sans clé LLM) » | Aucune clé détectée : ajoutez `GEMINI_API_KEY` puis redémarrez le service |
| « Données de démonstration » | Yahoo/Stooq inaccessibles depuis l'hébergeur, ou `MARKET_PROVIDER=demo` |
| Réponses lentes au premier appel | Plan gratuit en veille (cold start) ou téléchargement du modèle d'embeddings |
| PDF ignoré (« aucun texte exploitable ») | PDF scanné : passez-le par un OCR avant import |
| Recherche peu pertinente | Importez des cours plus spécifiques, ou passez à des embeddings API (`EMBEDDING_PROVIDER=gemini`) |
| `401 Accès refusé` sur l'API | `API_ACCESS_TOKEN` est défini : envoyez `X-API-Token` |
| Erreur d'indexation au démarrage | Consultez `/api/diagnostic` (champ `indexation_au_demarrage`) |

---

## ⚠️ Avertissement

TradeVision IA est un outil pédagogique d'aide à la décision. Il ne fournit ni conseil en
investissement, ni recommandation personnalisée, ni garantie de résultat. Les marchés
financiers présentent un risque de perte totale ou partielle du capital : l'utilisateur reste
seul responsable de ses décisions et de leur conformité aux réglementations applicables.

## 📄 Licence

MIT — réutilisation libre, y compris commerciale, sans garantie.
