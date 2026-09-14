# Chapitre 1 — Fondamentaux de l'analyse technique

## 1.1 Définition et principes

L'analyse technique est l'étude du comportement des prix (et des volumes) dans le but
d'identifier des configurations répétitives et de bâtir des scénarios probabilistes.
Elle repose sur trois hypothèses classiques :

1. **Le prix escompte tout** : l'information disponible est déjà intégrée dans la cotation.
2. **Les prix évoluent en tendances** : une direction amorcée a statistiquement tendance
   à se prolonger jusqu'à preuve du contraire (invalidation).
3. **L'histoire se répète** : les comportements humains (peur, avidité) produisent des
   figures récurrentes, exploitables par des règles.

L'analyse technique ne prédit pas l'avenir : elle identifie des **probabilités
conditionnelles** et des **niveaux d'invalidation**. La qualité d'un trader se mesure à
la gestion du risque, pas au taux de réussite brut.

## 1.2 Les unités de temps

Une même figure peut apparaître sur plusieurs unités de temps (UT) :

- **M1 à M15** : scalping, bruit élevé, frais et spread déterminants.
- **H1, H4** : intraday/swing court, bon compromis pour débuter une analyse structurée.
- **D1 (journalier)** : référence pour le swing trading ; figures fiables, moins de bruit.
- **W1, MN** : tendance de fond, positions longues.

Règle d'analyse : commencer par l'unité de temps supérieure pour déterminer le **biais
directionnel**, puis descendre pour affiner le **point d'entrée**. Analyser un signal M15
sans savoir où se situe le prix en D1 revient à naviguer sans carte.

## 1.3 Lire un graphique pas à pas

1. **Tendance** : le prix fait-il des sommets et creux ascendants (haussière), descendants
   (baissière), ou horizontaux (range) ?
2. **Structure de marché** : noter les derniers sommets/creux majeurs. On parle de
   *higher highs / higher lows* (HH/HL) en tendance haussière, *lower highs / lower lows*
   (LH/LL) en tendance baissière.
3. **Niveaux clés** : identifier les zones où le prix a réagi plusieurs fois (supports,
   résistances). Une zone testée 3 fois est plus significative qu'un simple plus haut isolé.
4. **Volatilité** : mesurer l'amplitude moyenne des bougies (ATR) pour calibrer stop et
   objectifs. Un stop trop serré dans un marché volatil est éliminé par le bruit.
5. **Volume** : chercher la confirmation. Une cassure de résistance sans volume est un
   signal faible, souvent un faux départ.
6. **Indicateurs** : ils ne créent jamais le signal, ils le **confirment** ou le
   **filtrent**. Un indicateur seul, sans contexte graphique, produit des faux signaux.

## 1.4 Tendance et moyennes mobiles

Les moyennes mobiles lissent le prix et matérialisent la tendance :

- **SMA/EMA 20** : tendance court terme, support dynamique en tendance haussière.
- **SMA/EMA 50** : tendance intermédiaire ; sa pente qualifie la force du mouvement.
- **SMA 200** : frontière entre marché haussier et baissier de fond.

Configurations classiques :

- Prix > EMA20 > EMA50 > SMA200 : alignement haussier, acheter les replis vers EMA20.
- Prix < EMA20 < EMA50 < SMA200 : alignement baissier, vendre les rebonds.
- Moyennes imbriquées et plates : marché en range, les croisements sont peu fiables.

Le croisement de moyennes est un signal **retardé** : il confirme un mouvement déjà entamé.
Il doit être combiné à la structure et aux niveaux.

## 1.5 La structure de marché et l'invalidation

Tout scénario doit définir son **niveau d'invalidation** : le prix au-delà duquel l'idée est
fausse. En tendance haussière, l'invalidation se place sous le dernier creux significatif ;
en tendance baissière, au-dessus du dernier sommet significatif.

Sans invalidation, il n'y a pas de scénario mais une opinion. L'opinion ne se gère pas, le
scénario se gère.

## 1.6 Erreurs fréquentes du débutant

- Trader sans unité de temps de référence (signaux contradictoires en permanence).
- Chercher la certitude : l'analyse technique produit des probabilités, pas des garanties.
- Multiplier les indicateurs redondants (RSI + stochastique + Williams %R mesurent
  largement la même chose).
- Ignorer le volume lors des cassures.
- Déplacer le stop pour éviter d'être stoppé : la première cause de perte majeure.
- Prendre l'analyse d'une IA comme un conseil d'investissement : elle doit rester un
  support de décision documenté et vérifiable.

## 1.7 Résumé opérationnel

Avant toute décision : tendance (biais), niveau clé (déclencheur), invalidation (stop),
objectif (sortie), taille de position (risque ≤ 1 % du capital), et une raison écrite.
Si l'une de ces cases est vide, la meilleure action est de ne pas trader.
