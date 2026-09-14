---
title: TradeVision IA
emoji: 📈
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Analyse et prédiction de graphiques de trading (RAG + vision)
---

# 📈 TradeVision IA

Analyse et prédiction de graphiques de trading : **RAG sur vos cours PDF**,
**LLM vision** (lecture de captures) et **moteur d'indicateurs techniques** déterministe.

> ⚠️ **Compte gratuit / PRO** : depuis 2026, créer un Space **Docker** ou **Gradio**
> nécessite un plan payant (PRO 9 $/mois, Team ou Enterprise). Pour un hébergement
> gratuit, déployez plutôt sur **Render** — voir `deploy/DEPLOIEMENT.md`.

Ce Space est déployé automatiquement depuis le dépôt GitHub (voir
`.github/workflows/sync-hf-space.yml`, installé depuis `deploy/`).

## Activer la vision et la rédaction par IA

Dans **Settings → Variables and secrets** du Space, ajoutez un secret :

| Secret | Rôle |
| --- | --- |
| `GEMINI_API_KEY` | **Recommandé, gratuit** (Google AI Studio) : lecture d'image + rédaction |
| `OPENAI_API_KEY` | Alternative OpenAI (GPT-4o / GPT-4.1) |
| `ANTHROPIC_API_KEY` | Alternative Anthropic Claude |
| `OPENROUTER_API_KEY` | Modèles variés, certains gratuits |

Sans clé, l'application fonctionne en **mode démo** : analyse technique complète
(tendance, supports/résistances, figures chartistes, indicateurs, plan de trading,
prédiction probabiliste) et recherche dans les cours indexés.

## Fonctionnalités

- 🔍 Analyse d'un symbole (actions, indices, crypto, devises, matières premières) et/ou d'une
  **capture de graphique**.
- 🧠 RAG : vos PDF/Markdown/notes sont découpés par sections, vectorisés et cités
  (`[Source n]`) dans la réponse.
- 📊 Moteur technique : tendance, structure de marché, pivots, supports/résistances,
  figures chartistes et chandeliers, RSI, MACD, ADX, ATR, Bollinger, stochastique, OBV.
- 🎯 Plan de trading chiffré : entrée, stop, objectifs, ratio risque/rendement, taille de
  position (règle du 1 %).
- 🗂️ Historique des analyses exportables en Markdown.
- 🔌 API REST documentée (`/api/docs`) — utilisable depuis n'importe quelle application.

> ⚠️ Outil pédagogique : ce n'est pas un conseil en investissement. Les marchés
> comportent un risque de perte en capital.

Dépôt source : `andrianantenainasambatrarj-glitch/projet-IA`.
