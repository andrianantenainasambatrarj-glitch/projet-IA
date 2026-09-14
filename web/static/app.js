/* TradeVision IA — interface web (JavaScript natif, aucune dépendance externe) */
"use strict";

const APP = window.APP_CONFIG || {};
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.prototype.slice.call(root.querySelectorAll(sel));

/* ------------------------------------------------------------------ utils */

function esc(value) {
  return String(value === null || value === undefined ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
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

function toast(message, kind = "info", delay = 5200) {
  const box = $("#toasts");
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.innerHTML = message;
  box.appendChild(el);
  setTimeout(() => el.remove(), delay);
}

/** Ajoute le jeton d'accès (si configuré) aux en-têtes d'une requête. */
function withToken(options = {}) {
  const opts = Object.assign({}, options);
  if (APP.apiToken) {
    opts.headers = Object.assign({}, opts.headers || {}, { "X-API-Token": APP.apiToken });
  }
  return opts;
}

/** Construit une URL d'API (avec ?token= pour les liens de téléchargement). */
function apiUrl(path) {
  if (!APP.apiToken) return path;
  return path + (path.indexOf("?") === -1 ? "?" : "&") + "token=" + encodeURIComponent(APP.apiToken);
}

async function api(path, options = {}) {
  const response = await fetch(path, withToken(options));
  const text = await response.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (err) { data = { raw: text }; }
  if (!response.ok) {
    const detail = data && data.detail;
    const message = typeof detail === "string" ? detail
      : Array.isArray(detail) ? detail.map((d) => (d.msg || JSON.stringify(d))).join(" ; ")
      : (data && data.erreur) || ("Erreur HTTP " + response.status);
    throw new Error(message);
  }
  return data;
}

function setBusy(button, busy, label) {
  if (!button) return;
  if (busy) {
    button.dataset.original = button.innerHTML;
    button.disabled = true;
    button.innerHTML = '<span class="spinner"></span> ' + (label || "Traitement…");
  } else {
    button.disabled = false;
    button.innerHTML = button.dataset.original || button.innerHTML;
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

    // Tableaux markdown
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

/* -------------------------------------------------------------------- tabs */

function initTabs() {
  $$("nav.tabs button").forEach((button) => {
    button.addEventListener("click", () => {
      $$("nav.tabs button").forEach((b) => b.classList.toggle("active", b === button));
      $$(".tab-panel").forEach((panel) => panel.classList.toggle("active", panel.id === button.dataset.tab));
      if (button.dataset.tab === "tab-graphique") drawChart();
      if (button.dataset.tab === "tab-connaissances") loadDocuments();
      if (button.dataset.tab === "tab-historique") loadReports();
      if (button.dataset.tab === "tab-notifications") loadNotifyStatus();
    });
  });
}

/* ------------------------------------------------------------------ analyse */

const state = {
  symbol: APP.defaultSymbol || "AAPL",
  period: APP.defaultPeriod || "6mo",
  interval: APP.defaultInterval || "1d",
  limit: 180,
  chart: null,
  lastResult: null,
  imageDataUrl: "",
  docFiles: [],
  docUploaded: false,
};

function verdictClass(score) {
  if (score >= 8) return "bull";
  if (score <= -8) return "bear";
  return "neutral";
}

function renderVerdict(analysis) {
  const box = $("#verdict");
  if (!analysis) { box.innerHTML = ""; return; }
  const cls = verdictClass(analysis.score);
  const setup = analysis.setup || {};
  box.className = "verdict " + cls;
  box.innerHTML =
    '<div class="main">' +
      '<p class="dir">' + esc(analysis.label || "—") + '</p>' +
      '<p class="sub">Score directionnel <b>' + Number(analysis.score).toFixed(1) + '/100</b> — ' +
      'confiance du moteur <b>' + pct((analysis.confidence || 0) * 100, 0) + '</b> — ' +
      'tendance : ' + esc((analysis.trend && analysis.trend.label) || "—") + '</p>' +
      '<div class="meter"><i style="width:' + Math.min(100, Math.abs(analysis.score)) + '%"></i></div>' +
      '<p class="sub" style="margin-top:8px">Plan proposé : <b>' + esc(setup.direction || "—") +
      '</b> · entrée ' + fmt(setup.entry) + ' · stop ' + fmt(setup.stop) +
      ' · objectifs ' + fmt(setup.target1) + ' / ' + fmt(setup.target2) +
      ' · R/R <b>' + (setup.risk_reward !== undefined ? setup.risk_reward : "—") + '</b></p>' +
    '</div>' +
    '<div class="kpi"><div class="label">Dernier prix</div><div class="value">' + fmt(analysis.price) + '</div></div>' +
    '<div class="kpi"><div class="label">RSI (14)</div><div class="value">' + fmt(analysis.indicators && analysis.indicators.rsi14, 1) + '</div></div>' +
    '<div class="kpi"><div class="label">Volatilité ATR</div><div class="value">' + pct(analysis.indicators && analysis.indicators.atr_pct, 2) + '</div></div>';
}

function renderLevels(analysis) {
  const box = $("#levels");
  if (!analysis || !analysis.levels) { box.innerHTML = '<div class="empty">Aucun niveau calculé.</div>'; return; }
  const rows = [];
  (analysis.levels.supports || []).forEach((lvl) => rows.push(
    '<tr><td><span class="pill bull">Support</span></td><td class="mono">' + fmt(lvl.price) +
    '</td><td>' + lvl.touches + ' touche(s)</td><td class="mono">-' + pct(lvl.distance_pct, 2) + '</td></tr>'));
  (analysis.levels.resistances || []).forEach((lvl) => rows.push(
    '<tr><td><span class="pill bear">Résistance</span></td><td class="mono">' + fmt(lvl.price) +
    '</td><td>' + lvl.touches + ' touche(s)</td><td class="mono">+' + pct(lvl.distance_pct, 2) + '</td></tr>'));
  box.innerHTML = rows.length
    ? '<table class="data"><thead><tr><th>Type</th><th>Prix</th><th>Solidité</th><th>Distance</th></tr></thead><tbody>' +
      rows.join("") + '</tbody></table>' +
      '<p class="small mt">Position dans le range : ' + pct(analysis.levels.range_position_pct, 0) +
      ' (bas ' + fmt(analysis.levels.period_low) + ' → haut ' + fmt(analysis.levels.period_high) + ')</p>'
    : '<div class="empty">Aucun niveau détecté.</div>';
}

function renderPatterns(analysis) {
  const box = $("#patterns");
  if (!analysis || !analysis.patterns || !analysis.patterns.length) {
    box.innerHTML = '<div class="empty">Aucune figure chartiste nette détectée sur la période.</div>';
    return;
  }
  box.innerHTML = analysis.patterns.map((p) => {
    const cls = p.bias === "haussier" ? "bull" : p.bias === "baissier" ? "bear" : "neutral";
    return '<div class="source" style="margin-bottom:8px"><div class="head"><b>' + esc(p.name) +
      '</b><span><span class="pill ' + cls + '">' + esc(p.bias) + '</span> confiance ' +
      pct((p.confidence || 0) * 100, 0) + '</span></div><pre>' + esc(p.description) +
      (p.target !== undefined ? "\nObjectif : " + fmt(p.target) : "") +
      (p.invalidation !== undefined ? " · Invalidation : " + fmt(p.invalidation) : "") + '</pre></div>';
  }).join("");
}

function renderSources(sources) {
  const box = $("#sources");
  if (!sources || !sources.length) {
    box.innerHTML = '<div class="empty">Aucun extrait de cours récupéré. Importez vos documents dans l\'onglet « Base de connaissances ».</div>';
    return;
  }
  box.innerHTML = sources.map((s, i) =>
    '<div class="source"><div class="head"><b>[Source ' + (i + 1) + '] ' + esc(s.title || s.source) +
    (s.section ? ' › ' + esc(s.section) : "") + (s.page ? ' · p.' + s.page : "") +
    '</b><span class="mono">score ' + Number(s.score || 0).toFixed(2) + '</span></div>' +
    '<pre>' + esc(s.extract || "") + '</pre></div>').join("");
}

function renderAnalysis(payload) {
  state.lastResult = payload;
  const analysis = payload.analysis;
  renderVerdict(analysis);
  renderLevels(analysis);
  renderPatterns(analysis);
  renderSources(payload.sources);

  const meta = [];
  meta.push('<span class="badge ' + (payload.mode === "ia" ? "ok" : "warn") + '">' +
    (payload.mode === "ia" ? "Analyse par IA · " + esc(payload.provider) + " " + esc(payload.model) :
      "Mode démo (moteur technique + cours)") + '</span>');
  meta.push('<span class="badge info">Recherche : ' + esc(payload.rag_mode || "—") + '</span>');
  if (payload.analysis) meta.push('<span class="badge">Données : ' + esc(payload.analysis.instrument.source) + '</span>');
  if (payload.report_id) meta.push('<span class="badge">Analyse #' + esc(payload.report_id) + '</span>');
  $("#meta").innerHTML = meta.join(" ");

  const warnings = (payload.warnings || []).concat((analysis && analysis.warnings) || []);
  const unique = warnings.filter((w, i) => warnings.indexOf(w) === i);
  $("#warnings").innerHTML = unique.length
    ? '<div class="banner">⚠️ <b>Points d\'attention</b><ul style="margin:8px 0 0 18px">' +
      unique.map((w) => "<li>" + esc(w) + "</li>").join("") + "</ul></div>"
    : "";

  $("#answer").innerHTML = mdToHtml(payload.answer || "");
  $("#result-card").style.display = "";
  if (analysis && analysis.instrument && analysis.instrument.symbol) {
    state.symbol = analysis.instrument.symbol;
    $("#symbol").value = analysis.instrument.symbol;
  }
  return analysis;
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
    return;
  }
  setBusy(button, true, "Analyse en cours…");
  try {
    // 1) Les documents joints (PDF/notes) sont d'abord indexés dans la base de
    //    connaissances : l'analyse RAG les utilisera immédiatement.
    if (state.docFiles.length && !state.docUploaded) {
      setBusy(button, true, "Indexation des documents…");
      const ok = await uploadPendingDocuments();
      if (!ok) { setBusy(button, false); return; }
      setBusy(button, true, "Analyse en cours…");
    }

    // 2) Analyse (symbole et/ou capture d'écran)
    const payload = await api("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    renderAnalysis(payload);
    toast("Analyse terminée (" + payload.sources.length + " extraits de cours utilisés).", "ok");
    if (payload.analysis && payload.analysis.instrument.symbol) {
      state.symbol = payload.analysis.instrument.symbol;
      loadChart();
    }
  } catch (err) {
    toast("Échec de l'analyse : " + esc(err.message), "err", 9000);
  } finally {
    setBusy(button, false);
  }
}

function initAnalyse() {
  $("#form-analyse").addEventListener("submit", runAnalyse);
  $("#btn-analyse").addEventListener("click", runAnalyse);
  $("#period").value = state.period;
  $("#interval").value = state.interval;

  /* ------------------------------------------------ bascule capture / cours */
  const modes = {
    image: {
      bouton: $("#dz-mode-image"),
      zone: $("#dropzone"),
      aide: "Une capture active la lecture d'image par IA et l'analyse croisée avec les données réelles.",
    },
    doc: {
      bouton: $("#dz-mode-doc"),
      zone: $("#dropzone-doc"),
      aide: "Le document est indexé puis l'analyse s'appuie dessus avec des citations [Source n].",
    },
  };

  const activerMode = (nom) => {
    Object.keys(modes).forEach((cle) => {
      const actif = cle === nom;
      modes[cle].bouton.className = "btn" + (actif ? " primary" : "");
      modes[cle].zone.style.display = actif ? "" : "none";
    });
    $("#dz-help").textContent = modes[nom].aide;
  };
  modes.image.bouton.addEventListener("click", () => activerMode("image"));
  modes.doc.bouton.addEventListener("click", () => activerMode("doc"));

  /* ------------------------------------------------------- capture d'image */
  const dropzone = $("#dropzone");
  const input = $("#image-input");

  const applyFile = (file) => {
    if (!file) return;
    if (file.size > (APP.maxUploadMb || 12) * 1024 * 1024) {
      toast("Fichier trop volumineux (limite " + (APP.maxUploadMb || 12) + " Mo).", "err");
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      state.imageDataUrl = String(reader.result || "");
      dropzone.innerHTML = '<b>Capture prête pour l\'analyse</b><div class="small">' + esc(file.name) +
        ' — cliquez pour remplacer</div><img src="' + state.imageDataUrl + '" alt="capture du graphique">';
    };
    reader.readAsDataURL(file);
  };

  dropzone.addEventListener("click", () => input.click());
  input.addEventListener("change", () => applyFile(input.files[0]));

  /* --------------------------------------------- cours : PDF / notes glissés */
  const dropzoneDoc = $("#dropzone-doc");
  const inputDoc = $("#doc-input");
  const EXTENSIONS = [".pdf", ".md", ".markdown", ".txt", ".text", ".rst"];

  const applyDocs = (fileList) => {
    const fichiers = Array.prototype.slice.call(fileList || []);
    if (!fichiers.length) return;
    const rejetes = fichiers.filter((f) => {
      const nom = (f.name || "").toLowerCase();
      return !EXTENSIONS.some((ext) => nom.endsWith(ext));
    });
    if (rejetes.length) {
      toast("Format non supporté : " + esc(rejetes.map((f) => f.name).join(", ")) +
        " (PDF, .md, .txt attendus).", "err", 8000);
    }
    const acceptes = fichiers.filter((f) => EXTENSIONS.some((ext) => (f.name || "").toLowerCase().endsWith(ext)));
    if (!acceptes.length) return;
    state.docFiles = acceptes;
    state.docUploaded = false;
    dropzoneDoc.innerHTML = '<b>' + acceptes.length + ' document(s) prêt(s)</b>' +
      '<div class="small">' + esc(acceptes.map((f) => f.name).join(', ')) +
      ' — cliquez pour remplacer</div>' +
      '<div class="small">Ils seront indexés automatiquement puis utilisés par l\'analyse.</div>' +
      '<input type="file" id="doc-input" multiple accept=".pdf,.md,.markdown,.txt,.text,.rst" style="display:none">';
    const nouvelInput = dropzoneDoc.querySelector("#doc-input");
    nouvelInput.addEventListener("change", () => applyDocs(nouvelInput.files));
    $("#doc-status").textContent = "Prêt à indexer : cliquez sur « Analyser et prédire ».";
  };

  dropzoneDoc.addEventListener("click", () => dropzoneDoc.querySelector("#doc-input").click());
  inputDoc.addEventListener("change", () => applyDocs(inputDoc.files));

  /* --------------------------------------------------------- glisser-déposer */
  [dropzone, dropzoneDoc].forEach((zone) => {
    ["dragenter", "dragover"].forEach((evt) => zone.addEventListener(evt, (e) => {
      e.preventDefault(); zone.classList.add("dragover");
    }));
    ["dragleave", "drop"].forEach((evt) => zone.addEventListener(evt, (e) => {
      e.preventDefault(); zone.classList.remove("dragover");
    }));
    zone.addEventListener("drop", (e) => {
      if (!e.dataTransfer || !e.dataTransfer.files.length) return;
      if (zone === dropzoneDoc) applyDocs(e.dataTransfer.files);
      else applyFile(e.dataTransfer.files[0]);
    });
  });

  $("#btn-demo").addEventListener("click", () => {
    $("#symbol").value = "AAPL";
    $("#question").value = "Analyse ce graphique, identifie la figure principale et donne-moi un plan avec stop et objectifs.";
    $("#period").value = "6mo";
    $("#interval").value = "1d";
    runAnalyse();
  });
}

/**
 * Indexe les documents joints à l'analyse (glisser-déposer depuis l'onglet Analyse).
 * Renvoie true si l'indexation a réussi (ou s'il n'y avait rien à indexer).
 */
async function uploadPendingDocuments() {
  if (!state.docFiles.length || state.docUploaded) return true;
  const statut = $("#doc-status");
  statut.innerHTML = '<span class="spinner"></span> Indexation de ' + state.docFiles.length + ' document(s)…';
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
    statut.innerHTML = '<span class="badge ok">' + ok.length + ' document(s) indexé(s) — ' +
      extraits + ' extraits ajoutés à votre base de connaissances</span>' +
      (ko.length ? ' <span class="badge warn">' + ko.length + ' en erreur</span>' : '');
    toast(ok.length + " document(s) indexé(s) (" + extraits + " extraits) : l'analyse va s'appuyer dessus.", "ok", 7000);
    return true;
  } catch (err) {
    statut.innerHTML = '<span class="badge err">Indexation impossible : ' + esc(err.message) + '</span>';
    toast("Indexation impossible : " + esc(err.message), "err", 9000);
    return false;
  }
}

/* ----------------------------------------------------------------- graphique */

function initChartControls() {
  $("#chart-limit").addEventListener("change", () => {
    state.limit = Number($("#chart-limit").value);
    loadChart();
  });
  $("#chart-refresh").addEventListener("click", () => loadChart(true));
  window.addEventListener("resize", () => drawChart());
}

async function loadChart(force) {
  const symbol = $("#chart-symbol").value.trim().toUpperCase() || state.symbol;
  $("#chart-status").textContent = "Chargement des données de " + symbol + "…";
  try {
    const payload = await api("/api/market/" + encodeURIComponent(symbol) +
      "?period=" + encodeURIComponent($("#chart-period").value) +
      "&interval=" + encodeURIComponent($("#chart-interval").value) +
      "&limit=" + state.limit + (force ? "&refresh=true" : ""));
    state.chart = payload;
    state.symbol = payload.instrument.symbol;
    $("#chart-symbol").value = payload.instrument.symbol;
    $("#chart-status").innerHTML = "<b>" + esc(payload.instrument.symbol) + "</b> " +
      esc(payload.instrument.name || "") + " — " + payload.candles.length + " bougies · source " +
      esc(payload.summary.source) + " · <span class=\"pill " + verdictClass(payload.summary.score) + "\">" +
      esc(payload.summary.label) + "</span> " + Number(payload.summary.score).toFixed(1) + "/100";
    drawChart();
  } catch (err) {
    $("#chart-status").innerHTML = '<span class="badge err">Données indisponibles</span> ' + esc(err.message);
    state.chart = null;
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

  if (!data || !data.candles || !data.candles.length) {
    ctx.fillStyle = "#6d7c99";
    ctx.font = "14px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("Aucune donnée à afficher — lancez une analyse de symbole.", width / 2, height / 2);
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
  candles.forEach((c) => { min = Math.min(min, c.l); max = Math.max(max, c.h); });
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

  // Fond + grille de prix
  ctx.fillStyle = "#0f1523";
  ctx.fillRect(margin.l, margin.t, plotW, plotH);
  ctx.strokeStyle = "#1b2438";
  ctx.lineWidth = 1;
  ctx.font = "11px " + (getComputedStyle(document.body).fontFamily || "sans-serif");
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  const gridLines = 6;
  for (let i = 0; i <= gridLines; i++) {
    const price = min + ((max - min) * i) / gridLines;
    const y = yOf(price);
    ctx.beginPath();
    ctx.moveTo(margin.l, y);
    ctx.lineTo(margin.l + plotW, y);
    ctx.stroke();
    ctx.fillStyle = "#6d7c99";
    ctx.fillText(fmt(price), margin.l + plotW + 6, y);
  }

  // Bornes des panneaux
  ctx.strokeStyle = "#22304a";
  ctx.strokeRect(margin.l, volTop, plotW, volH);
  ctx.strokeRect(margin.l, rsiTop, plotW, rsiH);

  const maxVolume = Math.max.apply(null, candles.map((c) => c.v || 0)) || 1;
  candles.forEach((c, i) => {
    const x = xOf(i);
    const up = c.c >= c.o;
    const color = up ? "#22c55e" : "#ef4444";
    // Volume
    const vh = ((c.v || 0) / maxVolume) * (volH - 4);
    ctx.fillStyle = up ? "rgba(34,197,94,0.55)" : "rgba(239,68,68,0.55)";
    ctx.fillRect(x - bodyW / 2, volTop + volH - vh - 2, bodyW, vh);
    // Mèches puis corps
    ctx.strokeStyle = color;
    ctx.beginPath();
    ctx.moveTo(x, yOf(c.h));
    ctx.lineTo(x, yOf(c.l));
    ctx.stroke();
    ctx.fillStyle = color;
    const yo = yOf(c.o), yc = yOf(c.c);
    const top = Math.min(yo, yc);
    const h = Math.max(1, Math.abs(yc - yo));
    ctx.fillRect(x - bodyW / 2, top, bodyW, h);
  });

  // Courbes (moyennes, Bollinger)
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
  drawSeries(overlays.bollinger_upper, "rgba(148,163,184,0.65)", [3, 3]);
  drawSeries(overlays.bollinger_lower, "rgba(148,163,184,0.65)", [3, 3]);
  drawSeries(overlays.sma200, "#f59e0b", [], 1.6);
  drawSeries(overlays.ema50, "#a855f7", [], 1.4);
  drawSeries(overlays.ema20, "#4f8cff", [], 1.5);

  // Supports / résistances
  const levels = data.levels || {};
  const drawLevel = (price, color, label) => {
    if (price === undefined || price === null || price < min || price > max) return;
    const y = yOf(price);
    ctx.strokeStyle = color;
    ctx.setLineDash([6, 4]);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(margin.l, y);
    ctx.lineTo(margin.l + plotW, y);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = color;
    ctx.font = "10px " + (getComputedStyle(document.body).fontFamily || "sans-serif");
    ctx.fillText(label + " " + fmt(price), margin.l + 6, y - 7);
  };
  (levels.resistances || []).slice(0, 3).forEach((lvl) => drawLevel(lvl.price, "rgba(239,68,68,0.75)", "R"));
  (levels.supports || []).slice(0, 3).forEach((lvl) => drawLevel(lvl.price, "rgba(34,197,94,0.75)", "S"));

  // Marqueurs de figures
  const offset = (data.summary && data.summary.offset) || 0;
  (data.summary && data.summary.patterns || []).forEach((p) => {
    const index = (p.index || 0) - offset;
    if (index < 0 || index >= candles.length) return;
    const candle = candles[index];
    const x = xOf(index);
    const bullish = p.bias === "haussier";
    const y = bullish ? yOf(candle.l) + 12 : yOf(candle.h) - 12;
    ctx.fillStyle = bullish ? "#22c55e" : p.bias === "baissier" ? "#ef4444" : "#f59e0b";
    ctx.beginPath();
    ctx.arc(x, y, 3.4, 0, Math.PI * 2);
    ctx.fill();
  });

  // RSI
  const rsiValues = data.rsi14 || [];
  if (rsiValues.length) {
    const yRsi = (value) => rsiTop + rsiH - (value / 100) * rsiH;
    [30, 50, 70].forEach((value) => {
      ctx.strokeStyle = value === 50 ? "#1b2438" : "rgba(79,140,255,0.25)";
      ctx.setLineDash(value === 50 ? [] : [4, 4]);
      ctx.beginPath();
      ctx.moveTo(margin.l, yRsi(value));
      ctx.lineTo(margin.l + plotW, yRsi(value));
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = "#6d7c99";
      ctx.font = "10px sans-serif";
      ctx.fillText(String(value), margin.l + plotW + 6, yRsi(value));
    });
    ctx.strokeStyle = "#e2b8ff";
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    let startedRsi = false;
    rsiValues.forEach((v, i) => {
      if (v === null || v === undefined) { startedRsi = false; return; }
      const x = xOf(i), y = yRsi(v);
      if (!startedRsi) { ctx.moveTo(x, y); startedRsi = true; } else { ctx.lineTo(x, y); }
    });
    ctx.stroke();
    ctx.fillStyle = "#9aa8c4";
    ctx.font = "11px sans-serif";
    ctx.fillText("RSI(14)", margin.l + 6, rsiTop + 12);
  }

  // Étiquettes de dates
  ctx.fillStyle = "#6d7c99";
  ctx.font = "10.5px sans-serif";
  ctx.textAlign = "center";
  const ticks = Math.min(8, candles.length);
  for (let i = 0; i < ticks; i++) {
    const index = Math.floor((i * (candles.length - 1)) / Math.max(1, ticks - 1));
    ctx.fillText(candles[index].date, xOf(index), height - 6);
  }
  ctx.textAlign = "left";

  // Surlignage du dernier prix
  const last = candles[candles.length - 1];
  ctx.strokeStyle = "rgba(232,238,252,0.35)";
  ctx.setLineDash([2, 3]);
  ctx.beginPath();
  ctx.moveTo(margin.l, yOf(last.c));
  ctx.lineTo(margin.l + plotW, yOf(last.c));
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = "#e8eefc";
  ctx.fillRect(margin.l + plotW + 2, yOf(last.c) - 8, margin.r - 6, 16);
  ctx.fillStyle = "#0b0f17";
  ctx.textBaseline = "middle";
  ctx.fillText(fmt(last.c), margin.l + plotW + 6, yOf(last.c));

  // Mémorise la géométrie pour le crosshair
  canvas._geom = { margin, plotW, plotH, priceH, volTop, rsiTop, rsiH, step, min, max, candles, xOf, yOf };

  // Légende
  $("#chart-legend").innerHTML =
    '<span><i style="background:#4f8cff"></i>EMA 20</span>' +
    '<span><i style="background:#a855f7"></i>EMA 50</span>' +
    '<span><i style="background:#f59e0b"></i>SMA 200</span>' +
    '<span><i style="background:#94a3b8"></i>Bollinger (20, 2σ)</span>' +
    '<span><i style="background:#22c55e"></i>Supports</span>' +
    '<span><i style="background:#ef4444"></i>Résistances</span>';
}

function initCrosshair() {
  const canvas = $("#chart");
  const tooltip = $("#tooltip");
  canvas.addEventListener("mousemove", (event) => {
    const geom = canvas._geom;
    if (!geom) return;
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const index = Math.floor((x - geom.margin.l) / geom.step);
    if (index < 0 || index >= geom.candles.length) { tooltip.style.display = "none"; return; }
    const candle = geom.candles[index];
    const rsi = (state.chart.rsi14 || [])[index];
    const overlay = state.chart.overlays || {};
    const line = (label, value) => label + " " + (value === null || value === undefined ? "—" : fmt(value));
    tooltip.innerHTML = [
      candle.date,
      "O " + fmt(candle.o) + "   H " + fmt(candle.h),
      "B " + fmt(candle.l) + "   C " + fmt(candle.c),
      "Volume " + Number(candle.v || 0).toLocaleString("fr-FR", { maximumFractionDigits: 0 }),
      line("EMA20", (overlay.ema20 || [])[index]),
      line("EMA50", (overlay.ema50 || [])[index]),
      line("SMA200", (overlay.sma200 || [])[index]),
      line("RSI14", rsi),
    ].join("\n");
    tooltip.style.display = "block";
    const tipWidth = tooltip.offsetWidth || 190;
    tooltip.style.left = Math.min(rect.width - tipWidth - 12, Math.max(8, x + 14)) + "px";
    tooltip.style.top = Math.max(8, event.clientY - rect.top - 60) + "px";

    drawChart();
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.strokeStyle = "rgba(232,238,252,0.28)";
    ctx.setLineDash([3, 3]);
    const cx = geom.xOf(index);
    ctx.beginPath();
    ctx.moveTo(cx, geom.margin.t);
    ctx.lineTo(cx, geom.margin.t + geom.plotH);
    ctx.stroke();
    ctx.setLineDash([]);
  });
  canvas.addEventListener("mouseleave", () => { $("#tooltip").style.display = "none"; });
}

/* ---------------------------------------------------------------------- chat */

function initChat() {
  const form = $("#form-chat");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const question = $("#chat-question").value.trim();
    if (!question) return;
    const button = $("#btn-chat");
    const history = $("#chat-history").innerHTML;
    setBusy(button, true, "Recherche…");
    try {
      const payload = await api("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: question, top_k: Number($("#chat-topk").value || 5) }),
      });
      $("#chat-history").innerHTML = history +
        '<div class="source" style="margin-bottom:12px"><div class="head"><b>Vous</b></div><pre>' +
        esc(question) + '</pre></div>' +
        '<div class="card" style="margin-bottom:14px;background:var(--bg-input)">' +
        '<div class="md">' + mdToHtml(payload.answer) + '</div>' +
        '<p class="small mt">' + (payload.mode === "ia"
          ? "Réponse rédigée par " + esc(payload.provider) + " " + esc(payload.model)
          : "Mode démo : extraits classés (ajoutez une clé LLM pour une réponse rédigée)") +
        ' · recherche ' + esc(payload.rag_mode) + '</p></div>';
      $("#chat-question").value = "";
      $("#chat-history").scrollIntoView({ behavior: "smooth", block: "end" });
    } catch (err) {
      toast("Erreur du chat : " + esc(err.message), "err");
    } finally {
      setBusy(button, false);
    }
  });
}

/* ------------------------------------------------------- base de connaissances */

async function loadDocuments() {
  const target = $("#docs-table");
  try {
    const payload = await api("/api/knowledge");
    const stats = payload.statistiques || {};
    $("#kb-stats").innerHTML =
      '<span class="badge info">' + (stats.documents || 0) + ' documents</span>' +
      '<span class="badge info">' + (stats.chunks || 0) + ' extraits indexés</span>' +
      '<span class="badge">' + esc(stats.embedding_provider || "—") + '</span>' +
      '<span class="badge">base ' + esc(stats.backend || "sqlite") + '</span>';

    if (!payload.documents.length) {
      target.innerHTML = '<div class="empty">Aucun document. Importez vos cours (PDF, Markdown, texte) pour enrichir l\'analyse.</div>';
      return;
    }
    target.innerHTML = '<table class="data"><thead><tr><th>Document</th><th>Type</th><th>Extraits</th>' +
      '<th>Ajouté le</th><th></th></tr></thead><tbody>' +
      payload.documents.map((doc) =>
        '<tr><td><b>' + esc(doc.title) + '</b><div class="small">' + esc(doc.source) + '</div></td>' +
        '<td>' + esc(doc.kind) + '</td><td class="mono">' + doc.chunk_count + '</td>' +
        '<td class="small">' + esc((doc.created_at || "").replace("T", " ").slice(0, 16)) + '</td>' +
        '<td class="nowrap"><a class="btn ghost" href="' + apiUrl("/api/knowledge/" + doc.doc_id + "/export") + '" target="_blank">JSON</a> ' +
        '<button class="btn ghost danger" data-doc="' + doc.doc_id + '">Supprimer</button></td></tr>').join("") +
      '</tbody></table>';

    $$("#docs-table button[data-doc]").forEach((button) => {
      button.addEventListener("click", async () => {
        if (!confirm("Supprimer ce document de l'index ?")) return;
        try {
          await api("/api/knowledge/" + button.dataset.doc, { method: "DELETE" });
          toast("Document supprimé.", "ok");
          loadDocuments();
        } catch (err) { toast("Suppression impossible : " + esc(err.message), "err"); }
      });
    });
  } catch (err) {
    target.innerHTML = '<div class="empty">Impossible de charger la base : ' + esc(err.message) + '</div>';
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
    setBusy(button, true, "Recherche…");
    try {
      const payload = await api("/api/knowledge/search?q=" + encodeURIComponent(query) + "&top_k=5");
      renderSources(payload.resultats);
      toast("Recherche " + payload.mode + " : " + payload.resultats.length + " résultat(s).", "ok");
    } catch (err) {
      toast("Recherche impossible : " + esc(err.message), "err");
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

/* ----------------------------------------------------------------- historique */

async function loadReports() {
  const target = $("#reports-table");
  try {
    const payload = await api("/api/reports?limit=30");
    if (!payload.analyses.length) {
      target.innerHTML = '<div class="empty">Aucune analyse enregistrée pour l\'instant.</div>';
      return;
    }
    target.innerHTML = '<table class="data"><thead><tr><th>Date</th><th>Marché</th><th>Conclusion</th>' +
      '<th>Score</th><th>Mode</th><th></th></tr></thead><tbody>' +
      payload.analyses.map((r) =>
        '<tr><td class="small nowrap">' + esc((r.created_at || "").replace("T", " ").slice(0, 16)) + '</td>' +
        '<td><b>' + esc(r.symbol || "image") + '</b><div class="small">' + esc(r.timeframe || "") + '</div></td>' +
        '<td><span class="pill ' + verdictClass(r.score) + '">' + esc(r.label || "—") + '</span></td>' +
        '<td class="mono">' + Number(r.score || 0).toFixed(1) + '</td>' +
        '<td class="small">' + esc(r.mode) + '</td>' +
        '<td class="nowrap"><button class="btn ghost" data-view="' + r.report_id + '">Ouvrir</button> ' +
        '<a class="btn ghost" href="' + apiUrl("/api/reports/" + r.report_id + "/export") + '" target="_blank">Markdown</a> ' +
        '<button class="btn ghost danger" data-del="' + r.report_id + '">Supprimer</button></td></tr>').join("") +
      '</tbody></table>';

    $$("#reports-table button[data-view]").forEach((button) => {
      button.addEventListener("click", () => openReport(button.dataset.view));
    });
    $$("#reports-table button[data-del]").forEach((button) => {
      button.addEventListener("click", async () => {
        if (!confirm("Supprimer cette analyse ?")) return;
        try {
          await api("/api/reports/" + button.dataset.del, { method: "DELETE" });
          toast("Analyse supprimée.", "ok");
          loadReports();
        } catch (err) { toast("Suppression impossible : " + esc(err.message), "err"); }
      });
    });
  } catch (err) {
    target.innerHTML = '<div class="empty">Impossible de charger l\'historique : ' + esc(err.message) + '</div>';
  }
}

async function openReport(reportId) {
  try {
    const report = await api("/api/reports/" + reportId);
    renderAnalysis({
      answer: report.answer,
      mode: report.mode,
      provider: report.provider,
      model: report.model,
      analysis: report.analysis && report.analysis.instrument ? report.analysis : null,
      observation: report.observation,
      sources: report.sources || [],
      rag_mode: "—",
      warnings: report.warnings || [],
      report_id: report.report_id,
    });
    // Revenir à l'onglet d'analyse pour lire le document
    const tab = $('nav.tabs button[data-tab="tab-analyse"]');
    if (tab) tab.click();
    $("#result-card").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    toast("Ouverture impossible : " + esc(err.message), "err");
  }
}


/* ---------------------------------------------------------- notifications */

function renderNotifyStatus(payload) {
  const notif = (payload && payload.notifications) || {};
  const veille = (payload && payload.veille) || {};
  const badges = [];
  badges.push(notif.pret
    ? '<span class="badge ok">Canaux : ' + esc(notif.canaux) + '</span>'
    : '<span class="badge warn">Aucun canal configuré</span>');
  badges.push(veille.actif
    ? '<span class="badge ok">Veille active (toutes les ' + esc(veille.intervalle_minutes || "?") + ' min)</span>'
    : '<span class="badge">Veille inactive</span>');
  if (notif.watchlist && notif.watchlist.length) {
    badges.push('<span class="badge info">' + notif.watchlist.length + ' marché(s) suivi(s)</span>');
  }
  $("#notif-status").innerHTML = badges.join(" ");

  if (!notif.pret) {
    $("#veille-etat").innerHTML = '<div class="banner">⚙️ ' + esc(notif.aide || "") + '</div>';
  } else if (veille.actif) {
    $("#veille-etat").innerHTML = '<div class="source"><div class="head"><b>Derniers passages</b>' +
      '<span>' + esc(veille.passages || 0) + ' passage(s)</span></div><pre>' + esc(JSON.stringify({
        démarrage: veille.demarre_le || "—",
        dernier_passage: veille.dernier_passage || "—",
        dernier_resultat: veille.dernier_resultat || {},
        derniere_erreur: veille.derniere_erreur || "",
        canaux: veille.canaux || "",
        watchlist: veille.watchlist || [],
      }, null, 2)) + '</pre></div>';
  } else {
    $("#veille-etat").innerHTML = '<div class="empty">Veille inactive' +
      (veille.raison ? " — " + esc(veille.raison) : "") + '</div>';
  }
  $("#notif-hint").textContent = notif.pret
    ? "Envoi via " + notif.canaux
    : "Configurez Telegram ou un webhook dans l'onglet « Alertes & veille ».";
}

async function loadNotifyStatus() {
  try {
    renderNotifyStatus(await api("/api/notifications"));
  } catch (err) {
    $("#notif-status").innerHTML = '<span class="badge err">État indisponible : ' + esc(err.message) + '</span>';
  }
}

function initNotifications() {
  $("#btn-notif-test").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    setBusy(button, true, "Envoi…");
    try {
      const payload = await api("/api/notifications/test", { method: "POST" });
      const envoye = payload.resultats.some((r) => r.statut === "envoye");
      toast(envoye ? "Message de test envoyé (" + esc(payload.canaux) + ")."
                   : "Envoi impossible : " + esc(payload.resultats.map((r) => r.detail).join(" ; ")),
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
      toast(payload.demarree ? "Veille démarrée." : "Veille non démarrée : " + esc((payload.notifications || {}).aide || ""),
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
    if (!payload || !payload.report_id) {
      toast("Lancez d'abord une analyse.", "warn");
      return;
    }
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

/* -------------------------------------------------------------------- santé */

async function loadHealth() {
  try {
    const payload = await api("/api/health");
    const badges = [];
    const llm = payload.llm || {};
    badges.push(llm.mode_demo
      ? '<span class="badge warn">Mode démo (sans clé LLM)</span>'
      : '<span class="badge ok">IA ' + esc(llm.provider_actif) + '</span>');
    if (llm.vision_disponible) badges.push('<span class="badge ok">Vision active</span>');
    const marche = (payload.marche || {}).provider_actif;
    badges.push('<span class="badge ' + (marche === "demo" ? "warn" : "ok") + '">Données ' + esc(marche) + '</span>');
    const kb = payload.connaissances || {};
    badges.push('<span class="badge info">' + (kb.chunks || 0) + ' extraits de cours</span>');
    const notif = payload.notifications || {};
    if (notif.pret) {
      badges.push('<span class="badge ' + ((payload.veille || {}).actif ? "ok" : "") + '">' +
        ((payload.veille || {}).actif ? "Veille active" : "Alertes prêtes") + '</span>');
    }
    if (notif.veille_active) badges.push('<span class="badge ok">Veille automatique</span>');
    $("#badges").innerHTML = badges.join(" ");
  } catch (err) {
    $("#badges").innerHTML = '<span class="badge err">Diagnostic indisponible</span>';
  }
}

/* Les fonctions utilisées par des attributs onclick inline doivent être globales */
window.loadReports = loadReports;

/* -------------------------------------------------------------------- init */

document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  initAnalyse();
  initChartControls();
  initCrosshair();
  initChat();
  initKnowledge();
  initNotifications();
  loadHealth();
  $("#chart-period").value = state.period;
  $("#chart-interval").value = state.interval;
  $("#chart-symbol").value = state.symbol;
  loadChart();
  if (APP.showKbHint) loadDocuments();
});
