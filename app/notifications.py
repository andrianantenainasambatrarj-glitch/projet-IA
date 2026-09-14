"""Notifications : envoi des analyses vers Telegram ou un webhook (Discord, Slack, ntfy…).

Deux usages :

1. **Ponctuel** — bouton « m'envoyer cette analyse » dans l'interface, ou
   ``POST /api/notify/analysis``.
2. **Veille automatique** — un fil d'arrière-plan analyse votre *watchlist* toutes les
   ``NOTIFY_INTERVAL_MINUTES`` et envoie un résumé des marchés qui dépassent
   ``NOTIFY_MIN_SCORE`` (voir ``app/scheduler.py``).

Utile aussi en ligne de commande (cron, GitHub Actions, Render Cron…) :

    python -m app.notifications --symbols AAPL,BTC-USD --send
    python -m app.notifications --digest            # scan de la watchlist
    python -m app.notifications --test              # message de test
"""

from __future__ import annotations

import argparse
import html
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

import httpx

from .config import Settings, get_settings

TELEGRAM_API = "https://api.telegram.org"


class NotificationError(RuntimeError):
    """Erreur d'envoi (jeton absent, réseau, quota…)."""


# --------------------------------------------------------------------- #
#  Rendu des messages
# --------------------------------------------------------------------- #

def _arrow(score: float) -> str:
    if score >= 22:
        return "🟢"
    if score >= 8:
        return "🟩"
    if score <= -22:
        return "🔴"
    if score <= -8:
        return "🟥"
    return "🟨"


def _verdict_emoji(label: str) -> str:
    label = (label or "").lower()
    if "fort" in label and "baiss" in label:
        return "🔻"
    if "baiss" in label:
        return "🔻"
    if "hauss" in label:
        return "🔺"
    return "↔️"


def format_analysis_message(
    payload: dict[str, Any],
    *,
    title: str = "Analyse TradeVision IA",
    base_url: str = "",
    include_disclaimer: bool = True,
) -> str:
    """Résumé compact et lisible d'une analyse (texte brut + balises HTML Telegram)."""
    analysis = (payload or {}).get("analysis") or {}
    instrument = analysis.get("instrument") or {}
    setup = analysis.get("setup") or {}
    indicators = analysis.get("indicators") or {}
    levels = analysis.get("levels") or {}
    sources = (payload or {}).get("sources") or []
    patterns = analysis.get("patterns") or []

    symbol = instrument.get("symbol") or (payload or {}).get("request", {}).get("symbol") or "—"
    price = analysis.get("price")
    score = float(analysis.get("score") or 0.0)
    confidence = float(analysis.get("confidence") or 0.0)

    lines: list[str] = []
    lines.append(f"<b>{html.escape(title)}</b>")
    lines.append(
        f"{_arrow(score)} <b>{html.escape(str(symbol))}</b> — "
        f"{html.escape(str(analysis.get('label') or 'analyse indisponible'))} "
        f"({score:+.1f}/100, confiance {confidence:.0%})"
    )
    if price:
        lines.append(f"Prix : <code>{price:.6f}</code> {html.escape(str(instrument.get('currency') or ''))}")
    if instrument.get("timeframe"):
        lines.append(
            f"Unité de temps : {html.escape(str(instrument['timeframe']))} · "
            f"données : {html.escape(str(instrument.get('source') or '—'))}"
        )

    if analysis:
        lines.append("")
        lines.append("<b>Plan de trading</b>")
        lines.append(
            f"• Direction : <b>{html.escape(str(setup.get('direction') or '—').upper())}</b>\n"
            f"• Entrée : <code>{setup.get('entry')}</code> · Stop : <code>{setup.get('stop')}</code>\n"
            f"• Objectifs : <code>{setup.get('target1')}</code> → <code>{setup.get('target2')}</code>\n"
            f"• Ratio R/R : {setup.get('risk_reward')}"
        )
        lines.append("")
        lines.append("<b>Niveaux clés</b>")
        supports = ", ".join(f"{level['price']:.4f}" for level in (levels.get("supports") or [])[:2])
        resistances = ", ".join(f"{level['price']:.4f}" for level in (levels.get("resistances") or [])[:2])
        lines.append(f"• Supports : {supports or '—'}\n• Résistances : {resistances or '—'}")
        if indicators:
            lines.append(
                f"• RSI(14) {indicators.get('rsi14')} · ATR {indicators.get('atr_pct')} % · "
                f"MACD {'haussier' if float(indicators.get('macd_histogram') or 0) > 0 else 'baissier'}"
            )
        if patterns:
            lines.append("")
            lines.append("<b>Figures détectées</b>")
            for pattern in patterns[:3]:
                lines.append(
                    f"• {html.escape(str(pattern.get('name')))} "
                    f"({html.escape(str(pattern.get('bias')))}, "
                    f"{float(pattern.get('confidence') or 0):.0%})"
                )
        scenarios = analysis.get("scenarios") or []
        if scenarios:
            lines.append("")
            lines.append("<b>Déclencheurs</b>")
            for scenario in scenarios:
                lines.append(
                    f"• {html.escape(str(scenario.get('name')))} : "
                    f"{html.escape(str(scenario.get('trigger')))}"
                )

    if sources:
        lines.append("")
        lines.append("<b>Références du cours</b>")
        for index, source in enumerate(sources[:3], start=1):
            reference = source.get("title") or source.get("source") or "document"
            section = source.get("section") or ""
            lines.append(f"• [{index}] {html.escape(str(reference))}{(' › ' + html.escape(str(section))) if section else ''}")

    warnings = (payload or {}).get("warnings") or []
    if warnings:
        lines.append("")
        lines.append(f"⚠️ {html.escape(str(warnings[0]))}")

    if base_url:
        lines.append("")
        lines.append(f"🔗 {html.escape(base_url)}")
    if include_disclaimer:
        lines.append("")
        lines.append(
            "<i>Analyse pédagogique générée automatiquement — ne constitue pas un conseil "
            "en investissement. Risque de perte en capital.</i>"
        )
    return "\n".join(lines)


def format_digest_message(
    results: Sequence[dict[str, Any]],
    *,
    title: str = "Veille de marché — TradeVision IA",
    base_url: str = "",
) -> str:
    """Résumé d'un scan de watchlist (une ligne par marché)."""
    lines = [f"<b>{html.escape(title)}</b>", ""]
    if not results:
        lines.append("Aucun marché analysé.")
    for payload in results:
        analysis = payload.get("analysis") or {}
        instrument = analysis.get("instrument") or {}
        setup = analysis.get("setup") or {}
        score = float(analysis.get("score") or 0.0)
        symbol = instrument.get("symbol") or "—"
        lines.append(
            f"{_arrow(score)} <b>{html.escape(str(symbol))}</b> {_verdict_emoji(str(analysis.get('label')))} "
            f"{html.escape(str(analysis.get('label') or '—'))} ({score:+.1f}) — "
            f"{html.escape(str(setup.get('direction') or 'attendre'))} "
            f"entrée <code>{setup.get('entry')}</code> stop <code>{setup.get('stop')}</code>"
        )
    if base_url:
        lines.append("")
        lines.append(f"🔗 {html.escape(base_url)}")
    lines.append("")
    lines.append("<i>Veille automatique — outil pédagogique, pas un conseil en investissement.</i>")
    return "\n".join(lines)


# --------------------------------------------------------------------- #
#  Canaux d'envoi
# --------------------------------------------------------------------- #

@dataclass
class DeliveryResult:
    """Résultat d'un envoi."""

    canal: str
    statut: str  # "envoye" | "ignore" | "erreur"
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"canal": self.canal, "statut": self.statut, "detail": self.detail}


class Notifier:
    """Envoie un message sur Telegram et/ou un webhook, si configurés."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.telegram_token = (self.settings.telegram_bot_token or "").strip()
        self.telegram_chat = (self.settings.telegram_chat_id or "").strip()
        self.webhook_url = (self.settings.notify_webhook_url or "").strip()

    # ------------------------------------------------------------ état
    @property
    def telegram_ready(self) -> bool:
        return bool(self.telegram_token and self.telegram_chat)

    @property
    def webhook_ready(self) -> bool:
        return bool(self.webhook_url)

    @property
    def ready(self) -> bool:
        return self.telegram_ready or self.webhook_ready

    @property
    def label(self) -> str:
        canaux = []
        if self.telegram_ready:
            canaux.append("Telegram")
        if self.webhook_ready:
            canaux.append("Webhook")
        return " + ".join(canaux) if canaux else "aucun canal configuré"

    # ------------------------------------------------------------ envoi
    def send(self, text: str) -> list[DeliveryResult]:
        """Envoie le message sur tous les canaux configurés."""
        resultats: list[DeliveryResult] = []
        if self.telegram_ready:
            resultats.append(self._send_telegram(text))
        if self.webhook_ready:
            resultats.append(self._send_webhook(text))
        if not resultats:
            resultats.append(
                DeliveryResult(
                    canal="aucun",
                    statut="ignore",
                    detail=(
                        "Aucun canal configuré : renseignez TELEGRAM_BOT_TOKEN + "
                        "TELEGRAM_CHAT_ID ou NOTIFY_WEBHOOK_URL."
                    ),
                )
            )
        return resultats

    def _send_telegram(self, text: str) -> DeliveryResult:
        url = f"{TELEGRAM_API}/bot{self.telegram_token}/sendMessage"
        payload = {
            "chat_id": self.telegram_chat,
            "text": text[:4096],  # limite Telegram
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            with httpx.Client(timeout=self.settings.notify_timeout_s) as client:
                response = client.post(url, json=payload)
        except httpx.HTTPError as exc:
            return DeliveryResult("telegram", "erreur", f"Réseau : {exc}")
        if response.status_code >= 400:
            detail = response.text[:200]
            return DeliveryResult("telegram", "erreur", f"HTTP {response.status_code} : {detail}")
        return DeliveryResult("telegram", "envoye", "Message livré.")

    def _send_webhook(self, text: str) -> DeliveryResult:
        # Adaptation du format selon la cible la plus courante
        if "discord" in self.webhook_url:
            payload: dict[str, Any] = {"content": text[:1900]}
        elif "slack" in self.webhook_url:
            payload = {"text": text[:3000]}
        else:  # ntfy, Zapier, Make, n8n, endpoint maison…
            payload = {"text": text[:3500], "title": "TradeVision IA"}
        try:
            with httpx.Client(timeout=self.settings.notify_timeout_s) as client:
                response = client.post(self.webhook_url, json=payload)
        except httpx.HTTPError as exc:
            return DeliveryResult("webhook", "erreur", f"Réseau : {exc}")
        if response.status_code >= 400:
            return DeliveryResult(
                "webhook", "erreur", f"HTTP {response.status_code} : {response.text[:200]}"
            )
        return DeliveryResult("webhook", "envoye", "Message livré.")

    # ------------------------------------------------------------ raccourcis
    def send_test(self) -> dict[str, Any]:
        """Message de vérification de la configuration."""
        horodatage = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        texte = (
            "<b>✅ Test de notification — TradeVision IA</b>\n"
            f"Connexion vérifiée le {horodatage}.\n"
            "Vous recevrez ici vos analyses et la veille de marché.\n\n"
            "<i>Outil pédagogique : pas un conseil en investissement.</i>"
        )
        resultats = [resultat.to_dict() for resultat in self.send(texte)]
        return {
            "canaux": self.label,
            "resultats": resultats,
            "succes": any(item["statut"] == "envoye" for item in resultats),
        }

    def send_analysis(self, payload: dict[str, Any], *, base_url: str = "") -> dict[str, Any]:
        """Envoie une analyse déjà calculée."""
        titre = "Analyse TradeVision IA"
        instrument = ((payload or {}).get("analysis") or {}).get("instrument") or {}
        if instrument.get("symbol"):
            titre = f"Analyse {instrument['symbol']} — TradeVision IA"
        texte = format_analysis_message(payload, title=titre, base_url=base_url)
        resultats = [resultat.to_dict() for resultat in self.send(texte)]
        return {
            "canaux": self.label,
            "resultats": resultats,
            "succes": any(item["statut"] == "envoye" for item in resultats),
            "message": texte,
        }

    def send_digest(self, results: Sequence[dict[str, Any]], *, base_url: str = "") -> dict[str, Any]:
        """Envoie le résumé d'un scan de watchlist."""
        texte = format_digest_message(results, base_url=base_url)
        resultats = [resultat.to_dict() for resultat in self.send(texte)]
        return {
            "canaux": self.label,
            "resultats": resultats,
            "succes": any(item["statut"] == "envoye" for item in resultats),
            "message": texte,
        }


def get_notifier(settings: Optional[Settings] = None) -> Notifier:
    """Renvoie un notificateur pour la configuration courante."""
    return Notifier(settings)


# --------------------------------------------------------------------- #
#  Analyses déclenchées + veille
# --------------------------------------------------------------------- #

def analyze_and_notify(
    symbol: str,
    *,
    settings: Optional[Settings] = None,
    question: str = "",
    period: str = "",
    interval: str = "",
    send: bool = True,
    base_url: str = "",
) -> dict[str, Any]:
    """Analyse un symbole et (optionnellement) l'envoie sur les canaux configurés."""
    settings = settings or get_settings()
    from .services.analysis_service import AnalysisRequest, run_analysis

    reponse = run_analysis(
        AnalysisRequest(
            symbol=symbol,
            period=period or settings.watchlist_period,
            interval=interval or settings.watchlist_interval,
            question=question,
            save=True,
        ),
        settings=settings,
    ).to_dict()

    resultat: dict[str, Any] = {
        "symbol": symbol.upper(),
        "score": (reponse.get("analysis") or {}).get("score", 0.0),
        "label": (reponse.get("analysis") or {}).get("label", ""),
        "report_id": reponse.get("report_id", ""),
        "envoye": False,
    }
    if send:
        livraison = get_notifier(settings).send_analysis(reponse, base_url=base_url)
        resultat.update(
            {
                "envoye": livraison["succes"],
                "canaux": livraison["canaux"],
                "resultats": livraison["resultats"],
                "message": livraison["message"],
            }
        )
    resultat["analysis"] = reponse.get("analysis")
    resultat["answer"] = reponse.get("answer")
    return resultat


def run_watchlist_cycle(
    *,
    settings: Optional[Settings] = None,
    send: bool = True,
    base_url: str = "",
) -> dict[str, Any]:
    """Analyse toute la watchlist et envoie un résumé des marchés significatifs."""
    settings = settings or get_settings()
    symboles = settings.watchlist_symbols
    analyses: list[dict[str, Any]] = []
    retenus: list[dict[str, Any]] = []
    erreurs: list[dict[str, str]] = []

    from .services.analysis_service import AnalysisRequest, run_analysis

    for symbole in symboles:
        try:
            reponse = run_analysis(
                AnalysisRequest(
                    symbol=symbole,
                    period=settings.watchlist_period,
                    interval=settings.watchlist_interval,
                    question="Veille automatique : résume la situation et les niveaux clés.",
                    save=True,
                ),
                settings=settings,
            ).to_dict()
            analyses.append(reponse)
            score = float((reponse.get("analysis") or {}).get("score") or 0.0)
            if abs(score) >= float(settings.notify_min_score):
                retenus.append(reponse)
        except Exception as exc:  # pragma: no cover - dépend du réseau
            erreurs.append({"symbol": symbole, "erreur": str(exc)[:200]})

    resultat: dict[str, Any] = {
        "horodatage": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "symboles_analyses": len(analyses),
        "symboles_retenus": len(retenus),
        "seuil_score": settings.notify_min_score,
        "erreurs": erreurs,
        "envoye": False,
        "resume": [
            {
                "symbol": ((payload.get("analysis") or {}).get("instrument") or {}).get("symbol", "—"),
                "label": (payload.get("analysis") or {}).get("label", ""),
                "score": (payload.get("analysis") or {}).get("score", 0.0),
            }
            for payload in analyses
        ],
    }
    if send and retenus:
        livraison = get_notifier(settings).send_digest(retenus, base_url=base_url)
        resultat.update(
            {
                "envoye": livraison["succes"],
                "canaux": livraison["canaux"],
                "resultats": livraison["resultats"],
                "message": livraison["message"],
            }
        )
    elif send and not retenus:
        resultat["raison"] = (
            f"Aucun marché au-dessus du seuil ({settings.notify_min_score:+.0f}) : "
            "aucun message envoyé."
        )
    return resultat


def notify_status(settings: Optional[Settings] = None) -> dict[str, Any]:
    """État de la configuration des notifications (affiché dans l'interface)."""
    settings = settings or get_settings()
    notifier = get_notifier(settings)
    return {
        "canaux": notifier.label,
        "telegram_configure": notifier.telegram_ready,
        "webhook_configure": notifier.webhook_ready,
        "pret": notifier.ready,
        "veille_active": bool(settings.notify_enabled and notifier.ready),
        "intervalle_minutes": settings.notify_interval_minutes,
        "seuil_score": settings.notify_min_score,
        "watchlist": settings.watchlist_symbols,
        "aide": (
            "Pour activer les envois : créez un bot Telegram avec @BotFather, récupérez le "
            "jeton, puis renseignez TELEGRAM_BOT_TOKEN et TELEGRAM_CHAT_ID dans les variables "
            "d'environnement. Ajoutez NOTIFY_ENABLED=true pour la veille automatique."
        ),
    }


# --------------------------------------------------------------------- #
#  Ligne de commande
# --------------------------------------------------------------------- #

def _main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.notifications",
        description="Envoie les analyses TradeVision IA sur Telegram / webhook.",
    )
    parser.add_argument("--symbols", default="", help="Liste de symboles (ex : AAPL,BTC-USD)")
    parser.add_argument("--digest", action="store_true", help="Scan complet de la watchlist")
    parser.add_argument("--test", action="store_true", help="Envoie un message de test")
    parser.add_argument("--dry-run", action="store_true", help="Affiche le message sans l'envoyer")
    parser.add_argument("--base-url", default="", help="URL publique à inclure dans le message")
    args = parser.parse_args(argv)

    settings = get_settings()

    if args.dry_run:
        pdf = analyze_and_notify(
            (args.symbols.split(",")[0] if args.symbols else "AAPL"),
            settings=settings,
            send=False,
        )
        print("—" * 68)
        print(format_analysis_message(pdf, base_url=args.base_url))
        print("—" * 68)
        if not pdf.get("analysis"):
            print("⚠️ Analyse technique indisponible (symbole invalide ou données absentes).")
        return 0

    if args.test:
        resultat = get_notifier(settings).send_test()
    elif args.digest:
        resultat = run_watchlist_cycle(settings=settings, send=True, base_url=args.base_url)
    else:
        symboles = [item.strip() for item in args.symbols.split(",") if item.strip()] or ["AAPL"]
        resultat = [
            analyze_and_notify(symbole, settings=settings, send=True, base_url=args.base_url)
            for symbole in symboles
        ]

    import json

    print(json.dumps(resultat, ensure_ascii=False, indent=2, default=str))
    if isinstance(resultat, dict) and resultat.get("resultats"):
        if not any(item["statut"] == "envoye" for item in resultat["resultats"]):
            return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
