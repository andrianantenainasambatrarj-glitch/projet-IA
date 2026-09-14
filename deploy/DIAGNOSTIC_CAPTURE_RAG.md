# Diagnostic — « ma capture EUR/USD a été analysée comme du AAPL »

**Projet :** TradeVision IA · **Date :** 14 septembre 2026 · **Correctif :** commit `23bc8b2`
(branche `arena/01a09f32-projet-ia`)

---

## 1. Le symptôme, tel qu'il a été observé

Une capture d'écran d'un graphique **EUR/USD** a été envoyée, et le compte rendu affichait :

```
AAPL · Données : yfinance · 127 bougies
Analyse IA · gemini gemini-3.6-flash
1. Lecture du graphique — Actif et horizon : AAPL (USD), unité de temps journalière (1d),
127 bougies analysées. Dernier prix à 332.27 USD…
```

Aucun message ne signalait que la capture ne correspondait pas aux chiffres. En clair :
**l'application parlait d'Apple alors que l'image montrait de l'euro-dollar.**

---

## 2. La chaîne réelle, étage par étage

```
  [1] Interface            [2] Serveur                 [3] Moteur technique      [4] Vision (LLM)
  formulaire ────────────► /api/analyze ─────────────► get_instrument(symbole)──┐
  capture (base64)         validate_image()            analyse() indicateurs    │
                                                    ┌───────────────────────────┘
                                                    ▼
  [5] Base de cours (RAG)  ◄──── requêtes de recherche ──── lecture de l'image
        extraits cités              (hybride / BM25)
                                                    │
                                                    ▼
  [6] Rédaction finale (LLM) ───► réponse + sources + avertissements ───► [7] Interface
```

Chaque étage a été vérifié en exécutant réellement la chaîne (avec un faux fournisseur
d'IA qui enregistre **tout** ce qui lui est envoyé). Résultat de cette instrumentation :

| Étage | Verdict | Preuve |
|---|---|---|
| 1 → 2 · La capture part-elle au serveur ? | **OK** | La requête contient bien `image_base64` (data URL décodée par `decode_image_payload`). |
| 2 · Format et taille | **OK** | `validate_image` réduit la capture à 1600 px de côté si Pillow est présent. |
| 2 → 4 · L'image part-elle au modèle vision ? | **OK** | Trace : `json_mode=True use_vision=True images=1 octets=[2946]`. |
| 4 · La lecture de l'image est-elle structurée ? | **OK** | JSON obtenu : actif, unité de temps, tendance, figures, niveaux, incertitudes. |
| 4 → 6 · La lecture est-elle réinjectée dans la rédaction ? | **OK** | Le prompt contient la section « LECTURE DE LA CAPTURE PAR LE MODÈLE VISION ». |
| 5 · Le RAG utilise-t-il la lecture de l'image ? | **OK** | `observation.rag_query` (« EUR/USD \| H1 \| baissière \| tête-épaules… ») figure dans les requêtes. |
| **6 · Confronte-t-on l'actif de l'image et le symbole saisi ?** | **NON — cause n°1** | Aucune comparaison n'existait dans le code : le prompt annonçait « DONNÉES DE MARCHÉ : AAPL » et le modèle répondait sur AAPL. |
| **4/6 · « EUR/USD » est-il reconnu comme un symbole exploitable ?** | **NON — cause n°2** | `guess_symbol("EUR/USD…")` renvoyait **une chaîne vide** (voir ci-dessous). |
| **7 · L'interface montre-t-elle la lecture de l'image ?** | **NON — cause n°3** | Le champ `observation` était renvoyé par l'API… et affiché nulle part. |

**Conclusion : la transmission n'était pas cassée — c'est la *confrontation* et la
*traçabilité* qui manquaient.** Le modèle voyait bien deux actifs différents, mais rien
ne lui demandait de les comparer, et rien ne vous permettait de vous en apercevoir.

---

## 3. Les trois causes, précisément

### Cause n°1 — Aucune confrontation entre la capture et les données chiffrées

Dans `app/services/analysis_service.py`, le prompt final présentait :

```
CONTEXTE — DONNÉES DE MARCHÉ
- Symbole : AAPL (Apple Inc.), devise USD…

CONTEXTE — RAPPORT TECHNIQUE CALCULÉ (moteur déterministe, chiffres fiables)
### Instrument : AAPL …

CONTEXTE — LECTURE DE L'IMAGE PAR LE MODÈLE VISION
- Marché / symbole estimé : EUR/USD
```

Le rapport chiffré était présenté comme **la référence** (« chiffres fiables ») et la
lecture de l'image comme un contexte secondaire. Aucune phrase ne disait : « si l'image
montre autre chose, dis-le et analyse l'image ». Le modèle a donc fait ce que le prompt
lui demandait : rédiger une analyse d'AAPL.

### Cause n°2 — « EUR/USD » n'était reconnu par aucune règle

La détection d'un symbole dans la lecture de l'image (`guess_symbol`) acceptait :

* un symbole de la liste des actifs populaires, comparé **caractère par caractère**
  (« EURUSD=X » n'est pas contenu dans « EUR/USD » → aucun résultat) ;
* un motif avec séparateur `-`, `.` ou `=` — **la barre oblique `/` n'était pas gérée**,
  alors que c'est le format affiché par toutes les plateformes de trading ;
* un sigle de 2 à 6 majuscules, mais `EUR` et `USD` figuraient dans la liste des mots à
  ignorer (unités de temps, indicateurs, mots courants).

Conséquence mesurée : `guess_symbol("EUR/USD H1 tendance baissière")` → `""`.
Donc, **même sans avoir saisi de symbole**, une capture Forex ne pouvait jamais être
rattachée à des données de marché : l'application analysait alors l'image seule.

### Cause n°3 — L'interface ne montrait nulle part ce que l'IA avait lu

L'API renvoyait déjà l'objet `observation` (actif lu, unité de temps, figures, niveaux,
résumé, incertitudes, confiance) et `request.has_image`. L'interface ignorait ces champs :
aucun moyen de savoir si la capture avait été transmise, ni si elle avait été lue.

---

## 4. Ce qui a été corrigé

| Correctif | Fichier | Effet concret |
|---|---|---|
| Détection des paires de devises et de crypto | `app/services/analysis_service.py` | « EUR/USD », « EUR-USD », « EURUSD », « EURUSD=X », « ETH/USDT », « BTCUSDT », « euro dollar » → `EURUSD=X`, `BTC-USD`… (les stablecoins USDT/USDC sont traités comme l'USD) |
| Confrontation systématique capture ↔ symbole | `app/services/analysis_service.py` | Si l'actif de l'image diffère du symbole analysé : avertissement explicite + état `coherence` renvoyé à l'interface |
| Nouvelle section de prompt « CONTRÔLE DE COHÉRENCE » | `app/prompts.py` | Consignes impératives : annoncer l'incohérence en tête de la section 1, appuyer les figures sur la capture, ne citer les niveaux calculés que pour l'actif chiffré, conseiller l'analyse du bon symbole |
| Règle permanente dans la méthode de l'analyste | `app/prompts.py` | « Quand une capture est fournie, elle fait foi pour identifier l'actif, l'unité de temps et les figures » |
| Consigne donnée au moteur vision | `app/services/analysis_service.py` | Le modèle vision sait quel actif l'application analyse et doit signaler un écart |
| Analyse automatique | `app/services/analysis_service.py` | Sans symbole saisi, l'actif lu sur la capture est désormais réellement analysé (cas Forex enfin fonctionnel) |
| **Bloc « Lecture de la capture par l'IA »** | `web/index.html`, `web/static/app.js` | Actif lu, unité de temps, tendance, fourchette de prix, figures, niveaux, indicateurs visibles, résumé, incertitudes, confiance |
| **Traçabilité de l'envoi** | interface + API (`bloc image`) | « Capture reçue : 812,4 Ko (réduite automatiquement depuis 3,1 Mo) · Lecture réussie · modèle gemini-3.6-flash » — ou l'explication de l'échec |
| **Bandeau d'incohérence + action** | `web/static/app.js` | « La capture ne correspond pas au symbole analysé » + bouton **« Analyser EURUSD=X avec cette capture »** qui relance l'analyse en un clic, sans redéposer l'image |
| **Requêtes documentaires affichées** | `web/static/app.js` | « Recherche documentaire lancée avec 3 requête(s) : … » — on voit ce qui a été cherché dans vos cours, donc le lien image → cours |

---

## 5. Comportement après correction (mesuré)

**Cas A — symbole AAPL saisi, capture EUR/USD** (le cas signalé) :

```
cohérence : symbole_demande=AAPL, capture_lue=EUR/USD, symbole_capture=EURUSD=X, incoherent=true
avertissement affiché à l'utilisateur :
   Incohérence détectée : la capture ne correspond pas au symbole analysé
   (EUR/USD sur l'image, AAPL pour les données chiffrées)…

section ajoutée au prompt :
   CONTRÔLE DE COHÉRENCE ENTRE LA CAPTURE ET LES DONNÉES CHIFFRÉES
   - Actif lu sur la capture : EUR/USD (symbole exploitable : EURUSD=X)
   - Unité de temps lue sur la capture : H1
   - Actif des données chiffrées : AAPL
   INCOHÉRENCE : la capture NE correspond PAS à l'actif des données chiffrées.
     1. commence la section 1 en annonçant cette incohérence…
     2. appuie toutes les figures et tous les niveaux de lecture sur la CAPTURE ;
     3. ne présente les niveaux calculés que comme ceux de AAPL ;
     4. conseille de relancer l'analyse avec EURUSD=X pour croiser capture et marché.
```

**Cas B — aucune saisie de symbole, capture EUR/USD :** l'application récupère
automatiquement les données d'`EURUSD=X` et croise l'analyse (impossible avant ce correctif).

---

## 6. Comment le vérifier vous-même

À l'écran, après une analyse avec capture :

1. **Badge** au-dessus du résultat : « Capture lue par l'IA » (ou « Capture non analysée
   (aucune IA vision) » si aucune clé n'est configurée) ;
2. **Bloc « Lecture de la capture par l'IA »** : ce que le modèle a lu, avec sa confiance ;
3. **Bandeau orange** s'il y a désaccord entre l'image et le symbole, avec le bouton de
   relance sur le bon symbole ;
4. **Bloc « Extraits de cours utilisés »** : la liste des requêtes lancées dans votre base.

En ligne de commande :

```bash
python -m pytest tests/test_liaison_image.py -q          # 21 tests de cette chaîne
node deploy/outils/verifier_interface.mjs http://…:8000  # 22 contrôles d'interface
```

Les tests couvrent notamment le contenu **réel** de la requête HTTP envoyée à Gemini
(présence de l'image en `inline_data`, type MIME correct, clé en en-tête `x-goog-api-key`)
et vérifient qu'aucune partie binaire n'est envoyée pour une analyse sans capture.

---

## 7. Réponse à la question posée : « où était le problème ? »

Le moteur qui lit la capture et le moteur qui traite le résultat **n'étaient pas
déconnectés** : l'image arrivait bien jusqu'au modèle, qui la lisait correctement.
Ce qui manquait, c'était **la confrontation** entre les deux sources d'information et
**la visibilité** de cette lecture :

* rien ne disait au modèle que, en cas de désaccord, la capture fait foi ;
* aucun symbole au format « EUR/USD » ne pouvait être transformé en actif négociable,
  ce qui rendait le croisement automatique impossible ;
* l'interface ne montrait pas la lecture d'image, ce qui donnait l'impression que la
  capture était purement ignorée.

Les trois points sont corrigés, testés et poussés. Si vous refaites la même manipulation
avec une capture EUR/USD, l'analyse doit maintenant commencer par signaler l'écart et vous
proposer l'analyse d'`EURUSD=X` en un clic.
