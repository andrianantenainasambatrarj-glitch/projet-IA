# 🚀 Guide de déploiement complet — TradeVision IA

Guide pas-à-pas, écrit pour être suivi **dans l'ordre**, sans connaissance préalable de
Docker ni de Linux. À la fin, votre application est en ligne, sécurisée et fonctionnelle.

---

## 0. Réponse à la question : **Render ou Hugging Face ?**

> Vérifié sur la documentation officielle en septembre 2026.

### Le point qui décide tout

**Hugging Face a changé sa politique en 2026** : les Spaces **Docker** et **Gradio** ne
peuvent plus être **créés** avec un compte gratuit. D'après la documentation officielle :

> « Static Spaces are free for everyone. **Gradio and Docker Spaces run on compute and
> require a paid plan to create**: PRO for personal accounts, Team or Enterprise for
> organizations. » — <https://huggingface.co/docs/hub/spaces-overview>

Concrètement, sur un compte **gratuit**, vous ne pouvez créer que des Spaces **Static**
(pages HTML/JS sans serveur) : c'est incompatible avec votre application FastAPI.
Le plan **PRO coûte 9 $/mois**.

### Comparatif honnête

| Critère | **Render (gratuit)** | **Hugging Face (gratuit)** | Hugging Face **PRO** (9 $/mois) |
| --- | --- | --- | --- |
| Peut héberger votre appli FastAPI | ✅ oui | ❌ non (Static seulement) | ✅ oui (Space Docker) |
| Carte bancaire requise | Non | Non | Oui |
| Ressources | 0,1 CPU / 512 Mo RAM | 2 vCPU / 16 Go (mais Docker payant) | 2 vCPU / 16 Go |
| Mise en veille | après **15 min** sans visite | après 48 h | après 48 h |
| Réveil (cold start) | ~30–60 s | ~30–90 s | ~30–90 s |
| Disque persistant | ❌ (système de fichiers éphémère) | ❌ (50 Go éphémères) | Option payante (dès 5 $/mois) |
| Travaux planifiés (cron) | ❌ sur le plan gratuit | — | — |
| HTTPS + domaine | ✅ gratuit (`*.onrender.com`) | ✅ (`*.hf.space`) | ✅ + domaine personnalisé |
| Quota | 750 h/mois par espace de travail | illimité (fair use) | illimité |

### ✅ Recommandation

**Déployez sur Render (plan gratuit). C'est la seule des deux options vraiment gratuite qui
fait tourner votre application.** Ses limites se gèrent sans payer :

| Limite de Render | Conséquence pour TradeVision IA | Solution mise en place |
| --- | --- | --- |
| Mise en veille après 15 min | 1ʳᵉ visite lente (~1 min) | Normal pour un outil perso ; sinon UptimeRobot gratuit (voir § 7) |
| Disque éphémère | Index, documents importés et historique remis à zéro après un redémarrage | Le corpus de démarrage est **réindexé automatiquement** ; pour vos PDF, réimportez-les (10 s) ou passez au plan payant (disque persistant 0,25 $/Go) |
| Pas de cron sur le plan gratuit | La veille automatique s'arrête quand le service dort | Utilisez `python -m app.notifications --digest` depuis un cron externe gratuit, ou le déclenchement manuel dans l'interface |
| 750 h/mois | Largement suffisant pour un usage personnel (une instance 24/7 ≈ 720 h) | Ne lancez pas deux services gratuits en parallèle |

**Hugging Face reste un excellent choix si vous prenez le plan PRO (9 $/mois)** : pour votre
usage, Render gratuit est le meilleur rapport simplicité/prix. Le workflow d'automatisation
vers un Space est fourni (`deploy/github-workflow-sync-hf-space.yml`) si vous passez un jour
sur PRO or Team.

---

## 1. Pré-requis (5 minutes)

| Élément | Obligatoire ? | Où l'obtenir |
| --- | --- | --- |
| Compte GitHub | ✅ | <https://github.com> (vous l'avez déjà) |
| Compte Render | ✅ | <https://render.com> — inscription avec GitHub, **sans carte bancaire** |
| Clé API Gemini | ⭐ Recommandée | <https://aistudio.google.com/apikey> — **gratuite**, active la vision + la rédaction |
| Application Telegram | Optionnel | Pour recevoir les analyses sur téléphone (§ 6) |

Le projet **fonctionne sans clé** (mode démo : moteur technique + vos cours). La clé Gemini
ajoute la lecture d'image par IA et la rédaction des réponses.

---

## 2. Mettre le code sur GitHub (10 minutes)

### 2.1 Cas A — vous n'avez pas encore fusionné la Pull Request

1. Ouvrez <https://github.com/andrianantenainasambatrarj-glitch/projet-IA/pull/1>
2. Cliquez **Merge pull request** → **Confirm merge**
3. Le code se retrouve sur la branche `main` : c'est ce que Render va déployer.

> Vous pouvez aussi fusionner depuis votre machine :
> ```bash
> git checkout main && git pull
> git merge arena/01a09f32-projet-ia
> git push origin main
> ```

### 2.2 Cas B — vous déployez directement depuis la branche

Render accepte n'importe quelle branche : au § 4, choisissez
`arena/01a09f32-projet-ia` au lieu de `main`. Pratique pour tester avant de fusionner.

### 2.3 Vérification

Sur GitHub, la racine du dépôt doit contenir : `app/`, `web/`, `knowledge/`, `tests/`,
`requirements.txt`, `render.yaml`, `Dockerfile`, `README.md`.

---

## 3. Créer la clé Gemini gratuite (3 minutes) — recommandé

1. Allez sur <https://aistudio.google.com/apikey> et connectez-vous avec un compte Google.
2. Cliquez **Create API key** → **Create API key in new project**.
3. Copiez la clé (elle commence par `AIza…`) et **gardez-la pour le § 4**.

> ⚠️ Ne collez jamais cette clé dans un fichier poussé sur GitHub. Elle ne doit vivre que
> dans les variables d'environnement du service (étape 4.4) ou dans un fichier `.env` local
> (déjà exclu par `.gitignore`).

---

## 4. Déployer sur Render (10 minutes)

### 4.1 Créer le service

1. Connectez-vous à <https://dashboard.render.com> avec votre compte GitHub.
2. Cliquez **New +** → **Blueprint**.
3. Sélectionnez le dépôt `projet-IA` → **Connect**.
4. Render lit `render.yaml` et propose un service nommé **tradevision-ia** → **Apply**.

> **Alternative** si le Blueprint ne vous convient pas : **New + → Web Service** →
> dépôt `projet-IA` → *Runtime* : **Python 3** → *Build Command* :
> `pip install --upgrade pip && pip install -r requirements.txt` →
> *Start Command* : `python run.py` → *Plan* : **Free**.

### 4.2 Attendre la compilation

Comptez **3 à 6 minutes**. Dans l'onglet *Logs*, vous devez voir à la fin :

```
TradeVision IA v1.0.0 — LLM : demo (mode démo) | embeddings : hash-512d | données : auto
Indexation terminée : 41 chunks
Uvicorn running on http://0.0.0.0:10000
```

### 4.3 Tester

Ouvrez `https://tradevision-ia.onrender.com` (le nom exact est affiché en haut de la page
Render). L'interface doit s'afficher avec la bannière « Mode démo actif ». Testez
**« Exemple guidé (AAPL) »** : l'analyse doit produire un verdict, des niveaux, des figures et
des extraits de cours.

### 4.4 Ajouter vos variables (clé API, notifications…)

Onglet **Environment** du service → **Add Environment Variable** :

| Variable | Valeur | Effet |
| --- | --- | --- |
| `GEMINI_API_KEY` | `AIza…` | Active la lecture d'image et la rédaction par IA |
| `API_ACCESS_TOKEN` | *(chaîne secrète de votre choix)* | Protège l'API `/api/*` |
| `APP_BASE_URL` | `https://tradevision-ia.onrender.com` | Ajouté en bas des messages envoyés |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | voir § 6 | Envoi des analyses sur Telegram |
| `NOTIFY_ENABLED` | `true` | Active la veille automatique (si un canal est configuré) |
| `WATCHLIST` | `AAPL,BTC-USD,^FCHI` | Marchés suivis |
| `EMBEDDING_PROVIDER` | `gemini` | Recherche sémantique de meilleure qualité (utilise la clé Gemini) |

Cliquez **Save Changes** : Render redéploie automatiquement (≈ 3 min).

### 4.5 Vérifier l'état complet

Ouvrez `https://<votre-app>.onrender.com/api/health`. Vous devez voir :

```json
{ "status": "ok",
  "llm": { "provider_actif": "gemini", "mode_demo": false, "vision_disponible": true },
  "connaissances": { "documents": 5, "chunks": 41 },
  "notifications": { "pret": true },
  "veille": { "actif": true } }
```

- `mode_demo: false` → la clé Gemini est bien prise en compte 🎉
- `notifications.pret: true` → Telegram/webhook opérationnel
- `veille.actif: true` → la veille tourne

---

## 5. Rendre le site « professionnel »

### 5.1 Nom et apparence

- **Nom du service** : Render → *Settings* → *Name* (change l'URL `*.onrender.com`).
- **Nom affiché de l'application** : variable `APP_NAME` = `Mon Analyse Trading IA`.
- **Votre propre domaine** (ex. `analyse.mondomaine.com`) : Render → *Settings* →
  *Custom Domains* → ajoutez l'enregistrement CNAME chez votre registrar. HTTPS automatique.

### 5.2 Sécurité

1. **Définissez `API_ACCESS_TOKEN`** dès que l'URL est publique : l'interface reste ouverte,
   mais les appels `/api/*` exigent l'en-tête `X-API-Token` (ou `?token=…`).
2. **Ne commitez jamais** `.env` ni de clé API (`.gitignore` les couvre déjà).
3. **CORS** : laissez `CORS_ORIGINS=*` si vous consommez l'API depuis des outils variés,
   sinon restreignez à votre domaine.
4. Vérifiez que le dépôt GitHub est **privé** si le code ne doit pas être public.

### 5.3 Contenu et crédibilité

1. **Importez vos vrais cours** : onglet *Base de connaissances* → glissez vos PDF.
   L'analyse citera alors votre méthode (`[Source 1]`…). Supprimez si besoin les documents
   d'exemple via le bouton *Supprimer*.
2. **Ajoutez l'avertissement légal** : il est déjà présent dans chaque réponse et dans
   l'onglet *Aide*. Complétez-le si vous exercez une activité réglementée.
3. **Publiez vos points forts** : le README GitHub + la capture d'écran de l'onglet *Analyse*
   suffisent à présenter le projet.

### 5.4 Suivi et maintenance

| Besoin | Solution gratuite |
| --- | --- |
| Éviter la mise en veille | UptimeRobot → *New Monitor* → HTTP(s) → URL `/healthz` → toutes les 5 min |
| Voir les erreurs | Render → onglet *Logs* (temps réel) |
| Vérifier l'état | `/api/health`, `/api/diagnostic`, `/healthz` |
| Sauvegarder | Vos sources sont sur GitHub ; l'index se régénère tout seul |
| Mettre à jour | `git push` sur la branche déployée → Render redéploie automatiquement |

---

## 6. Recevoir les analyses sur Telegram (5 minutes)

1. Dans Telegram, cherchez **@BotFather** → envoyez `/newbot` → choisissez un nom.
   BotFather renvoie un **jeton** (`123456789:AAE…`).
2. Cherchez votre nouveau bot (le lien est fourni) et envoyez-lui **`/start`**.
3. Récupérez votre **chat id** : ouvrez dans un navigateur
   `https://api.telegram.org/bot<VOTRE_JETON>/getUpdates`
   et notez la valeur de `result[0].message.chat.id` (un nombre, parfois négatif).
4. Render → *Environment* → ajoutez `TELEGRAM_BOT_TOKEN` et `TELEGRAM_CHAT_ID` → *Save*.
5. Dans l'application, onglet **🔔 Alertes & veille** → **Envoyer un message de test** :
   vous recevez « ✅ Test de notification ».
6. Configurez la veille :
   - *Marchés suivis* : `AAPL,BTC-USD,^FCHI,EURUSD=X`
   - *Intervalle* : `240` minutes (4 h)
   - *Seuil de score* : `20` (n'alerter que sur les situations marquées)
   - **Démarrer la veille**.

**Sans Telegram**, utilisez `NOTIFY_WEBHOOK_URL` :
Discord (*Paramètres du salon → Intégrations → Webhooks*), Slack, ou ntfy
(`https://ntfy.sh/mon-sujet` — notifie sur téléphone via l'app ntfy).

**Ligne de commande / cron externe** (utile quand le service gratuit dort) :

```bash
python -m app.notifications --test                 # vérifie la configuration
python -m app.notifications --digest               # scan de la watchlist + envoi
python -m app.notifications --symbols AAPL,BTC-USD # analyse ciblée + envoi
python -m app.notifications --dry-run              # affiche le message sans l'envoyer
```

Sur Render, un **Cron Job** n'est pas disponible sur le plan gratuit : vous pouvez appeler
`POST /api/notifications/watchlist` depuis un service de cron gratuit externe
(cron-job.org, GitHub Actions planifié) avec l'en-tête `X-API-Token`.

---

## 7. Options de déploiement alternatives

### 7.1 Hugging Face Spaces (si vous prenez PRO à 9 $/mois)

1. Créez un Space : <https://huggingface.co/new-space> → SDK **Docker** → port **7860**.
2. Token Hugging Face avec droit *write* (Settings → Access Tokens).
3. GitHub → *Settings → Secrets and variables → Actions* :
   - secret `HF_TOKEN` = votre token
   - variable `HF_SPACE` = `votre-pseudo/tradevision-ia`
4. Installez le workflow livré puis poussez :
   ```bash
   mkdir -p .github/workflows
   cp deploy/github-workflow-sync-hf-space.yml .github/workflows/sync-hf-space.yml
   git add .github/workflows && git commit -m "ci: déploiement Hugging Face" && git push
   ```
5. Secrets du Space : *Settings → Variables and secrets* → `GEMINI_API_KEY`, etc.

### 7.2 Docker local (si vous changez d'avis sur l'installation locale)

```bash
docker compose up --build        # http://localhost:7860
```

### 7.3 Autres hébergeurs gratuits

Le projet est un simple service web Python/Docker : il fonctionne aussi sur **Koyeb**,
**Fly.io** (crédit gratuit limité), **Railway** (crédit d'essai), ou tout VPS.
Commandes : *build* `pip install -r requirements.txt`, *start* `python run.py`.

---

## 8. Après le déploiement : contrôle de qualité (checklist)

- [ ] La page d'accueil s'affiche et la bannière d'état est correcte
- [ ] `/api/health` renvoie `"status": "ok"` et le bon fournisseur LLM
- [ ] L'analyse d'un symbole (ex. `AAPL`) produit un verdict, des niveaux et des figures
- [ ] Un glisser-déposer de **PDF** dans l'onglet *Analyse* indexe le document et l'analyse
      s'appuie dessus
- [ ] Le **chat** répond avec des citations `[Source n]`
- [ ] L'**historique** enregistre chaque analyse et l'export Markdown fonctionne
- [ ] Un **message de test** Telegram arrive (si configuré)
- [ ] La veille affiche son état et une date de dernier passage
- [ ] `API_ACCESS_TOKEN` est défini si l'URL est publique
- [ ] L'avertissement « pas un conseil en investissement » apparaît dans les réponses

---

## 9. Dépannage

| Symptôme | Cause | Solution |
| --- | --- | --- |
| `Application failed to respond` au bout de 60 s | Plan gratuit réveillé / build incomplet | Attendez 1 min, rechargez ; vérifiez les *Logs* |
| Toujours « Mode démo » après avoir mis la clé | Variable non enregistrée ou service non redéployé | Vérifiez `Environment`, puis *Manual Deploy → Deploy latest commit* |
| `403` / erreur Gemini | Clé invalide, quota atteint ou région non autorisée | Régénérez la clé sur AI Studio ; testez `curl /api/health` |
| Données « démo » alors que le réseau marche | Yahoo/Stooq bloqués depuis l'hébergeur | `MARKET_PROVIDER=yahoo` ou `yfinance` après `pip install yfinance`, sinon acceptez le mode démo |
| Documents perdus après un redémarrage | Disque éphémère du plan gratuit | Réimportez vos PDF, ou passez à un disque persistant payant, ou utilisez un stockage externe |
| Aucun message Telegram | Le bot n'a jamais reçu `/start`, ou chat id erroné | Renvoyez `/start` au bot puis relisez `getUpdates` |
| Veille inactive | `NOTIFY_ENABLED=true` mais aucun canal, ou service endormi | Configurez Telegram/webhook ; gardez le service éveillé (UptimeRobot) ou passez par un cron externe |
| Les PDF scannés sont ignorés | PDF image sans couche texte | Faites un OCR (Adobe, OCRmyPDF) puis réimportez |
| `401 Accès refusé` en appelant l'API | `API_ACCESS_TOKEN` défini | Ajoutez l'en-tête `X-API-Token: votre_jeton` |

---

## 10. Récapitulatif express (les 8 étapes essentielles)

1. **Fusionner** la Pull Request #1 dans `main` (ou déployer la branche telle quelle).
2. Créer un compte **Render** (gratuit, sans carte).
3. Créer une clé **Gemini** gratuite (aistudio.google.com/apikey).
4. Render → **New + → Blueprint** → dépôt `projet-IA` → **Apply**.
5. Attendre le build (≈ 3–6 min) puis ouvrir l'URL `*.onrender.com`.
6. Ajouter `GEMINI_API_KEY` (+ `API_ACCESS_TOKEN`, `APP_BASE_URL`) dans *Environment*.
7. (Optionnel) Configurer **Telegram** et démarrer la **veille** dans l'onglet *Alertes*.
8. Parcourir la **checklist du § 8** et importer vos propres cours.

Bon déploiement ! 🚀
