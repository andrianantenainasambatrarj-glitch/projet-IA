# Rapport d'audit — erreurs de logique (front + back) et refonte de l'interface

**Projet :** TradeVision IA · **Date :** 14 septembre 2026 · **Version auditée :** commits `59f7775` puis `39f27ac`
(précédent : `848723e`) · **Branche :** `arena/01a09f32-projet-ia`

---

## 1. En résumé

| Élément | Résultat |
|---|---|
| Bugs de logique corrigés côté serveur (Python) | **15** |
| Bugs corrigés côté interface (JavaScript) | **7** |
| Tests Python | **98 réussis** (0 échec, ~7 s) dont **15 nouveaux** de non-régression |
| Contrôles d'interface automatisés (moteur JavaScript réel) | **19 / 19 réussis**, **0 erreur JavaScript** |
| Dépendances ajoutées | **aucune** (CSS et JS toujours locaux, aucun CDN) |
| Nouveaux outils livrés | `deploy/outils/verifier_interface.mjs` (test de l'interface sans navigateur) |

Deux catégories de problèmes ont été trouvées : des **calculs faux ou incohérents**
(probabilités de scénarios qui dépassaient 100 %, RSI neutre compté comme un signal
baissier, séries de démonstration qui ignoraient la période demandée) et des
**incohérences entre ce que l'application affiche et ce qu'elle fait réellement**
(veille annoncée comme démarrée alors qu'elle ne démarrait pas, badge « IA » affiché
alors que l'analyse se rabattait sur la démonstration, watchlist transformée en un
symbole unique invalide). Tout est corrigé, testé et poussé.

---

## 2. Méthode

1. **Lecture intégrale du serveur** (`app/`, 20 modules) : chaque calcul a été vérifié
   sur le papier puis confronté à un cas limite (valeur nulle, valeur extrême, séries
   vides, combinaisons de paramètres impossibles, échec réseau).
2. **Lecture intégrale de l'interface** (`web/index.html`, `web/static/style.css`,
   `web/static/app.js`) : contrat des identifiants, cohérence HTML ↔ JS ↔ CSS,
   chemins d'erreur (réseau coupé, réponse vide, fichier refusé).
3. **Exécution réelle de l'interface** : la page est chargée dans un moteur JavaScript
   (`jsdom`) avec de vraies réponses de l'API, puis les écrans sont pilotés
   automatiquement (onglets, analyse, graphique, recherche documentaire, chat,
   historique, thème). C'est ce test qui a révélé le bug nº 7 ci-dessous, invisible
   à la simple lecture.
4. **Non-régression** : chaque bug corrigé a reçu un test (`tests/test_logique.py`),
   pour qu'il ne puisse pas revenir lors d'une modification future.

---

## 3. Erreurs de logique corrigées côté serveur

### 3.1 Calculs de marché et d'analyse

| # | Symptôme observé | Cause | Correctif |
|---|---|---|---|
| 1 | Les deux scénarios pouvaient afficher **65 % + 65 % = 130 %** de probabilité (103,5 % constaté en pratique) | Les deux probabilités étaient calculées **indépendamment** à partir de l'éloignement aux niveaux de support/résistance | Les probabilités sont **dérivées du score directionnel** et **complémentaires** (total 100 %), bornées entre 15 % et 85 % : une « certitude » n'existe pas en trading |
| 2 | Un **RSI à 50** (marché sans tendance) retirait des points et s'affichait comme « survente » | La condition `else` attrapait la zone neutre 45–55 | Fonction `rsi_contribution()` dédiée : chaque zone (surachat, haussier, neutre, baissier, survente) a son propre traitement, la zone neutre ne pèse plus sur le score |
| 3 | Un **MACD exactement nul** comptait comme baissier (−8 points) | Test `histogram > 0` sinon « baissier » | Histogramme nul = **neutre**, avec une note explicite |
| 4 | Demander « 1 jour » affichait **un an de données** ; demander « 5 minutes » affichait des bougies **quotidiennes** | La série de démonstration générait toujours ~260 bougies journalières, quelle que soit la demande | `demo_candle_count()` calcule le nombre de bougies cohérent avec la période **et** l'unité de temps ; `_demo_step()` utilise le vrai espacement (5 min, 1 h, 7 j, 30 j) |
| 5 | Demander « 1 an / 5 minutes » changeait la période **sans rien dire** | Les fournisseurs limitent l'historique intraday : la valeur était corrigée en silence | La correction reste appliquée, mais elle est désormais **expliquée à l'utilisateur** dans les avertissements de l'analyse (« Historique intraday limité : la période « 1y » a été ramenée à « 1mo »… ») |
| 6 | Le mode démonstration générait au maximum 260 bougies même configuré plus haut | Valeur par défaut utilisée comme plafond strict | Plafond porté à **800 bougies** (`DEMO_CANDLES`), la période demandée reste prioritaire |

### 3.2 Veille, alertes et programmation

| # | Symptôme observé | Cause | Correctif |
|---|---|---|---|
| 7 | Cliquer sur « Démarrer la veille » affichait un démarrage… mais **la veille ne démarrait jamais** | `start_scheduler()` sortait immédiatement lorsque les notifications étaient désactivées (`NOTIFY_ENABLED=false`), et la réponse ne renvoyait pas l'état réel | La route active explicitement la fonctionnalité avant de démarrer et renvoie l'**état réel** (`demarrage`, `veille`) ; l'interface n'affiche plus un état faux |
| 8 | La veille ne suivait **aucun symbole** après passage par le formulaire | La liste était jointe par des espaces : `AAPL BTC-USD ^FCHI` était interprété comme **un seul symbole** invalide | Séparateur virgule (`AAPL, BTC-USD, ^FCHI`), la valeur du formulaire fait désormais un aller-retour sans perte |
| 9 | Modifier l'intervalle de veille n'avait **aucun effet** | Le fil d'exécution tournait déjà : `start_scheduler()` renvoyait `True` sans appliquer la nouvelle cadence | Si l'intervalle a changé, l'ancien fil est arrêté proprement puis relancé avec la nouvelle cadence |
| 10 | La page d'accueil pouvait mettre plusieurs secondes à s'afficher | `market_status()` testait réellement les fournisseurs (appels réseau) à chaque appel | Le diagnostic répond **immédiatement** depuis le cache et relance le test **en arrière-plan** ; `/api/health?refresh=true` force un test bloquant. Fonction `reset_status_cache()` pour les tests |

### 3.3 Fournisseur IA, format des fichiers, performances

| # | Symptôme observé | Cause | Correctif |
|---|---|---|---|
| 11 | Le diagnostic déclenchait la découverte des modèles Gemini (**appel réseau bloquant**) à chaque affichage de page | `provider_status()` appelait `list_models()` | Le diagnostic lit uniquement le **cache** (`cached_models()`, aucun réseau) |
| 12 | Un échec réseau de la découverte de modèles était **réessayé à chaque requête** | Seuls les succès étaient mis en cache | Les échecs sont mémorisés **2 minutes** (les succès 30 minutes) : plus d'appels en rafale |
| 13 | Le bandeau annonçait le mode démonstration alors qu'une clé IA était configurée (et inversement : mode IA annoncé, analyse en repli local) | `mode_demo` ne regardait que la présence du client, pas la disponibilité réelle de la clé, et l'interface ne lisait pas `fallback_reason` | Le mode démo est calculé **honnêtement** (clé absente ou client indisponible) et l'interface affiche « Repli local (appel IA en échec) » quand c'est le cas |
| 14 | Déposer un fichier qui n'est pas une image (PDF, TXT) provoquait une **erreur interne 500** | L'erreur de format remontait sans être convertie en réponse métier | Réponse **400** avec un message clair (« Format d'image non reconnu… ») ; un fichier **vide** renvoie également 400 au lieu de 413 |
| 15 | Le nom du modèle IA affiché restait « auto » alors que le modèle était résolu | Le libellé était lu avant la résolution | `_model_label()` : le **modèle réellement utilisé** est affiché, y compris après bascule automatique de modèle |
| 16 | Ralentissement inutile de la recherche documentaire | `set(doc_ids)` était recalculé à **chaque itération** de la boucle BM25 | Ensemble construit **une seule fois** avant la boucle |
| 17 | Les notes de la source (période ajustée, repli démo hors-ligne) n'apparaissaient pas dans l'analyse | Les notes de l'instrument n'étaient pas recopiées dans les avertissements | Elles sont désormais visibles dans le bloc « Avertissements » de l'analyse |

> Deux points signalés par la lecture n'étaient **pas** des bugs et sont laissés tels quels :
> la protection par jeton `?token=` (choix assumé de « protection légère ») et le plafond
> intraday imposé par les fournisseurs gratuits (contrainte externe, désormais expliquée).

---

## 4. Erreurs corrigées côté interface (JavaScript)

| # | Symptôme | Cause | Correctif |
|---|---|---|---|
| 1 | Après avoir déposé un cours puis lancé l'analyse, le bouton restait bloqué sur « Traitement… » | La seconde étape écrasait le libellé d'origine mémorisé du bouton | `setBusy()` conserve le libellé d'origine sur toute la chaîne d'opérations et le restaure toujours |
| 2 | Info-bulle du graphique affichant des valeurs fausses, ou exception au survol | Géométrie (`canvas._geom`) conservée après un changement de données vides | Géométrie remise à `null` et garde-fou dans le gestionnaire de survol |
| 3 | Résultats de la recherche documentaire **invisibles** | Ils étaient écrits dans un bloc caché de l'onglet Analyse | Ils s'affichent dans l'onglet **Connaissances**, à côté du formulaire de recherche |
| 4 | Le badge du haut annonçait une IA active alors que l'analyse se rabattait sur la démonstration | Le badge ne tenait pas compte du motif de repli renvoyé par l'API | Le badge reflète l'état réel : IA active, repli local, ou données de démonstration |
| 5 | Graphique saccadé au déplacement de la souris | Redessin complet (chandeliers + indicateurs) à chaque `mousemove` | Redessin **au rythme de l'affichage** (`requestAnimationFrame`) et géométrie mise en cache |
| 6 | Le bouton « Tester les sources » perdait son icône et restait en état occupé | Il utilisait le mécanisme générique des boutons d'action, qui remplace tout le contenu | État dédié `disabled` + classe `.loading` : l'icône tourne, le bouton reste identique, puis redevient actif |
| 7 | **Découvert par le test automatisé** : une exception JavaScript était levée à **chaque clic d'onglet** lorsque l'application est affichée dans un cadre (iframe de prévisualisation, fenêtre de démonstration) | `history.replaceState()` est refusé par le navigateur dans ce contexte et l'exception interrompait la suite du traitement | Appel encadré : l'URL est mise à jour si c'est possible, la navigation fonctionne dans tous les cas |

---

## 5. Refonte de l'interface

Objectif : une interface **lisible, professionnelle et honnête** (elle dit toujours ce
qu'elle fait vraiment), sans aucune dépendance externe.

### Structure et navigation
- **7 onglets** au lieu de 3 : *Analyse · Graphique · Chat · Connaissances · Historique · Alertes & veille · Aide*.
- Onglets accessibles au clavier (flèches ← →), `role="tablist"` complet, onglet actif
  conservé dans l'URL (`#graphique`) : un lien partagé ouvre directement le bon écran.
- Bandeau de diagnostic permanent : état du fournisseur IA, des sources de marché,
  de la base de cours, de l'historique et de la veille, avec bouton de rafraîchissement.

### Comprendre le résultat
- **Nouveau bloc « Facteurs du score »** : chaque point du score est expliqué en clair
  (« RSI 56 orienté à la hausse », « MACD baissier », « Prix à 92 % du haut de range :
  peu de marge avant résistance »), suivi de la note du plan de trading. Ces explications
  étaient calculées par le serveur mais n'étaient affichées nulle part : l'utilisateur
  voyait un score sans pouvoir le vérifier.

### Lisibilité et honnêteté de l'information
- **Thème sombre et thème clair** (bascule dans l'en-tête, préférence mémorisée, appliqué
  avant le premier affichage pour éviter tout clignotement).
- Bandeaux colorés distincts : mode démonstration, aucune source de marché réelle,
  erreur de fournisseur IA, avec la raison exacte renvoyée par l'API.
- Carte de verdict réorganisée : pastille de tendance, score, confiance, puis blocs
  *Niveaux clés · Figures détectées · Scénarios et probabilités · Avertissements · Sources des cours*.
- États vides explicites (« Aucune analyse enregistrée pour l'instant ») et messages
  d'erreur en français, jamais un écran figé.

### Graphique
- Chandeliers, moyennes mobiles, volume, croix de visée avec info-bulle (date, OHLC,
  variation), contrôles *période / unité de temps / nombre de bougies*, statut textuel
  (« 180 bougies chargées ») et redimensionnement automatique.

### Formulaires et gestes
- **Glisser-déposer** pour la capture de graphique et pour les cours (PDF, TXT, MD),
  avec aperçu du fichier et bouton d'effacement.
- Chat avec historique de conversation, sources utilisées et exemples de questions cliquables.
- Connaissances : statistiques de la base, tableau des documents, recherche avec extraits
  sourcés, indexation et suppression.
- Alertes : canaux disponibles, test d'envoi réel, watchlist, seuil de score, intervalle,
  avec l'état réel de la veille (prochain passage).
- Notifications discrètes (toasts) pour confirmer chaque action, sans bloquer l'écran.

### Qualité technique et accessibilité
- Focus visible sur tous les éléments interactifs, `aria-label` / `aria-live` /
  `aria-selected` renseignés, contrastes vérifiés dans les deux thèmes.
- Respect de `prefers-reduced-motion` (animations désactivées si le système le demande),
  **feuille de style d'impression** (l'analyse s'imprime proprement).
- Adaptatif : 940 px, 760 px, 700 px, 640 px ; polices système (aucun téléchargement).
- Repli CSS `@supports` pour les navigateurs sans `color-mix()`.

---

## 6. Comment ces corrections sont vérifiées

### Tests serveur
```bash
python -m pytest tests -q
# 98 passed, 2 warnings in 6.62s
```
`tests/test_logique.py` (nouveau, 15 tests) verrouille précisément les bugs corrigés :
probabilités de scénarios qui totalisent 100 %, RSI neutre, MACD nul, nombre de bougies
de démonstration par période et par unité de temps, note d'ajustement intraday, fichier
non-image refusé en 400, démarrage réel de la veille, watchlist multi-symboles,
diagnostic de marché sans appel réseau bloquant, statut IA honnête.

### Test de l'interface dans un vrai moteur JavaScript
```bash
npm install jsdom                                     # une seule fois
node deploy/outils/verifier_interface.mjs http://127.0.0.1:8000
# 19/19 contrôles réussis — Aucune erreur JavaScript détectée.
```
Le script charge la page servie par l'application, exécute son JavaScript et pilote
les écrans réels (analyse, graphique, recherche, chat, historique, thème). Il est
utilisable tel quel dans une intégration continue (code de sortie non nul en cas de problème).

### Vérifications statiques complémentaires
- `node --check web/static/app.js` : syntaxe valide.
- Tous les identifiants utilisés par le JavaScript existent dans la page (et inversement).
- Aucune classe CSS utilisée sans style, aucune variable CSS utilisée sans définition,
  aucune icône appelée sans être définie, aucune `<div>` orpheline.

---

## 7. Limites connues (signalées en toute transparence)

Ces points n'empêchent pas l'utilisation et sont documentés pour la suite :

- **Aucun navigateur graphique n'est disponible dans mon environnement** : le
  rendu visuel final (couleurs, alignements) est vérifié par le test automatique
  ci-dessus et **à l'œil dans l'aperçu en direct** — jsdom ne calcule pas le CSS.
- `SCHEDULER_STATUS["prochain_passage"]` est donné en heure locale formatée (et non
  en heure ISO) : lisible pour l'utilisateur, moins pratique pour un programme.
- Le fournisseur Binance ne traite pas encore une réponse JSON de forme inattendue
  (elle remonterait en erreur générique au lieu d'un message dédié).
- `validate_image` reconnaît les images par leur en-tête de fichier : un fichier
  commençant par les bons octets mais corrompu passerait ce contrôle (le fournisseur
  d'IA le refuserait ensuite, avec un message clair).
- Si tous les extraits de cours ont un score identique, la normalisation les place
  tous à 1,0 : l'ordre reste celui du BM25, mais l'affichage des scores est trompeur.
- La protection par jeton d'accès reste « légère » : le jeton est présent dans la page
  et peut apparaître dans une URL exportée (`?token=`) — suffisant pour une
  démonstration, pas pour des données sensibles.

---

## 8. Livraison

- **Commits poussés sur la branche `arena/01a09f32-projet-ia` :**
  - `59f7775` — *« fix(qualite): audit des erreurs de logique/script + refonte de
    l'interface web »* (17 fichiers, +2 534 / −934 lignes) ;
  - `39f27ac` — *« feat(interface): bloc « Facteurs du score » expliquant chaque
    indicateur »*.
- **Fichiers principaux modifiés :** `app/market.py`, `app/analysis.py`, `app/llm.py`,
  `app/scheduler.py`, `app/api/routes.py`, `app/services/analysis_service.py`,
  `app/retriever.py`, `app/main.py`, `app/config.py`, `web/index.html`,
  `web/static/style.css`, `web/static/app.js`.
- **Nouveaux fichiers :** `tests/test_logique.py`, `deploy/outils/verifier_interface.mjs`.
