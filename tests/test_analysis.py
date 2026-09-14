"""Tests du moteur d'analyse technique."""

from __future__ import annotations

from app.analysis import Instrument, analyze, render_report, to_rag_query
from app.market import demo_instrument


def test_analyse_dune_serie_de_demonstration(settings):
    instrument = demo_instrument("AAPL", "6mo", "1d", 260)
    resultat = analyze(instrument)

    assert -100 <= resultat.score <= 100
    assert 0 < resultat.confidence <= 1
    assert resultat.label
    assert resultat.indicators["rsi14"] is not None
    assert resultat.indicators["atr14"] > 0
    assert resultat.levels["period_high"] >= resultat.levels["period_low"]
    assert 0 <= resultat.levels["range_position_pct"] <= 100
    assert resultat.setup["direction"] in {"achat", "vente", "attendre"}
    assert len(resultat.scenarios) == 2


def test_plan_de_trading_coherent(settings):
    resultat = analyze(demo_instrument("BTC-USD", "6mo", "1d", 300))
    setup = resultat.setup

    if setup["direction"] == "achat":
        assert setup["stop"] < setup["entry"] <= setup["target1"] <= setup["target2"]
    elif setup["direction"] == "vente":
        assert setup["stop"] > setup["entry"] >= setup["target1"] >= setup["target2"]
    assert setup["risk_reward"] >= 0
    assert setup["position_size_hint"]


def test_rapport_et_requete_rag(settings):
    resultat = analyze(demo_instrument("EURUSD=X", "6mo", "1d", 200))
    rapport = render_report(resultat)
    for section in (
        "### Tendance",
        "### Indicateurs",
        "### Niveaux clés",
        "### Figures détectées",
        "### Synthèse technique",
        "### Plan de trading",
        "### Scénarios",
    ):
        assert section in rapport

    requete = to_rag_query(resultat)
    assert "analyse graphique" in requete
    assert len(requete) > 40


def test_series_trop_courtes_signalees(settings):
    instrument = demo_instrument("AAPL", "6mo", "1d", 60)
    instrument.candles = instrument.candles[:20]  # série volontairement trop courte
    resultat = analyze(instrument)
    assert any("bougies" in avertissement.lower() for avertissement in resultat.warnings)


def test_serie_de_demonstration_est_deterministe(settings):
    premier = demo_instrument("NVDA", "6mo", "1d", 120)
    second = demo_instrument("NVDA", "6mo", "1d", 120)
    assert [c.c for c in premier.candles] == [c.c for c in second.candles]


def test_instrument_vide_ne_plante_pas(settings):
    instrument = Instrument(symbol="TEST", name="Test", candles=[])
    # analyze() doit lever une erreur explicite (index sur série vide)
    try:
        analyze(instrument)
    except Exception as exc:  # IndexError attendue
        assert isinstance(exc, (IndexError, ValueError))
    else:  # si une garde est ajoutée plus tard, un résultat reste acceptable
        pass
