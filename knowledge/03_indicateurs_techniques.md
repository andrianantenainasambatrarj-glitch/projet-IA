# Chapitre 3 — Les indicateurs techniques et leur usage rigoureux

## 3.1 Rôle des indicateurs

Un indicateur est une **transformation mathématique** du prix ou du volume. Il ne contient
aucune information que le prix ne contient déjà : son intérêt est de rendre visible une
information peu lisible (momentum, volatilité, force de tendance). Un indicateur isolé
produit beaucoup de faux signaux ; il doit servir de **filtre** ou de **confirmation**.

## 3.2 RSI — Relative Strength Index (14 périodes)

Le RSI mesure la vitesse relative des hausses et des baisses, sur une échelle de 0 à 100.

- RSI > 70 : zone de surachat — attention, en tendance forte le RSI peut rester > 70
  longtemps : ce n'est **pas** un signal de vente à lui seul.
- RSI < 30 : zone de survente, même remarque en tendance baissière.
- RSI entre 40 et 60 : marché sans conviction.

Usages fiables :

1. **Divergence** : le prix fait un nouveau plus haut, le RSI non → essoufflement
   (divergence baissière) ; le prix fait un nouveau plus bas, le RSI remonte → divergence
   haussière, épuisement de la baisse.
2. **Repères de tendance** : en tendance haussière saine, les replis s'arrêtent souvent
   autour de 40-50 ; en tendance baissière, les rebonds s'épuisent vers 50-60.
3. **Filtre d'entrée** : éviter d'acheter un RSI > 75 en l'absence de structure haussière.

## 3.3 MACD (12, 26, 9)

Différence entre deux moyennes exponentielles, comparée à sa propre moyenne (ligne de
signal). L'histogramme représente l'écart entre les deux.

- Ligne MACD > ligne de signal : momentum haussier.
- Croisement sous la ligne de signal : momentum baissier.
- Le MACD est **retardé** : il confirme, il n'anticipe pas.
- Divergence MACD/prix : même logique que pour le RSI, signal d'essoufflement.

## 3.4 Bandes de Bollinger (20, 2)

Trois courbes : moyenne mobile 20 périodes, et deux bandes à ±2 écarts-types.

- **Squeeze** (bandes resserrées) : volatilité comprimée, expansion probable — souvent
  avant une cassure. À combiner avec les niveaux, pas avec des prévisions de direction.
- Prix qui « marche » le long de la bande supérieure : tendance forte, ne pas vendre
  aveuglément.
- Sortie de bande puis retour à l'intérieur : souvent un échec de mouvement (faux breakout).
- Les bandes ne sont pas des supports/résistances magiques : elles servent à mesurer la
  **volatilité relative**.

## 3.5 ATR — Average True Range (14)

L'ATR mesure l'amplitude moyenne des mouvements (en valeur absolue). C'est l'outil central
de la gestion du risque :

- **Stop loss** : placer le stop à 1,5–2,5 × ATR du point d'entrée (ou derrière la
  structure), pour éviter d'être éliminé par le bruit.
- **Objectifs** : un objectif de 2 × ATR est cohérent en intraday ; 3–4 × ATR en swing.
- **Comparaison** : ATR/prix en % permet de comparer la volatilité entre actifs
  (une crypto avec ATR 3 % se trade avec une taille de position plus petite qu'une action
  avec ATR 1 %).
- ATR élevé = position plus petite ; ATR faible = position possiblement plus grande.

## 3.6 Stochastique (14, 3) et Williams %R

Ces oscillateurs indiquent la position de la clôture dans le range récent. > 80 : haut de
canal ; < 20 : bas de canal. Ils sont redondants avec le RSI ; en utiliser plusieurs n'améliore
pas la qualité du signal, cela augmente la confiance apparente (biais dangereux).

Usage correct : repérer les retours de zone extrême **dans le sens de la tendance de fond**
(acheter un retour au-dessus de 20 en tendance haussière).

## 3.7 ADX / DMI (14)

L'ADX mesure la **force** de la tendance, sans direction :

- ADX < 20 : marché sans tendance — les stratégies de suivi de tendance échouent, préférer
  le range ou l'abstention.
- ADX 20–25 : tendance naissante.
- ADX > 25 : tendance installée ; les stratégies de continuation deviennent statistiquement
  plus fiables.
- +DI > −DI : domination acheteuse ; l'inverse en baissier.

Combinaison classique : ADX > 25 + +DI > −DI + prix au-dessus des moyennes → recherche
d'achats sur replis, pas de ventes à découvert.

## 3.8 Volume et OBV

L'OBV (On-Balance Volume) cumule le volume selon le sens de la clôture :

- OBV en hausse avec le prix : mouvement soutenu par les flux.
- OBV qui stagne pendant une hausse du prix : hausse sans participation, méfiance.
- Divergence OBV/prix : même lecture que les divergences d'oscillateurs.

Volume du jour comparé à la moyenne 20 périodes : > 1,5× = événement (publication,
décision), < 0,7× = marché en attente.

## 3.9 Combiner sans surcharger

Trois indicateurs bien choisis suffisent : un de tendance (moyennes, ADX), un de momentum
(RSI ou MACD), un de volatilité (ATR, Bollinger). La hiérarchie reste toujours la même :

1. Contexte (tendance, unité de temps supérieure) ;
2. Niveau (support/résistance, figure) ;
3. Confirmation (indicateur, volume) ;
4. Gestion du risque (stop, taille de position, ratio R/R).

Inverser cette hiérarchie — partir de l'indicateur — est la cause la plus fréquente
d'analyses incohérentes.
