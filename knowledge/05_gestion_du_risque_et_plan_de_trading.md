# Chapitre 5 — Gestion du risque, plan de trading et psychologie

## 5.1 Le principe fondateur

Un trader gagne de l'argent non pas en ayant raison souvent, mais en perdant **peu** quand
il a tort. Avec un ratio risque/rendement de 1:2, un taux de réussite de 40 % suffit à être
rentable :

- 40 trades gagnants × 2R = +80R
- 60 trades perdants × 1R = −60R
- Résultat net : +20R, avant frais.

Corollaire : la gestion du risque n'est pas une option, c'est la stratégie.

## 5.2 La règle du 1 % (ou 2 % maximum)

Risque par trade = pourcentage fixe du capital, jamais plus de 2 %, idéalement 1 %.

**Taille de position** :

    Taille = (Capital × Risque%) ÷ (Distance entre entrée et stop)

Exemple : capital 10 000 €, risque 1 % = 100 €. Entrée 50,00 €, stop 48,00 € → distance
2,00 € → taille = 100 ÷ 2 = 50 actions. Le risque monétaire reste constant quelle que soit
la volatilité de l'actif, ce qui rend les résultats comparables entre trades.

Conséquences : plus le stop est large (marché volatil), plus la position est petite. Inutile
de réduire la qualité de l'analyse pour « faire tenir » une position.

## 5.3 Placement du stop loss

Trois méthodes usuelles, souvent combinées :

1. **Structurel** : sous le dernier creux significatif (achat) ou au-dessus du dernier sommet
   (vente). C'est le stop le plus logique : si ce niveau cède, le scénario est invalidé.
2. **Volatilité** : 1,5 à 2,5 × ATR(14) depuis l'entrée, pour se placer hors du bruit
   normal du marché.
3. **Monétaire / temporel** : perte maximale acceptée, ou sortie si le scénario ne se
   matérialise pas dans un délai défini (ex. 5 bougies).

Un stop se place **au moment de l'entrée**, jamais après. Déplacer un stop perdant pour
éviter d'être stoppé est la cause la plus fréquente de pertes catastrophiques.

## 5.4 Objectifs et sorties partielles

- Objectif 1 (≈ 1R à 1,5R) : sécuriser une partie (50 %) et déplacer le stop à l'équilibre.
- Objectif 2 (≈ 2R à 3R) : prochaine zone technique majeure.
- Trailing stop : suivre le prix avec l'ATR ou la structure pour laisser courir un mouvement
  en tendance forte.
- Ne jamais laisser un gain devenir une perte : dès que le prix a parcouru 1R, le stop passe
  au point d'entrée au minimum.

## 5.5 Le plan de trading écrit

Un plan tient en une page et répond à ces questions :

1. **Quels marchés et quelles unités de temps ?** (ex. actions US et indices, D1 + H4)
2. **Quelles configurations je joue ?** (ex. repli sur EMA20 en tendance haussière après
   avalement haussier sur support)
3. **Quel déclencheur d'entrée ?** (clôture au-dessus de X, retest de Y)
4. **Où est mon invalidation ?** (niveau précis)
5. **Quels objectifs ?** (R multiples ou niveaux techniques)
6. **Quelle taille de position ?** (formule du 1 %)
7. **Combien de trades par jour/semaine ?** (éviter le sur-trading)
8. **Quelles conditions m'interdisent de trader ?** (volatilité extrême, publication
   majeure, fatigue, série de pertes, état émotionnel)

## 5.6 Journal de trading

Pour chaque trade, noter : date, actif, unité de temps, configuration, déclencheur, entrée,
stop, objectif, taille, résultat en R, captures d'écran avant/après, respect du plan (oui/non),
émotion dominante. Après 30 à 50 trades, l'analyse du journal révèle les fuites de
performance : sur-trading, sorties précoces, tailles incohérentes, configurations non
maîtrisées.

## 5.7 Psychologie : les biais à surveiller

- **Aversion à la perte** : garder une position perdante en espérant un retour — le stop
  existe pour supprimer cette décision émotionnelle.
- **Excès de confiance** après une série gagnante : augmentation de la taille hors règles.
- **Vengeance trading** : reprendre immédiatement après une perte pour « se refaire ».
- **Biais de confirmation** : ne retenir que les informations qui valident son opinion.
  C'est exactement pourquoi une analyse contradictoire (scénario alternatif) doit toujours
  être écrite.
- **FOMO** : entrer en retard sur un mouvement déjà étendu, avec un stop trop loin, donc un
  mauvais ratio.

## 5.8 Checklist avant chaque trade

- [ ] Tendance de fond identifiée (unité de temps supérieure)
- [ ] Niveau clé et figure identifiés, avec un cours de référence
- [ ] Déclencheur précis défini
- [ ] Stop défini avant l'entrée, taille de position calculée
- [ ] Ratio risque/rendement ≥ 1:2, sinon aucun trade
- [ ] Scénario d'invalidation écrit noir sur blanc
- [ ] Aucun événement macro majeur imminent sur l'actif
- [ ] État émotionnel compatible avec l'exécution du plan

## 5.9 Avertissement

Aucune analyse — humaine ou assistée par IA — ne garantit un résultat. Les performances
passées ne préjugent pas des performances futures. Les marchés peuvent évoluer contre toute
attente rationnelle. N'investissez que du capital dont la perte ne remet pas en cause votre
situation financière, et considérez l'analyse technique comme un cadre de probabilités et de
gestion du risque, non comme une prédiction certaine.
