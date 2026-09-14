/* =====================================================================
   TradeVision IA — interface web (JavaScript natif, aucune dépendance)
   Correctifs notables de cette version :
     - le libellé des boutons ne reste plus bloqué sur « Traitement… »
       lorsqu'une action enchaîne deux étapes (analyse + indexation) ;
     - le curseur du graphique ne plante plus quand les données sont
       indisponibles (géométrie remise à zéro) ;
     - les résultats de la recherche documentaire s'affichent dans
       l'onglet « Connaissances » (et non dans un bloc masqué) ;
     - les badges d'état ne se contredisent plus (veille / sources).
   ===================================================================== */
"use strict";

const APP = window.APP_CONFIG || {};
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.prototype.slice.call(root.querySelectorAll(sel));
const ONGLETS = ["tab-analyse", "tab-graphique", "tab-chat", "tab-connaissances", "tab-historique", "tab-notifications", "tab-aide"];

/* ------------------------------------------------------------------ utils */

function esc(value) {
  return String(value === null || value === undefined ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function icon(name, cls) {
  return '<svg class="ico ' + (cls || "") + '" aria-hidden="true"><use href="#i-' + name + '"/></svg>';
}

function fmt(value, digits = 4) {
  const num = Number(value);
  if (!isFinite(num)) return "—";
  const abs = Math.abs(num);
  const decimals = abs >= 1000 ? 2 : abs >= 10 ? 3 : digits;
  return num.toLocaleString("fr-FR", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

function pct(value, digits = 1) {
  const num = Number(value);
  return isFinite(num) ? num.toFixed(digits) + " %" : "—";
}

function heure(iso) {
  const texte = String(iso || "");
  return texte.length >= 16 ? texte.slice(11, 16) : texte;
}

function dateHeure(iso) {
  return String(iso || "").replace("T", " ").slice(0, 16);
}

function toast(message, kind = "info", delay = 5200) {
  const box = $("#toasts");
  if (!box) return;
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.innerHTML =
    '<div class="toast-body">' + message + '</div>' +
    '<button type="button" aria-label="Fermer la notification">' + icon("close", "ico-sm") + "</button>";
  const fermer = () => el.remove();
  el.querySelector("button").addEventListener("click", fermer);
  box.appendChild(el);
  setTimeout(fermer, delay);
}

/** Ajoute le jeton d'accès (si configuré) aux en-têtes d'une requête. */
function withToken(options = {}) {
  const opts = Object.assign({}, options);
  if (APP.apiToken) {
    opts.headers = Object.assign({}, opts.headers || {}, { "X-API-Token": APP.apiToken });
  }
  return opts;
}

/** URL d'API (avec ?token= pour les liens de téléchargement, qui n'ont pas d'en-tête). */
function apiUrl(path) {
  if (!APP.apiToken) return path;
  return path + (path.indexOf("?") === -1 ? "?" : "&") + "token=" + encodeURIComponent(APP.apiToken);
}

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, withToken(options));
  } catch (err) {
    throw new Error("Serveur injoignable (vérifiez votre connexion).");
  }
  const text = await response.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (err) { data = { raw: text }; }

  if (!response.ok) {
    const detail = data && data.detail;
    const message = typeof detail === "string" ? detail
      : Array.isArray(detail) ? detail.map((d) => d.msg || JSON.stringify(d)).join(" ; ")
      : (data && data.erreur) || ("Erreur HTTP " + response.status);
    const suffixe = response.status === 401
      ? " (jeton d'accès manquant ou invalide : vérifiez API_ACCESS_TOKEN)"
      : response.status === 413 ? " (fichier trop volumineux)" : "";
    throw new Error(message + suffixe);
  }
  return data;
}

/** Active/désactive l'état « en cours » d'un bouton sans écraser son libellé d'origine. */
function setBusy(button, busy, label) {
  if (!button) return;
  if (busy) {
    if (!button.dataset.original) button.dataset.original = button.innerHTML;
    if (button.dataset.busy === "1") {
      // Déjà occupé : on met seulement à jour le libellé (l'original reste intact).
      const zone = button.querySelector(".busy-label");
      if (zone) zone.textContent = label || "Traitement…";
    } else {
      button.dataset.busy = "1";
      button.disabled = true;
      button.innerHTML = '<span class="spinner" aria-hidden="true"></span><span class="busy-label">' +
        esc(label || "Traitement…") + "</span>";
    }
  } else {
    button.disabled = false;
    delete button.dataset.busy;
    if (button.dataset.original) {
      button.innerHTML = button.dataset.original;
      delete button.dataset.original;
    }
  }
}

/* ------------------------------------------------------- markdown minimal */

function mdInline(text) {
  return esc(text)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
}

function mdToHtml(markdown) {
  const lines = String(markdown || "").replace(/\r\n/g, "\n").split("\n");
  const out = [];
  let inCode = false, inList = false, inQuote = false, inTable = false, paragraph = [];

  const flushParagraph = () => {
    if (paragraph.length) { out.push("<p>" + mdInline(paragraph.join(" ")) + "</p>"); paragraph = []; }
  };
  const closeList = () => { if (inList) { out.push("</ul>"); inList = false; } };
  const closeQuote = () => { if (inQuote) { out.push("</blockquote>"); inQuote = false; } };
  const closeTable = () => { if (inTable) { out.push("</tbody></table>"); inTable = false; } };
  const closeAll = () => { flushParagraph(); closeList(); closeQuote(); closeTable(); };

  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i];
    const line = raw.trimEnd();

    if (/^```/.test(line)) {
      closeAll();
      if (!inCode) { out.push("<pre><code>"); inCode = true; }
      else { out.push("</code></pre>"); inCode = false; }
      continue;
    }
    if (inCode) { out.push(esc(raw)); continue; }
    if (!line.trim()) { closeAll(); continue; }

    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      closeAll();
      const level = Math.min(6, heading[1].length);
      out.push("<h" + level + ">" + mdInline(heading[2]) + "</h" + level + ">");
      continue;
    }

    if (/^(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) { closeAll(); out.push("<hr>"); continue; }

    const quote = line.match(/^>\s?(.*)$/);
    if (quote) {
      flushParagraph(); closeList(); closeTable();
      if (!inQuote) { out.push("<blockquote>"); inQuote = true; }
      out.push("<p>" + mdInline(quote[1]) + "</p>");
      continue;
    } else { closeQuote(); }

    if (/^\|.*\|$/.test(line)) {
      const cells = line.split("|").slice(1, -1).map((c) => c.trim());
      const next = (lines[i + 1] || "");
      const isSeparator = /^\|[\s:|-]+\|$/.test(next.trim());
      if (!inTable) {
        flushParagraph();
        out.push("<table><thead><tr>" + cells.map((c) => "<th>" + mdInline(c) + "</th>").join("") + "</tr></thead><tbody>");
        inTable = true;
        if (isSeparator) i++;
        continue;
      }
      if (isSeparator) continue;
      out.push("<tr>" + cells.map((c) => "<td>" + mdInline(c) + "</td>").join("") + "</tr>");
      continue;
    } else { closeTable(); }

    const bullet = line.match(/^\s*[-*•]\s+(.*)$/);
    const ordered = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (bullet || ordered) {
      flushParagraph();
      if (!inList) { out.push("<ul>"); inList = true; }
      out.push("<li>" + mdInline((bullet || ordered)[1]) + "</li>");
      continue;
    } else { closeList(); }

    paragraph.push(line.trim());
  }
  if (inCode) out.push("</code></pre>");
  closeAll();
  return out.join("\n");
}

/* -------------------------------------------------------------------- état */

const state = {
  symbol: APP.defaultSymbol || "AAPL",
  period: APP.defaultPeriod || "6mo",
  interval: APP.defaultInterval || "1d",
  limit: 180,
  chart: null,
  lastResult: null,
  lastReportId: "",
  imageDataUrl: "",
  docFiles: [],
  docUploaded: false,
};

function sauverPreferences() {
  try {
    localStorage.setItem("tv-prefs", JSON.stringify({
      symbol: state.symbol, period: state.period, interval: state.interval, limit: state.limit,
    }));
  } catch (err) { /* mode privé : sans conséquence */ }
}

function chargerPreferences() {
  try {
    const brut = localStorage.getItem("tv-prefs");
    if (!brut) return;
    const prefs = JSON.parse(brut);
    if (prefs.symbol) state.symbol = prefs.symbol;
    if (prefs.period) state.period = prefs.period;
    if (prefs.interval) state.interval = prefs.interval;
    if (prefs.limit) state.limit = prefs.limit;
  } catch (err) { /* préférences illisibles : on garde les valeurs par défaut */ }
}

/* ------------------------------------------------------------------ thème */

function initTheme() {
  const bouton = $("#btn-theme");
  if (!bouton) return;
  const appliquer = (theme) => {
    document.documentElement.dataset.theme = theme;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", theme === "light" ? "#f4f6fb" : "#080b12");
    bouton.setAttribute("aria-label", theme === "light" ? "Passer au thème sombre" : "Passer au thème clair");
  };
  appliquer(document.documentElement.dataset.theme || "dark");
  bouton.addEventListener("click", () => {
    const theme = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    appliquer(theme);
    try { localStorage.setItem("tv-theme", theme); } catch (err) { /* ignoré */ }
    drawChart();
  });
}

/* --------------------------------------------------------------- onglets */

function afficherOnglet(id, { focus = false } = {}) {
  if (ONGLETS.indexOf(id) === -1) id = "tab-analyse";
  $$("nav.tabs button").forEach((b) => {
    const actif = b.dataset.tab === id;
    b.classList.toggle("active", actif);
    b.setAttribute("aria-selected", actif ? "true" : "false");
    b.tabIndex = actif ? 0 : -1;
    if (actif && focus) b.focus();
  });
  $$(".tab-panel").forEach((panel) => {
    const actif = panel.id === id;
    panel.classList.toggle("active", actif);
    panel.hidden = !actif;
  });
  // L'URL reflète l'onglet ouvert (partage de lien, retour arrière). L'appel est
  // protégé : dans une iframe ou une page ouverte en local, replaceState peut être
  // refusé par le navigateur — la navigation interne doit continuer de fonctionner.
  try {
    if (location.hash.slice(1) !== id.replace("tab-", "")) {
      history.replaceState(null, "", "#" + id.replace("tab-", ""));
    }
  } catch (err) { /* URL non modifiable : sans conséquence sur la navigation */ }
  if (id === "tab-graphique") { drawChart(); if (!state.chart) loadChart(); }
  if (id === "tab-connaissances") loadDocuments();
  if (id === "tab-historique") loadReports();
  if (id === "tab-notifications") loadNotifyStatus();
}

function initTabs() {
  const boutons = $$("nav.tabs button");
  boutons.forEach((bouton, index) => {
    bouton.addEventListener("click", () => afficherOnglet(bouton.dataset.tab));
    bouton.addEventListener("keydown", (event) => {
      const touches = { ArrowRight: 1, ArrowLeft: -1, Home: "home", End: "end" };
      if (!(event.key in touches)) return;
      event.preventDefault();
      let cible = touches[event.key] === "home" ? 0
        : touches[event.key] === "end" ? boutons.length - 1
        : (index + touches[event.key] + boutons.length) % boutons.length;
      afficherOnglet(boutons[cible].dataset.tab, { focus: true });
    });
  });
  window.addEventListener("hashchange", () => afficherOnglet("tab-" + location.hash.slice(1)));
}

/* -------------------------------------------------------------- analyse */

function verdictClass(score) {
  const valeur = Number(score);
  if (valeur >= 8) return "bull";
  if (valeur <= -8) return "bear";
  return "neutral";
}

function renderVerdict(analysis) {
  const box = $("#verdict");
  if (!analysis) { box.innerHTML = ""; box.className = "verdict"; return; }
  const cls = verdictClass(analysis.score);
  const setup = analysis.setup || {};
  const indicateurs = analysis.indicators || {};
  box.className = "verdict " + cls;
  box.innerHTML =
    '<div class="main">' +
      '<p class="dir">' + esc(analysis.label || "—") + "</p>" +
      "<p class=\"sub\">Score directionnel <b>" + Number(analysis.score).toFixed(1) + "/100</b>" +
      " · confiance <b>" + pct((analysis.confidence || 0) * 100, 0) + "</b>" +
      " · tendance : " + esc((analysis.trend && analysis.trend.label) || "—") + "</p>" +
      '<div class="meter" role="img" aria-label="Score ' + Number(analysis.score).toFixed(1) + ' sur 100">' +
        '<i style="width:' + Math.min(100, Math.abs(Number(analysis.score))) + '%"></i></div>' +
      '<div class="plan"><p class="sub">Plan proposé : <b>' + esc(setup.direction || "—") + "</b>" +
      " · entrée <span class=\"mono\">" + fmt(setup.entry) + "</span>" +
      " · stop <span class=\"mono\">" + fmt(setup.stop) + "</span>" +
      " · objectifs <span class=\"mono\">" + fmt(setup.target1) + "</span> / <span class=\"mono\">" + fmt(setup.target2) + "</span>" +
      " · R/R <b>" + (setup.risk_reward !== undefined ? setup.risk_reward : "—") + "</b></p></div>" +
    "</div>" +
    '<div class="kpis">' +
      '<div class="kpi"><span class="label">Dernier prix</span><span class="value">' + fmt(analysis.price) + "</span></div>" +
      '<div class="kpi"><span class="label">RSI (14)</span><span class="value">' + fmt(indicateurs.rsi14, 1) + "</span></div>" +
      '<div class="kpi"><span class="label">Volatilité ATR</span><span class="value">' + pct(indicateurs.atr_pct, 2) + "</span></div>" +
      '<div class="kpi"><span class="label">Position range</span><span class="value">' +
        pct(analysis.levels && analysis.levels.range_position_pct, 0) + "</span></div>" +
    "</div>";
}

function renderLevels(analysis) {
  const box = $("#levels");
  if (!analysis || !analysis.levels) { box.innerHTML = '<div class="empty">Aucun niveau calculé.</div>'; return; }
  const niveaux = analysis.levels;
  const lignes = [];
  (niveaux.supports || []).forEach((lvl) => lignes.push(
    "<tr><td><span class=\"pill bull\">Support</span></td><td class=\"mono\">" + fmt(lvl.price) +
    "</td><td>" + lvl.touches + " touche(s)</td><td class=\"mono\">−" + pct(lvl.distance_pct, 2) + "</td></tr>"));
  (niveaux.resistances || []).forEach((lvl) => lignes.push(
    "<tr><td><span class=\"pill bear\">Résistance</span></td><td class=\"mono\">" + fmt(lvl.price) +
    "</td><td>" + lvl.touches + " touche(s)</td><td class=\"mono\">+" + pct(lvl.distance_pct, 2) + "</td></tr>"));
  box.innerHTML = lignes.length
    ? '<div class="table-wrap"><table class="data"><thead><tr><th>Type</th><th>Prix</th><th>Solidité</th>' +
      "<th>Distance</th></tr></thead><tbody>" + lignes.join("") + "</tbody></table></div>" +
      '<p class="small mt">Position dans le range : <b>' + pct(niveaux.range_position_pct, 0) + "</b> (bas " +
      fmt(niveaux.period_low) + " → haut " + fmt(niveaux.period_high) + ")</p>"
    : '<div class="empty">Aucun niveau détecté sur la période.</div>';
}

function renderPatterns(analysis) {
  const box = $("#patterns");
  if (!analysis || !analysis.patterns || !analysis.patterns.length) {
    box.innerHTML = '<div class="empty">' + icon("chart") +
      "<p>Aucune figure chartiste nette détectée sur la période.</p></div>";
    return;
  }
  box.innerHTML = analysis.patterns.map((p) => {
    const cls = p.bias === "haussier" ? "bull" : p.bias === "baissier" ? "bear" : "neutral";
    return '<div class="source"><div class="head"><b>' + esc(p.name) + "</b><span>" +
      '<span class="pill ' + cls + '">' + esc(p.bias) + "</span> confiance " + pct((p.confidence || 0) * 100, 0) +
      "</span></div><pre>" + esc(p.description) +
      (p.target !== undefined ? "\nObjectif : " + fmt(p.target) : "") +
      (p.invalidation !== undefined ? " · Invalidation : " + fmt(p.invalidation) : "") + "</pre></div>";
  }).join("");
}

function renderSources(sources, cible, requetes) {
  const box = cible || $("#sources");
  if (!box) return;
  // Quelles recherches ont réellement été lancées dans vos cours : rend visible le
  // lien entre l'image envoyée, l'analyse chiffrée et la base de connaissances.
  const trace = (requetes && requetes.length)
    ? '<p class="small muted">Recherche documentaire lancée avec ' + requetes.length +
      " requête(s) : " + esc(requetes.map((q) => "« " + q.slice(0, 70) + " »").join(", ")) + "</p>"
    : "";
  if (!sources || !sources.length) {
    box.innerHTML = '<div class="empty">' + icon("book") +
      "<p>Aucun extrait de cours récupéré. Importez vos documents dans l'onglet « Connaissances ».</p></div>" + trace;
    return;
  }
  box.innerHTML = sources.map((s, i) => {
    const score = Math.max(0, Math.min(1, Number(s.score) || 0));
    return '<div class="source"><div class="head"><b>[Source ' + (i + 1) + "] " + esc(s.title || s.source) +
      (s.section ? " › " + esc(s.section) : "") + (s.page ? " · p." + s.page : "") +
      '</b><span class="mono">score ' + Number(s.score || 0).toFixed(2) + "</span></div>" +
      "<pre>" + esc(s.extract || "") + "</pre>" +
      '<div class="score-bar"><i style="width:' + (score * 100).toFixed(0) + '%"></i></div></div>';
  }).join("") + trace;
}

// --------------------------------------------------------------- vision
// Affiche ce que le moteur vision a réellement lu sur la capture, et si l'image
// a bien été transmise au modèle : sans ce bloc, impossible de distinguer
// « capture ignorée » de « capture lue mais non exploitée ».
function renderVision(payload) {
  const box = $("#vision");
  if (!box) return;
  const img = payload.image || {};
  const obs = payload.observation || {};
  if (!img.fournie) {
    box.innerHTML = '<div class="empty">' + icon("image") +
      "<p>Aucune capture envoyée : l'analyse repose uniquement sur les données de marché et vos cours.</p></div>";
    return;
  }
  const etat = img.lecture_reussie
    ? '<span class="badge ok">Lecture réussie</span>'
    : (img.transmise_au_modele ? '<span class="badge err">Lecture en échec</span>'
      : '<span class="badge warn">Aucune IA vision configurée</span>');
  let html = '<p class="small">Capture reçue : <b>' + Number(img.taille_ko || 0).toFixed(1) + " Ko</b>" +
    (img.reduite ? " (réduite automatiquement depuis " + Number(img.taille_origine_ko || 0).toFixed(1) + " Ko)" : "") +
    " · " + etat +
    (img.modele_vision ? ' · modèle : <span class="mono">' + esc(img.modele_vision) + "</span>" : "") + "</p>";
  if (!img.transmise_au_modele) {
    html += '<div class="banner">' + icon("warn") +
      "<div>La capture a bien été reçue, mais <b>aucune IA vision n'est configurée</b> : " +
      "elle n'a pas été analysée. Ajoutez une clé API (onglet <b>Aide</b>) pour activer la lecture d'image.</div></div>";
  } else if (!img.lecture_reussie) {
    html += '<div class="banner banner-err">' + icon("warn") +
      "<div>La capture a été transmise mais n'a pas pu être lue : " +
      esc(img.erreur || "raison inconnue") + "</div></div>";
  }
  const lignes = [
    ["Type de graphique", obs.chart_type],
    ["Actif lu sur l'image", obs.symbol_guess],
    ["Unité de temps lue", obs.timeframe],
    ["Tendance observée", obs.trend],
    ["Fourchette de prix lue", obs.price_range],
    ["Dernier prix estimé", obs.last_price],
    ["Volume", obs.volume],
  ].filter((paire) => paire[1] && String(paire[1]).trim());
  if (lignes.length) {
    html += '<dl class="kv">' + lignes.map((paire) =>
      "<dt>" + esc(paire[0]) + "</dt><dd>" + esc(String(paire[1])) + "</dd>").join("") + "</dl>";
  }
  if ((obs.patterns || []).length) {
    html += '<p class="small"><b>Figures vues sur la capture :</b> ' + esc(obs.patterns.join(", ")) + "</p>";
  }
  if ((obs.levels || []).length) {
    html += '<p class="small"><b>Niveaux relevés sur la capture :</b> ' + esc(obs.levels.join(" · ")) + "</p>";
  }
  if ((obs.indicators || []).length) {
    html += '<p class="small"><b>Indicateurs visibles :</b> ' + esc(obs.indicators.join(", ")) + "</p>";
  }
  if (obs.summary) html += "<p>" + esc(obs.summary) + "</p>";
  if ((obs.uncertainties || []).length) {
    html += '<p class="small muted">Incertitudes de lecture : ' + esc(obs.uncertainties.join(" · ")) + "</p>";
  }
  if (obs.confidence) {
    html += '<p class="small muted">Confiance de la description : ' + pct(obs.confidence * 100, 0) + "</p>";
  }
  box.innerHTML = html;
}

// ----------------------------------------------------------- cohérence
// Confronte l'actif lu sur la capture et le symbole des données chiffrées.
function renderCoherence(payload) {
  const box = $("#coherence");
  if (!box) return;
  const co = payload.coherence || {};
  if (co.incoherent) {
    const symbole = co.symbole_capture || "";
    box.innerHTML = '<div class="banner">' + icon("warn") +
      "<div><b>La capture ne correspond pas au symbole analysé</b><p>" + esc(co.message || "") + "</p>" +
      (symbole
        ? '<button type="button" class="btn" id="btn-analyser-capture" data-symbole="' + esc(symbole) + '">' +
          icon("analyse", "ico-sm") + " Analyser " + esc(symbole) + " avec cette capture</button>"
        : "") +
      "</div></div>";
  } else if (co.symbole_capture && co.message) {
    box.innerHTML = '<div class="banner banner-info">' + icon("info") + "<div>" + esc(co.message) + "</div></div>";
  } else {
    box.innerHTML = "";
  }
}

function renderFactors(analysis) {
  const box = $("#factors");
  if (!box) return;
  const indicateurs = (analysis && analysis.indicators) || {};
  const notes = Array.isArray(indicateurs.score_notes) ? indicateurs.score_notes.slice() : [];
  if (analysis && analysis.setup && analysis.setup.note) notes.push(analysis.setup.note);
  box.innerHTML = notes.length
    ? notes.map((n) => "<li>" + esc(String(n)) + "</li>").join("")
    : '<li class="muted">Indicateurs neutres : aucun facteur ne pèse sur le score.</li>';
}

function renderAnalysis(payload) {
  state.lastResult = payload;
  state.lastReportId = payload.report_id || "";
  const analysis = payload.analysis;
  renderVerdict(analysis);
  renderVision(payload);
  renderCoherence(payload);
  renderFactors(analysis);
  renderLevels(analysis);
  renderPatterns(analysis);
  renderSources(payload.sources, null, payload.rag_requetes);

  const meta = [];
  const avertissements = (payload.warnings || []).concat((analysis && analysis.warnings) || []);
  const repliLocal = avertissements.some((w) => /Appel LLM impossible|Erreur LLM/i.test(w));
  if (payload.mode === "ia") {
    meta.push('<span class="badge ok">' + icon("ia", "ico-sm") + " Analyse IA · " +
      esc(payload.provider) + " " + esc(payload.model || "") + "</span>");
  } else if (repliLocal) {
    meta.push('<span class="badge warn">' + icon("warn", "ico-sm") + " Repli local (appel IA en échec)</span>");
  } else {
    meta.push('<span class="badge warn">Moteur technique + cours (sans LLM)</span>');
  }
  meta.push('<span class="badge info">Recherche : ' + esc(payload.rag_mode || "—") + "</span>");
  if (analysis && analysis.instrument) {
    meta.push('<span class="badge">Données : ' + esc(analysis.instrument.source) + " · " +
      esc(analysis.instrument.candles) + " bougies</span>");
  }
  if (payload.report_id) meta.push('<span class="badge no-dot">Analyse #' + esc(payload.report_id) + "</span>");
  const image = payload.image || {};
  if (image.fournie) {
    meta.push(image.lecture_reussie
      ? '<span class="badge ok">' + icon("image", "ico-sm") + " Capture lue par l'IA</span>"
      : (image.transmise_au_modele
        ? '<span class="badge err">Capture non lue</span>'
        : '<span class="badge warn">Capture non analysée (aucune IA vision)</span>'));
  }
  $("#meta").innerHTML = meta.join(" ");

  const uniques = avertissements.filter((w, i) => avertissements.indexOf(w) === i);
  $("#warnings").innerHTML = uniques.length
    ? '<div class="banner banner-info">' + icon("info") + '<div><b>Points d\'attention</b><ul>' +
      uniques.map((w) => "<li>" + esc(w) + "</li>").join("") + "</ul></div></div>"
    : "";

  $("#answer").innerHTML = mdToHtml(payload.answer || "");
  const carte = $("#result-card");
  carte.hidden = false;

  const lien = $("#btn-report-export");
  if (lien) {
    if (payload.report_id) {
      lien.href = apiUrl("/api/reports/" + payload.report_id + "/export");
      lien.hidden = false;
    } else {
      lien.hidden = true;
    }
  }

  if (analysis && analysis.instrument && analysis.instrument.symbol) {
    state.symbol = analysis.instrument.symbol;
    $("#symbol").value = analysis.instrument.symbol;
    sauverPreferences();
  }
  return analysis;
}

function erreurAnalyse(message) {
  const carte = $("#result-card");
  carte.hidden = false;
  $("#verdict").className = "verdict";
  $("#verdict").innerHTML = "";
  $("#meta").innerHTML = '<span class="badge err">Analyse impossible</span>';
  $("#warnings").innerHTML = '<div class="banner banner-err">' + icon("warn") +
    "<div><b>L'analyse n'a pas pu être produite</b><br>" + esc(message) + "</div></div>";
  $("#answer").innerHTML = "";
  $("#levels").innerHTML = "";
  $("#patterns").innerHTML = "";
  $("#sources").innerHTML = "";
}

async function runAnalyse(event) {
  if (event) event.preventDefault();
  const button = $("#btn-analyse");
  const question = $("#question").value.trim();
  const symbol = $("#symbol").value.trim().toUpperCase();
  const body = {
    symbol: symbol,
    period: $("#period").value,
    interval: $("#interval").value,
    question: question,
    top_k: Number($("#topk").value || 5),
    image_base64: state.imageDataUrl || "",
  };
  if (!body.symbol && !body.image_base64) {
    toast("Indiquez un symbole (ex. AAPL) ou joignez une capture de graphique.", "warn");
    $("#symbol").focus();
    return;
  }

  setBusy(button, true, "Analyse en cours…");
  try {
    if (state.docFiles.length && !state.docUploaded) {
      setBusy(button, true, "Indexation des documents…");
      const ok = await uploadPendingDocuments();
      if (!ok) return;
      setBusy(button, true, "Analyse en cours…");
    }
    const payload = await api("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    renderAnalysis(payload);
    toast("Analyse terminée — " + (payload.sources || []).length + " extrait(s) de cours utilisé(s).", "ok");
    if (payload.analysis && payload.analysis.instrument) {
      const symbole = payload.analysis.instrument.symbol;
      state.symbol = symbole;
      $("#chart-symbol").value = symbole;
      loadChart();
    }
  } catch (err) {
    erreurAnalyse(err.message);
    toast("Échec de l'analyse : " + esc(err.message), "err", 9000);
  } finally {
    setBusy(button, false);
  }
}

/* Prépare une zone de dépôt : le champ fichier reste intact, seul le texte change. */
function preparerDropzone(zone) {
  let corps = zone.querySelector(".dz-body");
  if (!corps) {
    corps = document.createElement("div");
    corps.className = "dz-body";
    while (zone.firstChild) corps.appendChild(zone.firstChild);
    zone.appendChild(corps);
  }
  return corps;
}

function initAnalyse() {
  $("#form-analyse").addEventListener("submit", runAnalyse);
  $("#btn-analyse").addEventListener("click", runAnalyse);
  $("#period").value = state.period;
  $("#interval").value = state.interval;

  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") runAnalyse();
  });

  /* -------------------------------------------- bascule capture / cours */
  const modes = {
    image: {
      bouton: $("#dz-mode-image"),
      zone: $("#dropzone"),
      aide: "Une capture active la lecture d'image par IA et l'analyse croisée avec les données réelles.",
    },
    doc: {
      bouton: $("#dz-mode-doc"),
      zone: $("#dropzone-doc"),
      aide: "Le document est indexé, puis l'analyse s'appuie dessus avec des citations [Source n].",
    },
  };

  const activerMode = (nom) => {
    Object.keys(modes).forEach((cle) => {
      const actif = cle === nom;
      modes[cle].bouton.classList.toggle("active", actif);
      modes[cle].bouton.setAttribute("aria-pressed", actif ? "true" : "false");
      modes[cle].zone.hidden = !actif;
    });
    $("#dz-help").textContent = modes[nom].aide;
  };
  modes.image.bouton.addEventListener("click", () => activerMode("image"));
  modes.doc.bouton.addEventListener("click", () => activerMode("doc"));

  /* ----------------------------------------------------- capture d'image */
  const dropzone = $("#dropzone");
  const input = $("#image-input");
  const corpsImage = preparerDropzone(dropzone);

  const applyFile = (file) => {
    if (!file) return;
    if (!/^image\//.test(file.type || "")) {
      toast("Format non reconnu : joignez une image (PNG, JPEG, WEBP).", "err");
      return;
    }
    if (file.size > (APP.maxUploadMb || 12) * 1024 * 1024) {
      toast("Fichier trop volumineux (limite " + (APP.maxUploadMb || 12) + " Mo).", "err");
      return;
    }
    const reader = new FileReader();
    reader.onerror = () => toast("Lecture du fichier impossible.", "err");
    reader.onload = () => {
      state.imageDataUrl = String(reader.result || "");
      corpsImage.innerHTML = '<span class="dropzone-pill">' + icon("check") + "<b>Capture prête</b></span>" +
        '<span class="small">' + esc(file.name) + " — cliquez pour remplacer</span>" +
        '<img src="' + state.imageDataUrl + '" alt="Aperçu de la capture du graphique">';
    };
    reader.readAsDataURL(file);
  };

  dropzone.addEventListener("click", () => input.click());
  dropzone.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); input.click(); }
  });
  input.addEventListener("change", () => applyFile(input.files[0]));

  /* ------------------------------------------- cours : PDF / notes glissés */
  const dropzoneDoc = $("#dropzone-doc");
  const inputDoc = $("#doc-input");
  const corpsDoc = preparerDropzone(dropzoneDoc);
  const EXTENSIONS = [".pdf", ".md", ".markdown", ".txt", ".text", ".rst"];

  const estAccepte = (nom) => EXTENSIONS.some((ext) => (nom || "").toLowerCase().endsWith(ext));

  const applyDocs = (fileList) => {
    const fichiers = Array.prototype.slice.call(fileList || []);
    if (!fichiers.length) return;
    const rejetes = fichiers.filter((f) => !estAccepte(f.name));
    if (rejetes.length) {
      toast("Format non supporté : " + esc(rejetes.map((f) => f.name).join(", ")) +
        " — formats acceptés : PDF, .md, .txt.", "err", 8000);
    }
    const acceptes = fichiers.filter((f) => estAccepte(f.name));
    if (!acceptes.length) return;
    state.docFiles = acceptes;
    state.docUploaded = false;
    corpsDoc.innerHTML = '<span class="dropzone-pill">' + icon("check") + "<b>" + acceptes.length +
      " document(s) prêt(s)</b></span>" +
      '<span class="small">' + esc(acceptes.map((f) => f.name).join(", ")) + " — cliquez pour remplacer</span>" +
      '<span class="small">Ils seront indexés automatiquement, puis utilisés par l\'analyse.</span>';
    $("#doc-status").textContent = "Prêt à indexer : cliquez sur « Analyser et prédire ».";
  };

  const ouvrirSelecteurDoc = () => inputDoc.click();
  dropzoneDoc.addEventListener("click", ouvrirSelecteurDoc);
  dropzoneDoc.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); ouvrirSelecteurDoc(); }
  });
  inputDoc.addEventListener("change", () => applyDocs(inputDoc.files));

  /* ------------------------------------------------- glisser-déposer */
  [[dropzone, applyFile], [dropzoneDoc, applyDocs]].forEach(([zone, appliquer]) => {
    ["dragenter", "dragover"].forEach((evt) => zone.addEventListener(evt, (e) => {
      e.preventDefault(); zone.classList.add("dragover");
    }));
    ["dragleave", "dragend"].forEach((evt) => zone.addEventListener(evt, () => zone.classList.remove("dragover")));
    zone.addEventListener("drop", (e) => {
      e.preventDefault();
      zone.classList.remove("dragover");
      if (!e.dataTransfer || !e.dataTransfer.files.length) return;
      appliquer(zone === dropzoneDoc ? e.dataTransfer.files : e.dataTransfer.files[0]);
    });
  });

  /* --------------------------------------------------------- exemple guidé */
  $("#btn-demo").addEventListener("click", () => {
    $("#symbol").value = "AAPL";
    $("#question").value = "Analyse ce graphique, identifie la figure principale et donne-moi un plan avec stop et objectifs.";
    $("#period").value = "6mo";
    $("#interval").value = "1d";
    activerMode("image");
    runAnalyse();
  });

  /* ------------------------------------------------------------ copie */
  $("#btn-copy").addEventListener("click", async () => {
    const texte = ($("#result-card").hidden ? "" : ($("#answer").innerText || "")).trim();
    if (!texte) { toast("Lancez d'abord une analyse.", "warn"); return; }
    try {
      await navigator.clipboard.writeText(texte);
      toast("Conclusion copiée dans le presse-papiers.", "ok");
    } catch (err) {
      toast("Copie impossible : sélectionnez le texte manuellement.", "warn");
    }
  });

  // mémorise le symbole saisi même sans lancer d'analyse
  $("#symbol").addEventListener("change", () => {
    state.symbol = ($("#symbol").value.trim().toUpperCase() || "AAPL");
    $("#chart-symbol").value = state.symbol;
    sauverPreferences();
  });
  [["#period", "period"], ["#interval", "interval"]].forEach(([sel, cle]) => {
    $(sel).addEventListener("change", () => { state[cle] = $(sel).value; sauverPreferences(); });
  });
}

async function uploadPendingDocuments() {
  if (!state.docFiles.length || state.docUploaded) return true;
  const statut = $("#doc-status");
  statut.innerHTML = '<span class="badge"><span class="badge-spinner"></span> Indexation de ' +
    state.docFiles.length + " document(s)…</span>";
  const form = new FormData();
  state.docFiles.forEach((file) => form.append("files", file));
  try {
    const payload = await api("/api/knowledge/upload", { method: "POST", body: form });
    const resultats = payload.resultats || [];
    const ok = resultats.filter((r) => r.status !== "error");
    const ko = resultats.filter((r) => r.status === "error");
    ko.forEach((r) => toast(esc(r.source) + " : " + esc(r.error), "err", 9000));
    if (!ok.length) {
      statut.innerHTML = '<span class="badge err">Indexation impossible</span>';
      return false;
    }
    const extraits = ok.reduce((total, r) => total + (r.chunks || 0), 0);
    state.docUploaded = true;
    statut.innerHTML = '<span class="badge ok">' + ok.length + " document(s) indexé(s) — " + extraits +
      " extraits ajoutés</span>" + (ko.length ? ' <span class="badge warn">' + ko.length + " en erreur</span>" : "");
    toast(ok.length + " document(s) indexé(s) (" + extraits + " extraits) : l'analyse va s'appuyer dessus.", "ok", 7000);
    return true;
  } catch (err) {
    statut.innerHTML = '<span class="badge err">Indexation impossible : ' + esc(err.message) + "</span>";
    toast("Indexation impossible : " + esc(err.message), "err", 9000);
    return false;
  }
}

/* ------------------------------------------------------------- graphique */

function initChartControls() {
  $("#chart-limit").addEventListener("change", () => {
    state.limit = Number($("#chart-limit").value);
    sauverPreferences();
    loadChart();
  });
  $("#chart-refresh").addEventListener("click", () => loadChart(true));
  let minuteur = null;
  window.addEventListener("resize", () => {
    clearTimeout(minuteur);
    minuteur = setTimeout(drawChart, 120);
  });
}

async function loadChart(force) {
  const symbol = ($("#chart-symbol").value.trim().toUpperCase()) || state.symbol;
  state.chart = null;                       // évite d'afficher d'anciennes données
  $("#chart-status").innerHTML = '<span class="badge"><span class="badge-spinner"></span> Chargement de ' +
    esc(symbol) + "…</span>";
  try {
    const payload = await api("/api/market/" + encodeURIComponent(symbol) +
      "?period=" + encodeURIComponent($("#chart-period").value) +
      "&interval=" + encodeURIComponent($("#chart-interval").value) +
      "&limit=" + state.limit + (force ? "&refresh=true" : ""));
    state.chart = payload;
    state.symbol = payload.instrument.symbol;
    $("#chart-symbol").value = payload.instrument.symbol;
    const resume = payload.summary || {};
    $("#chart-status").innerHTML = "<b>" + esc(payload.instrument.symbol) + "</b> " +
      esc(payload.instrument.name || "") + " — " + payload.candles.length + " bougies · source " +
      esc(resume.source || "—") + ' · <span class="pill ' + verdictClass(resume.score) + '">' +
      esc(resume.label || "—") + "</span> <b>" + Number(resume.score || 0).toFixed(1) + "/100</b>";
    sauverPreferences();
    drawChart();
  } catch (err) {
    state.chart = null;
    $("#chart-status").innerHTML = '<span class="badge err">Données indisponibles</span> ' + esc(err.message);
    drawChart();
  }
}

function drawChart() {
  const canvas = $("#chart");
  if (!canvas) return;
  const data = state.chart;
  const dpr = window.devicePixelRatio || 1;
  const width = canvas.clientWidth || 900;
  const height = canvas.clientHeight || 560;
  canvas.width = Math.floor(width * dpr);
  canvas.height = Math.floor(height * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  const styles = getComputedStyle(document.body);
  const c = (nom, defaut) => (styles.getPropertyValue(nom) || defaut).trim() || defaut;

  if (!data || !data.candles || !data.candles.length) {
    canvas._geom = null;                     // le curseur ne doit pas réutiliser une géométrie périmée
    ctx.fillStyle = c("--text-mute", "#78859f");
    ctx.font = "14px " + c("--font", "system-ui, sans-serif");
    ctx.textAlign = "center";
    ctx.fillText("Aucune donnée à afficher — vérifiez le symbole.", width / 2, height / 2);
    $("#chart-legend").innerHTML = "";
    return;
  }

  const candles = data.candles;
  const margin = { l: 6, r: 78, t: 12, b: 22 };
  const plotW = width - margin.l - margin.r;
  const plotH = height - margin.t - margin.b;
  const priceH = plotH * 0.66;
  const volH = plotH * 0.15;
  const rsiH = plotH * 0.19;
  const volTop = margin.t + priceH + 8;
  const rsiTop = volTop + volH + 8;

  let min = Infinity, max = -Infinity;
  candles.forEach((k) => { min = Math.min(min, k.l); max = Math.max(max, k.h); });
  const overlays = data.overlays || {};
  ["ema20", "ema50", "sma200", "bollinger_upper", "bollinger_lower"].forEach((key) => {
    (overlays[key] || []).forEach((v) => {
      if (v === null || v === undefined) return;
      min = Math.min(min, v); max = Math.max(max, v);
    });
  });
  const pad = (max - min) * 0.06 || max * 0.01 || 1;
  min -= pad; max += pad;

  const step = plotW / candles.length;
  const bodyW = Math.max(1.4, Math.min(9, step * 0.62));
  const xOf = (i) => margin.l + step * (i + 0.5);
  const yOf = (price) => margin.t + priceH - ((price - min) / (max - min)) * priceH;

  ctx.fillStyle = c("--surface-3", "#0f1523");
  ctx.fillRect(margin.l, margin.t, plotW, plotH);
  ctx.strokeStyle = c("--border-soft", "#1a2436");
  ctx.lineWidth = 1;
  ctx.font = "11px " + c("--font", "system-ui, sans-serif");
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";

  const gridLines = 6;
  for (let i = 0; i <= gridLines; i++) {
    const price = min + ((max - min) * i) / gridLines;
    const y = yOf(price);
    ctx.beginPath(); ctx.moveTo(margin.l, y); ctx.lineTo(margin.l + plotW, y); ctx.stroke();
    ctx.fillStyle = c("--text-mute", "#78859f");
    ctx.fillText(fmt(price), margin.l + plotW + 6, y);
  }

  ctx.strokeStyle = c("--border", "#23304a");
  ctx.strokeRect(margin.l, volTop, plotW, volH);
  ctx.strokeRect(margin.l, rsiTop, plotW, rsiH);

  const maxVolume = Math.max.apply(null, candles.map((k) => k.v || 0)) || 1;
  candles.forEach((k, i) => {
    const x = xOf(i);
    const up = k.c >= k.o;
    const couleur = up ? "#22c55e" : "#ef4444";
    const vh = ((k.v || 0) / maxVolume) * (volH - 4);
    ctx.fillStyle = up ? "rgba(34,197,94,0.5)" : "rgba(239,68,68,0.5)";
    ctx.fillRect(x - bodyW / 2, volTop + volH - vh - 2, bodyW, vh);
    ctx.strokeStyle = couleur;
    ctx.beginPath(); ctx.moveTo(x, yOf(k.h)); ctx.lineTo(x, yOf(k.l)); ctx.stroke();
    ctx.fillStyle = couleur;
    const yo = yOf(k.o), yc = yOf(k.c);
    ctx.fillRect(x - bodyW / 2, Math.min(yo, yc), bodyW, Math.max(1, Math.abs(yc - yo)));
  });

  const drawSeries = (values, color, dash, lineWidth) => {
    if (!values) return;
    ctx.strokeStyle = color;
    ctx.lineWidth = lineWidth || 1.3;
    ctx.setLineDash(dash || []);
    ctx.beginPath();
    let started = false;
    values.forEach((v, i) => {
      if (v === null || v === undefined) { started = false; return; }
      const x = xOf(i), y = yOf(v);
      if (!started) { ctx.moveTo(x, y); started = true; } else { ctx.lineTo(x, y); }
    });
    ctx.stroke();
    ctx.setLineDash([]);
  };
  drawSeries(overlays.bollinger_upper, "rgba(148,163,184,0.6)", [3, 3]);
  drawSeries(overlays.bollinger_lower, "rgba(148,163,184,0.6)", [3, 3]);
  drawSeries(overlays.sma200, "#f59e0b", [], 1.6);
  drawSeries(overlays.ema50, "#b98cff", [], 1.4);
  drawSeries(overlays.ema20, "#5b93ff", [], 1.5);

  const levels = data.levels || {};
  const drawLevel = (price, color, label) => {
    if (price === undefined || price === null || price < min || price > max) return;
    const y = yOf(price);
    ctx.strokeStyle = color;
    ctx.setLineDash([6, 4]);
    ctx.beginPath(); ctx.moveTo(margin.l, y); ctx.lineTo(margin.l + plotW, y); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = color;
    ctx.font = "10px " + c("--font", "system-ui, sans-serif");
    ctx.fillText(label + " " + fmt(price), margin.l + 6, y - 7);
  };
  (levels.resistances || []).slice(0, 3).forEach((lvl) => drawLevel(lvl.price, "rgba(239,68,68,0.75)", "R"));
  (levels.supports || []).slice(0, 3).forEach((lvl) => drawLevel(lvl.price, "rgba(34,197,94,0.75)", "S"));

  const resume = data.summary || {};
  const offset = resume.offset || 0;
  (resume.patterns || []).forEach((p) => {
    const index = (p.index || 0) - offset;
    if (index < 0 || index >= candles.length) return;
    const bougie = candles[index];
    const x = xOf(index);
    const haussier = p.bias === "haussier";
    const y = haussier ? yOf(bougie.l) + 12 : yOf(bougie.h) - 12;
    ctx.fillStyle = haussier ? "#22c55e" : p.bias === "baissier" ? "#ef4444" : "#f59e0b";
    ctx.beginPath(); ctx.arc(x, y, 3.4, 0, Math.PI * 2); ctx.fill();
  });

  const rsiValues = data.rsi14 || [];
  if (rsiValues.length) {
    const yRsi = (value) => rsiTop + rsiH - (value / 100) * rsiH;
    [30, 50, 70].forEach((value) => {
      ctx.strokeStyle = value === 50 ? c("--border-soft", "#1a2436") : "rgba(91,147,255,0.28)";
      ctx.setLineDash(value === 50 ? [] : [4, 4]);
      ctx.beginPath(); ctx.moveTo(margin.l, yRsi(value)); ctx.lineTo(margin.l + plotW, yRsi(value)); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = c("--text-mute", "#78859f");
      ctx.font = "10px " + c("--font", "system-ui, sans-serif");
      ctx.fillText(String(value), margin.l + plotW + 6, yRsi(value));
    });
    ctx.strokeStyle = "#b98cff";
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    let startedRsi = false;
    rsiValues.forEach((v, i) => {
      if (v === null || v === undefined) { startedRsi = false; return; }
      const x = xOf(i), y = yRsi(v);
      if (!startedRsi) { ctx.moveTo(x, y); startedRsi = true; } else { ctx.lineTo(x, y); }
    });
    ctx.stroke();
    ctx.fillStyle = c("--text-dim", "#a3b1cb");
    ctx.font = "11px " + c("--font", "system-ui, sans-serif");
    ctx.fillText("RSI(14)", margin.l + 6, rsiTop + 12);
  }

  ctx.fillStyle = c("--text-mute", "#78859f");
  ctx.font = "10.5px " + c("--font", "system-ui, sans-serif");
  ctx.textAlign = "center";
  const ticks = Math.min(8, candles.length);
  for (let i = 0; i < ticks; i++) {
    const index = Math.floor((i * (candles.length - 1)) / Math.max(1, ticks - 1));
    ctx.fillText(candles[index].date, xOf(index), height - 6);
  }
  ctx.textAlign = "left";

  const derniere = candles[candles.length - 1];
  ctx.strokeStyle = "rgba(232,238,252,0.3)";
  ctx.setLineDash([2, 3]);
  ctx.beginPath(); ctx.moveTo(margin.l, yOf(derniere.c)); ctx.lineTo(margin.l + plotW, yOf(derniere.c)); ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = c("--text", "#e9eefb");
  ctx.fillRect(margin.l + plotW + 2, yOf(derniere.c) - 8, margin.r - 6, 16);
  ctx.fillStyle = c("--bg", "#080b12");
  ctx.textBaseline = "middle";
  ctx.fillText(fmt(derniere.c), margin.l + plotW + 6, yOf(derniere.c));

  canvas._geom = { margin, plotW, plotH, volTop, rsiTop, rsiH, step, candles, xOf, yOf };

  $("#chart-legend").innerHTML =
    '<span><i style="background:#5b93ff"></i>EMA 20</span>' +
    '<span><i style="background:#b98cff"></i>EMA 50</span>' +
    '<span><i style="background:#f59e0b"></i>SMA 200</span>' +
    '<span><i style="background:#94a3b8"></i>Bollinger (20, 2σ)</span>' +
    '<span><i style="background:#22c55e"></i>Supports</span>' +
    '<span><i style="background:#ef4444"></i>Résistances</span>';
}

function initCrosshair() {
  const canvas = $("#chart");
  const tooltip = $("#tooltip");
  let frame = null;

  canvas.addEventListener("mousemove", (event) => {
    const geom = canvas._geom;
    const data = state.chart;
    // Correctif : sans données, aucune géométrie n'est disponible — on sort proprement.
    if (!geom || !data || !data.candles) { tooltip.style.display = "none"; return; }
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const index = Math.floor((x - geom.margin.l) / geom.step);
    if (index < 0 || index >= geom.candles.length) { tooltip.style.display = "none"; return; }

    const bougie = geom.candles[index];
    const rsi = (data.rsi14 || [])[index];
    const overlay = data.overlays || {};
    const ligne = (label, value) => label + " " + (value === null || value === undefined ? "—" : fmt(value));
    tooltip.textContent = [
      bougie.date,
      "O " + fmt(bougie.o) + "   H " + fmt(bougie.h),
      "B " + fmt(bougie.l) + "   C " + fmt(bougie.c),
      "Volume " + Number(bougie.v || 0).toLocaleString("fr-FR", { maximumFractionDigits: 0 }),
      ligne("EMA20", (overlay.ema20 || [])[index]),
      ligne("EMA50", (overlay.ema50 || [])[index]),
      ligne("SMA200", (overlay.sma200 || [])[index]),
      ligne("RSI14", rsi),
    ].join("\n");
    tooltip.style.display = "block";
    const largeur = tooltip.offsetWidth || 190;
    tooltip.style.left = Math.min(rect.width - largeur - 12, Math.max(8, x + 14)) + "px";
    tooltip.style.top = Math.max(8, event.clientY - rect.top - 70) + "px";

    // Redessin limité à une image par rafale d'événements (évite le scintillement).
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = null;
      drawChart();
      const ctx = canvas.getContext("2d");
      const dpr = window.devicePixelRatio || 1;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.strokeStyle = "rgba(232,238,252,0.28)";
      ctx.setLineDash([3, 3]);
      const cx = geom.xOf(index);
      ctx.beginPath(); ctx.moveTo(cx, geom.margin.t); ctx.lineTo(cx, geom.margin.t + geom.plotH); ctx.stroke();
      ctx.setLineDash([]);
    });
  });
  canvas.addEventListener("mouseleave", () => { tooltip.style.display = "none"; });
}

/* ----------------------------------------------------------------- chat */

function initChat() {
  const form = $("#form-chat");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const question = $("#chat-question").value.trim();
    if (!question) return;
    const button = $("#btn-chat");
    const zone = $("#chat-history");
    if (zone.querySelector(".empty")) zone.innerHTML = "";
    zone.insertAdjacentHTML("beforeend",
      '<div class="msg user"><div class="who">Vous</div><div>' + esc(question) + "</div></div>" +
      '<div class="msg bot" id="chat-attente"><div class="who">Recherche…</div>' +
      '<span class="skeleton-line"></span></div>');
    zone.lastElementChild.scrollIntoView({ behavior: "smooth", block: "nearest" });
    setBusy(button, true, "Recherche…");
    try {
      const payload = await api("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: question, top_k: Number($("#chat-topk").value || 5) }),
      });
      const attente = $("#chat-attente");
      if (attente) attente.remove();
      const source = payload.mode === "ia"
        ? "Réponse rédigée par " + esc(payload.provider) + " " + esc(payload.model || "")
        : "Mode technique : extraits classés (ajoutez une clé LLM pour une réponse rédigée)";
      zone.insertAdjacentHTML("beforeend",
        '<div class="msg bot"><div class="who">Assistant</div><div class="md">' + mdToHtml(payload.answer) +
        '</div><p class="small meta">' + source + " · recherche " + esc(payload.rag_mode || "—") +
        " · " + (payload.sources || []).length + " source(s)</p></div>");
      const avertissements = payload.warnings || [];
      if (avertissements.length) toast(esc(avertissements[0]), "warn", 7000);
      $("#chat-question").value = "";
      zone.lastElementChild.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (err) {
      const attente = $("#chat-attente");
      if (attente) attente.remove();
      toast("Erreur du chat : " + esc(err.message), "err");
    } finally {
      setBusy(button, false);
    }
  });
}

/* --------------------------------------------------- base de connaissances */

async function loadDocuments() {
  const target = $("#docs-table");
  try {
    const payload = await api("/api/knowledge");
    const stats = payload.statistiques || {};
    $("#kb-stats").innerHTML =
      '<div class="stat"><span class="label">Documents</span><span class="value">' + (stats.documents || 0) + "</span></div>" +
      '<div class="stat"><span class="label">Extraits indexés</span><span class="value">' + (stats.chunks || 0) + "</span></div>" +
      '<div class="stat"><span class="label">Embeddings</span><span class="value mono">' + esc(stats.embedding_provider || "—") + "</span></div>" +
      '<div class="stat"><span class="label">Base vectorielle</span><span class="value mono">' + esc(stats.backend || "sqlite") + "</span></div>";

    if (!payload.documents.length) {
      target.innerHTML = '<div class="empty">' + icon("book") +
        "<p>Aucun document. Importez vos cours (PDF, Markdown, texte) pour enrichir l'analyse.</p></div>";
      return;
    }
    target.innerHTML = '<div class="table-wrap"><table class="data"><thead><tr><th>Document</th><th>Type</th>' +
      "<th>Extraits</th><th>Ajouté le</th><th></th></tr></thead><tbody>" +
      payload.documents.map((doc) =>
        "<tr><td><b>" + esc(doc.title) + '</b><div class="small">' + esc(doc.source) + "</div></td>" +
        "<td>" + esc(doc.kind) + '</td><td class="mono">' + doc.chunk_count + "</td>" +
        '<td class="small nowrap">' + esc(dateHeure(doc.created_at)) + "</td>" +
        '<td class="cell-actions">' +
          '<a class="btn subtle" href="' + apiUrl("/api/knowledge/" + doc.doc_id + "/export") +
            '" target="_blank" rel="noopener">JSON</a>' +
          '<button type="button" class="btn danger" data-doc="' + esc(doc.doc_id) + '">' + icon("trash", "ico-sm") + " Supprimer</button>" +
        "</td></tr>").join("") + "</tbody></table></div>";

    $$("#docs-table button[data-doc]").forEach((bouton) => {
      bouton.addEventListener("click", async () => {
        if (!confirm("Supprimer ce document de l'index ?")) return;
        try {
          await api("/api/knowledge/" + encodeURIComponent(bouton.dataset.doc), { method: "DELETE" });
          toast("Document supprimé.", "ok");
          loadDocuments();
        } catch (err) { toast("Suppression impossible : " + esc(err.message), "err"); }
      });
    });
  } catch (err) {
    target.innerHTML = '<div class="empty">' + icon("warn") +
      "<p>Impossible de charger la base : " + esc(err.message) + "</p></div>";
  }
}

function initKnowledge() {
  $("#form-upload").addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = $("#kb-files");
    if (!input.files.length) { toast("Sélectionnez au moins un fichier.", "warn"); return; }
    const button = $("#btn-upload");
    const form = new FormData();
    Array.prototype.forEach.call(input.files, (file) => form.append("files", file));
    setBusy(button, true, "Indexation…");
    try {
      const payload = await api("/api/knowledge/upload", { method: "POST", body: form });
      const ok = (payload.resultats || []).filter((r) => r.status !== "error");
      const ko = (payload.resultats || []).filter((r) => r.status === "error");
      toast(ok.length + " fichier(s) indexé(s)" + (ko.length ? ", " + ko.length + " en erreur" : "") + ".",
        ko.length ? "warn" : "ok", 7000);
      ko.forEach((r) => toast(esc(r.source) + " : " + esc(r.error), "err", 9000));
      input.value = "";
      loadDocuments();
    } catch (err) {
      toast("Import impossible : " + esc(err.message), "err");
    } finally {
      setBusy(button, false);
    }
  });

  $("#form-text").addEventListener("submit", async (event) => {
    event.preventDefault();
    const title = $("#text-title").value.trim();
    const content = $("#text-content").value.trim();
    if (title.length < 2 || content.length < 40) {
      toast("Indiquez un titre et un contenu (40 caractères minimum).", "warn");
      return;
    }
    const button = $("#btn-text");
    setBusy(button, true, "Indexation…");
    try {
      const payload = await api("/api/knowledge/text", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: title, content: content }),
      });
      toast("Note indexée (" + payload.resultat.chunks + " extraits).", "ok");
      $("#text-title").value = ""; $("#text-content").value = "";
      loadDocuments();
    } catch (err) {
      toast("Indexation impossible : " + esc(err.message), "err");
    } finally {
      setBusy(button, false);
    }
  });

  $("#form-search").addEventListener("submit", async (event) => {
    event.preventDefault();
    const query = $("#search-query").value.trim();
    if (!query) return;
    const button = $("#btn-search");
    const zone = $("#kb-results");
    zone.innerHTML = '<div class="empty"><span class="skeleton-line"></span></div>';
    setBusy(button, true, "Recherche…");
    try {
      const payload = await api("/api/knowledge/search?q=" + encodeURIComponent(query) + "&top_k=5");
      // Correctif : les résultats s'affichent dans CET onglet (avant, ils étaient
      // écrits dans un bloc masqué de l'onglet Analyse : l'utilisateur ne voyait rien).
      if (!payload.resultats.length) {
        zone.innerHTML = '<div class="empty">' + icon("analyse") +
          "<p>Aucun extrait trouvé pour « " + esc(query) + " ». Essayez d'autres termes ou importez de nouveaux cours.</p></div>";
      } else {
        zone.innerHTML = '<p class="small">' + payload.resultats.length + " extrait(s) — recherche " +
          esc(payload.mode) + "</p>";
        const bloc = document.createElement("div");
        bloc.className = "sources";
        zone.appendChild(bloc);
        renderSources(payload.resultats, bloc);
      }
    } catch (err) {
      zone.innerHTML = '<div class="banner banner-err">' + icon("warn") +
        "<div>Recherche impossible : " + esc(err.message) + "</div></div>";
    } finally {
      setBusy(button, false);
    }
  });

  $("#btn-reindex").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    setBusy(button, true, "Réindexation…");
    try {
      const payload = await api("/api/knowledge/reindex", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ force: true }),
      });
      toast("Réindexation terminée : " + (payload.chunks_indexed || 0) + " extraits.", "ok");
      loadDocuments();
    } catch (err) {
      toast("Réindexation impossible : " + esc(err.message), "err");
    } finally {
      setBusy(button, false);
    }
  });
}

/* ------------------------------------------------------------ historique */

async function loadReports() {
  const target = $("#reports-table");
  const filtre = ($("#reports-filter") && $("#reports-filter").value.trim().toUpperCase()) || "";
  try {
    const payload = await api("/api/reports?limit=30" + (filtre ? "&symbol=" + encodeURIComponent(filtre) : ""));
    if (!payload.analyses.length) {
      target.innerHTML = '<div class="empty">' + icon("history") + "<p>" +
        (filtre ? "Aucune analyse pour « " + esc(filtre) + " »." : "Aucune analyse enregistrée pour l'instant.") +
        "</p></div>";
      return;
    }
    target.innerHTML = '<div class="table-wrap"><table class="data"><thead><tr><th>Date</th><th>Marché</th>' +
      "<th>Conclusion</th><th>Score</th><th>Mode</th><th></th></tr></thead><tbody>" +
      payload.analyses.map((r) =>
        '<tr><td class="small nowrap">' + esc(dateHeure(r.created_at)) + "</td>" +
        "<td><b>" + esc(r.symbol || "image") + '</b><div class="small">' + esc(r.timeframe || "") + "</div></td>" +
        '<td><span class="pill ' + verdictClass(r.score) + '">' + esc(r.label || "—") + "</span></td>" +
        '<td class="mono">' + Number(r.score || 0).toFixed(1) + "</td>" +
        '<td class="small">' + esc(r.mode) + "</td>" +
        '<td class="cell-actions">' +
          '<button type="button" class="btn subtle" data-view="' + esc(r.report_id) + '">Ouvrir</button>' +
          '<a class="btn subtle" href="' + apiUrl("/api/reports/" + r.report_id + "/export") +
            '" target="_blank" rel="noopener">' + icon("download", "ico-sm") + "</a>" +
          '<button type="button" class="btn danger" data-del="' + esc(r.report_id) + '" aria-label="Supprimer cette analyse">' +
            icon("trash", "ico-sm") + "</button>" +
        "</td></tr>").join("") + "</tbody></table></div>";

    $$("#reports-table button[data-view]").forEach((bouton) => {
      bouton.addEventListener("click", () => openReport(bouton.dataset.view));
    });
    $$("#reports-table button[data-del]").forEach((bouton) => {
      bouton.addEventListener("click", async () => {
        if (!confirm("Supprimer cette analyse ?")) return;
        try {
          await api("/api/reports/" + encodeURIComponent(bouton.dataset.del), { method: "DELETE" });
          toast("Analyse supprimée.", "ok");
          loadReports();
        } catch (err) { toast("Suppression impossible : " + esc(err.message), "err"); }
      });
    });
  } catch (err) {
    target.innerHTML = '<div class="empty">' + icon("warn") +
      "<p>Impossible de charger l'historique : " + esc(err.message) + "</p></div>";
  }
}

async function openReport(reportId) {
  try {
    const report = await api("/api/reports/" + encodeURIComponent(reportId));
    renderAnalysis({
      answer: report.answer,
      mode: report.mode,
      provider: report.provider,
      model: report.model,
      analysis: (report.analysis && report.analysis.instrument) ? report.analysis : null,
      observation: report.observation,
      sources: report.sources || [],
      rag_mode: "—",
      warnings: report.warnings || [],
      report_id: report.report_id,
    });
    afficherOnglet("tab-analyse");
    const carte = $("#result-card");
    carte.scrollIntoView({ behavior: "smooth", block: "start" });
    carte.focus({ preventScroll: true });
    toast("Analyse #" + esc(report.report_id) + " ouverte.", "ok");
  } catch (err) {
    toast("Ouverture impossible : " + esc(err.message), "err");
  }
}

/* --------------------------------------------------------- notifications */

function renderNotifyStatus(payload) {
  const notif = (payload && payload.notifications) || {};
  const veille = (payload && payload.veille) || {};
  const cartes = [];
  cartes.push('<div class="stat"><span class="label">Canaux</span><span class="value">' +
    (notif.pret ? esc(notif.canaux) : "aucun") + "</span>" +
    (notif.pret ? "" : '<span class="small">Configurez Telegram ou un webhook</span>') + "</div>");
  cartes.push('<div class="stat"><span class="label">Veille automatique</span><span class="value">' +
    (veille.actif ? "active" : "inactive") + "</span>" +
    (veille.actif ? '<span class="small">toutes les ' + esc(veille.intervalle_minutes || veille.intervalle || "?") +
      " min</span>" : "") + "</div>");
  cartes.push('<div class="stat"><span class="label">Marchés suivis</span><span class="value">' +
    ((notif.watchlist || []).length) + "</span>" +
    '<span class="small">' + esc((notif.watchlist || []).join(", ")) + "</span></div>");
  cartes.push('<div class="stat"><span class="label">Seuil d\'envoi</span><span class="value">' +
    (notif.seuil_score !== undefined ? Number(notif.seuil_score).toFixed(0) : "0") + "</span>" +
    '<span class="small">|score| ≥ seuil</span></div>');
  $("#notif-status").innerHTML = cartes.join("");

  const etat = $("#veille-etat");
  if (!notif.pret) {
    etat.innerHTML = '<div class="banner banner-info">' + icon("info") +
      "<div>" + esc(notif.aide || "") + "</div></div>";
  } else if (veille.actif) {
    const dernier = veille.dernier_resultat || {};
    etat.innerHTML = '<div class="table-wrap"><table class="data"><thead><tr><th>Démarrée</th>' +
      "<th>Dernier passage</th><th>Marchés</th><th>Retenus</th><th>Envoi</th></tr></thead><tbody><tr>" +
      "<td>" + esc(dateHeure(veille.demarre_le)) + "</td>" +
      "<td>" + esc(dateHeure(veille.dernier_passage) || "en attente…") + "</td>" +
      '<td class="mono">' + (dernier.symboles_analyses || 0) + "</td>" +
      '<td class="mono">' + (dernier.symboles_retenus || 0) + "</td>" +
      "<td>" + (dernier.envoye ? '<span class="pill bull">envoyé</span>' : '<span class="pill neutral">aucun envoi</span>') +
      (veille.derniere_erreur ? '<div class="small">' + esc(veille.derniere_erreur) + "</div>" : "") +
      "</td></tr></tbody></table></div>" +
      (dernier.raison ? '<p class="small mt">' + esc(dernier.raison) + "</p>" : "");
  } else {
    etat.innerHTML = '<div class="empty">' + icon("bell") + "<p>Veille inactive" +
      (veille.raison ? " — " + esc(veille.raison) : "") + "</p></div>";
  }
  $("#notif-hint").textContent = notif.pret
    ? "Envoi via " + notif.canaux
    : "Configurez Telegram ou un webhook dans l'onglet Alertes.";
}

async function loadNotifyStatus() {
  try {
    renderNotifyStatus(await api("/api/notifications"));
  } catch (err) {
    $("#notif-status").innerHTML = '<div class="stat"><span class="label">État</span><span class="value">' +
      esc(err.message) + "</span></div>";
  }
}

function initNotifications() {
  $("#btn-notif-test").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    setBusy(button, true, "Envoi…");
    try {
      const payload = await api("/api/notifications/test", { method: "POST" });
      const envoye = (payload.resultats || []).some((r) => r.statut === "envoye");
      toast(envoye ? "Message de test envoyé via " + esc(payload.canaux) + "."
                   : "Envoi impossible : " + esc((payload.resultats || []).map((r) => r.detail).join(" ; ")),
        envoye ? "ok" : "err", 8000);
      loadNotifyStatus();
    } catch (err) {
      toast("Test impossible : " + esc(err.message), "err");
    } finally { setBusy(button, false); }
  });

  $("#form-notif-analysis").addEventListener("submit", async (event) => {
    event.preventDefault();
    const symbol = $("#notif-symbol").value.trim().toUpperCase();
    if (!symbol) { toast("Indiquez un symbole.", "warn"); return; }
    const button = $("#btn-notif-analysis");
    setBusy(button, true, "Analyse + envoi…");
    try {
      const payload = await api("/api/notifications/analysis", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          symbol: symbol,
          interval: $("#notif-interval").value,
          question: $("#notif-question").value.trim(),
        }),
      });
      toast(payload.envoye
        ? "Analyse de " + esc(payload.symbol) + " envoyée via " + esc(payload.canaux) + "."
        : "Analyse calculée mais non envoyée : " + esc((payload.resultats || []).map((r) => r.detail).join(" ; ")),
        payload.envoye ? "ok" : "warn", 8000);
    } catch (err) {
      toast("Analyse impossible : " + esc(err.message), "err");
    } finally { setBusy(button, false); }
  });

  $("#btn-veille-scan").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    setBusy(button, true, "Scan de la watchlist…");
    try {
      const payload = await api("/api/notifications/watchlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          watchlist: $("#veille-watchlist").value.trim(),
          interval_minutes: Number($("#veille-interval").value || 240),
          min_score: Number($("#veille-score").value || 0),
          start: false,
        }),
      });
      toast("Scan terminé : " + payload.symboles_analyses + " marché(s) analysé(s), " +
        payload.symboles_retenus + " retenu(s)" + (payload.envoye ? ", résumé envoyé." : "."),
        payload.envoye ? "ok" : "warn", 8000);
      loadNotifyStatus();
    } catch (err) {
      toast("Scan impossible : " + esc(err.message), "err");
    } finally { setBusy(button, false); }
  });

  $("#btn-veille-start").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    setBusy(button, true, "Démarrage…");
    try {
      const payload = await api("/api/notifications/veille/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          watchlist: $("#veille-watchlist").value.trim(),
          interval_minutes: Number($("#veille-interval").value || 240),
          min_score: Number($("#veille-score").value || 0),
          start: true,
        }),
      });
      toast(payload.demarree ? "Veille démarrée." : "Veille non démarrée : " + esc(((payload.notifications || {}).aide || "")),
        payload.demarree ? "ok" : "warn", 9000);
      renderNotifyStatus(payload);
    } catch (err) {
      toast("Démarrage impossible : " + esc(err.message), "err");
    } finally { setBusy(button, false); }
  });

  $("#btn-veille-stop").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    setBusy(button, true, "Arrêt…");
    try {
      await api("/api/notifications/veille/stop", { method: "POST" });
      toast("Veille arrêtée.", "ok");
      loadNotifyStatus();
    } catch (err) {
      toast("Arrêt impossible : " + esc(err.message), "err");
    } finally { setBusy(button, false); }
  });

  $("#btn-send-notif").addEventListener("click", async (event) => {
    const payload = state.lastResult;
    if (!payload || !payload.report_id) { toast("Lancez d'abord une analyse.", "warn"); return; }
    const button = event.currentTarget;
    setBusy(button, true, "Envoi…");
    try {
      const result = await api("/api/notifications/send", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ report_id: payload.report_id, base_url: APP.appBaseUrl || "" }),
      });
      toast(result.succes
        ? "Analyse envoyée via " + esc(result.canaux) + "."
        : "Envoi impossible : " + esc((result.resultats || []).map((r) => r.detail).join(" ; ")),
        result.succes ? "ok" : "err", 8000);
    } catch (err) {
      toast("Envoi impossible : " + esc(err.message), "err");
    } finally { setBusy(button, false); }
  });
}

/* ----------------------------------------------------------------- santé */

function rendreBadgeIA(llm, erreur) {
  if (llm.mode_demo) {
    return '<span class="badge warn">IA non configurée</span>';
  }
  if (erreur && erreur.message) {
    return '<span class="badge err" title="' + esc(erreur.message) + '">IA ' + esc(llm.provider_actif) +
      " — erreur (" + esc(heure(erreur.horodatage)) + ")</span>";
  }
  const modele = llm.modele_vision && llm.modele_vision !== "auto"
    ? " · " + esc(llm.modele_vision)
    : (llm.modele_vision === "auto" ? " · modèle auto" : "");
  return '<span class="badge ok">IA ' + esc(llm.provider_actif) + modele + "</span>";
}

function rendreBanniere(erreur, modeDemo) {
  const zone = $("#banner-ia");
  if (!zone) return;
  if (erreur && erreur.message) {
    zone.innerHTML = '<div class="banner banner-err" role="alert">' + icon("warn") +
      "<div><b>Le fournisseur IA a renvoyé une erreur</b> — l'application travaille en repli local " +
      "(moteur technique + vos cours).<br><code class=\"mono\">" + esc(erreur.message) + "</code><br>" +
      '<span class="small">Vérifiez <span class="mono">GEMINI_API_KEY</span> et le modèle ' +
      "(laissez <span class=\"mono\">VISION_MODEL</span> vide pour la détection automatique). " +
      'Détail : <a href="/api/health">/api/health</a>.</span></div></div>';
  } else if (!modeDemo) {
    zone.innerHTML = "";
  }
  // en mode démo sans erreur : on conserve la bannière explicative rendue par le serveur
}

async function loadHealth(forcer) {
  try {
    const payload = await api("/api/health" + (forcer ? "?refresh=true" : ""));
    const llm = payload.llm || {};
    const erreur = llm.derniere_erreur || {};
    const marche = payload.marche || {};
    const badges = [];

    badges.push(rendreBadgeIA(llm, erreur));
    if (llm.vision_disponible) badges.push('<span class="badge ok">Vision active</span>');
    else if (!llm.mode_demo) badges.push('<span class="badge err">Vision indisponible</span>');

    const source = marche.provider_actif;
    if (marche.test_en_cours && source === "inconnu") {
      badges.push('<span class="badge" title="Test des sources de marché en cours">Sources : test en cours…</span>');
    } else if (source === "demo" || marche.aucune_source_reelle) {
      badges.push('<span class="badge warn" title="Aucune source réelle joignable depuis le serveur">' +
        "Données de démonstration</span>");
    } else {
      badges.push('<span class="badge ok" title="Source utilisée pour les actions/indices ; crypto : ' +
        esc(marche.provider_actif_crypto || source) + '">Données ' + esc(source) + "</span>");
    }

    const kb = payload.connaissances || {};
    badges.push('<span class="badge info">' + (kb.chunks || 0) + " extraits de cours</span>");

    const notif = payload.notifications || {};
    const veille = payload.veille || {};
    if (veille.actif) {
      badges.push('<span class="badge ok">Veille active</span>');
    } else if (notif.pret) {
      badges.push('<span class="badge info">Alertes prêtes</span>');
    }

    $("#badges").innerHTML = badges.join(" ");
    rendreBanniere(erreur, llm.mode_demo);

    if (forcer) {
      const nb = (marche.providers_disponibles || []).length;
      toast(nb ? "Diagnostic actualisé : " + nb + " source(s) de marché disponible(s)."
               : "Diagnostic actualisé : aucune source de marché réelle joignable (mode démonstration).",
        nb ? "ok" : "warn", 6000);
    }
  } catch (err) {
    $("#badges").innerHTML = '<span class="badge err">Diagnostic indisponible</span>';
  }
}

/* ------------------------------------------------------------------ init */

// Bouton « Analyser <symbole> avec cette capture » proposé lorsqu'une incohérence
// est détectée entre l'actif de l'image et le symbole saisi.
function initCoherenceActions() {
  const box = $("#coherence");
  if (!box) return;
  box.addEventListener("click", (event) => {
    const bouton = event.target.closest("#btn-analyser-capture");
    if (!bouton) return;
    const symbole = (bouton.dataset.symbole || "").trim();
    if (!symbole) return;
    $("#symbol").value = symbole;
    sauverPreferences();
    toast(
      "Analyse relancée sur " + symbole + " avec la même capture" +
      (state.imageDataUrl ? "." : " (aucune capture jointe)."),
      "info",
      4500
    );
    runAnalyse();
  });
}

document.addEventListener("DOMContentLoaded", () => {
  chargerPreferences();
  initTheme();
  initTabs();
  initAnalyse();
  initCoherenceActions();
  initChartControls();
  initCrosshair();
  initChat();
  initKnowledge();
  initNotifications();

  $("#btn-health").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    button.classList.add("loading");
    try { await loadHealth(true); } finally {
      button.disabled = false;
      button.classList.remove("loading");
    }
  });

  $("#period").value = state.period;
  $("#interval").value = state.interval;
  $("#chart-symbol").value = state.symbol;
  $("#chart-period").value = state.period;
  $("#chart-interval").value = state.interval;
  $("#chart-limit").value = String(state.limit);
  $("#reports-filter").addEventListener("input", () => {
    clearTimeout(window._filtreMinuteur);
    window._filtreMinuteur = setTimeout(loadReports, 350);
  });
  $("#btn-refresh-reports").addEventListener("click", loadReports);

  loadHealth();
  loadChart();
  afficherOnglet(location.hash ? "tab-" + location.hash.slice(1) : "tab-analyse");

  // Le diagnostic complet (sondes réseau) se poursuit en arrière-plan côté serveur.
  setTimeout(() => loadHealth(), 6000);
});
