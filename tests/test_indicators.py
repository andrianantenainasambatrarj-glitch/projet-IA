"""Tests des indicateurs techniques (aucune dépendance externe)."""

from __future__ import annotations

import math

from app import indicators as ind


def test_sma_valeurs_connues():
    valeurs = [1, 2, 3, 4, 5]
    resultat = ind.sma(valeurs, 3)
    assert resultat[0] is None and resultat[1] is None
    assert resultat[2] == 2.0
    assert resultat[3] == 3.0
    assert resultat[4] == 4.0


def test_sma_tolere_les_none():
    resultat = ind.sma([1.0, None, 3.0, 4.0], 2)
    assert resultat[0] is None
    assert resultat[1] is None
    assert resultat[2] is None  # fenêtre contenant un None
    assert resultat[3] == 3.5


def test_ema_converge_vers_la_valeur():
    valeurs = [10.0] * 60
    resultat = ind.ema(valeurs, 20)
    assert resultat[-1] == 10.0


def test_rsi_borne_et_extremes():
    haussier = [float(n) for n in range(1, 60)]
    baissier = [float(n) for n in range(60, 1, -1)]
    rsi_haut = ind.last_valid(ind.rsi(haussier, 14))
    rsi_bas = ind.last_valid(ind.rsi(baissier, 14))
    assert rsi_haut is not None and rsi_haut > 95
    assert rsi_bas is not None and rsi_bas < 5


def test_macd_et_histogramme_alignes():
    valeurs = [100 + math.sin(n / 5) * 5 + n * 0.3 for n in range(120)]
    donnees = ind.macd(valeurs)
    assert len(donnees["macd"]) == len(valeurs)
    dernier_hist = ind.last_valid(donnees["histogram"])
    dernier_macd = ind.last_valid(donnees["macd"])
    dernier_signal = ind.last_valid(donnees["signal"])
    assert dernier_hist is not None
    assert abs(dernier_hist - (dernier_macd - dernier_signal)) < 1e-9


def test_atr_positif_et_bollinger_ordonne():
    hauts = [10 + n * 0.5 for n in range(60)]
    bas = [9 + n * 0.5 for n in range(60)]
    clotures = [9.5 + n * 0.5 for n in range(60)]
    atr = ind.last_valid(ind.atr(hauts, bas, clotures, 14))
    assert atr is not None and atr > 0

    bandes = ind.bollinger(clotures, 20, 2.0)
    haut = ind.last_valid(bandes["upper"])
    milieu = ind.last_valid(bandes["middle"])
    bas_bande = ind.last_valid(bandes["lower"])
    assert bas_bande < milieu < haut


def test_adx_et_stochastique_dans_les_bornes():
    hauts, bas, clotures = [], [], []
    prix = 100.0
    for n in range(90):
        prix += 0.6 if n % 7 < 5 else -0.4
        hauts.append(prix + 1.2)
        bas.append(prix - 1.1)
        clotures.append(prix)

    adx = ind.adx(hauts, bas, clotures)
    valeur_adx = ind.last_valid(adx["adx"])
    assert valeur_adx is not None and 0 <= valeur_adx <= 100

    stoch = ind.stochastic(hauts, bas, clotures)
    valeur_k = ind.last_valid(stoch["k"])
    assert valeur_k is not None and 0 <= valeur_k <= 100


def test_obv_suit_le_sens_du_prix():
    clotures = [10, 11, 12, 11, 13]
    volumes = [100, 100, 100, 100, 100]
    obv = ind.obv(clotures, volumes)
    assert obv == [0.0, 100.0, 200.0, 100.0, 200.0]


def test_slope_detecte_la_direction():
    croissante = [float(n) for n in range(50)]
    decroissante = [float(50 - n) for n in range(50)]
    assert ind.slope(croissante, 20) > 0
    assert ind.slope(decroissante, 20) < 0
