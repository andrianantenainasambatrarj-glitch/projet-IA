/* =====================================================================
   Vérification automatique de l'interface (sans navigateur)

   Ce script charge la page réelle servie par l'application, exécute son
   JavaScript (jsdom) en pointant sur la vraie API, puis vérifie que les
   éléments clés se remplissent et qu'aucune erreur JavaScript ne survient.

   Utilisation (Node 18+) :
       npm install jsdom        # une seule fois, dans le dossier de votre choix
       node deploy/outils/verifier_interface.mjs http://127.0.0.1:8000

   Sortie : liste des contrôles OK / ÉCHEC, erreurs JavaScript éventuelles,
   code de sortie 0 si tout est bon (utilisable dans une CI).
   ===================================================================== */
import { JSDOM, VirtualConsole } from "jsdom";

const BASE = (process.argv[2] || "http://127.0.0.1:8000").replace(/\/$/, "");

const erreurs = [];
const controles = [];
const verifier = (nom, condition, detail = "") => controles.push({ nom, ok: !!condition, detail: String(detail || "").slice(0, 120) });
const attendre = (ms) => new Promise((r) => setTimeout(r, ms));

const virtualConsole = new VirtualConsole();
virtualConsole.on("jsdomError", (e) => erreurs.push("jsdomError: " + e.message));
virtualConsole.on("error", (...a) => erreurs.push("console.error: " + a.join(" ")));

/* ------------------------------------------------- page et scripts */
const reponse = await fetch(BASE + "/");
if (!reponse.ok) {
  console.error("Page inaccessible sur " + BASE + " (l'application est-elle démarrée ?)");
  process.exit(2);
}
const page = await reponse.text();
if (!/static\/app\.js/.test(page)) {
  console.error("Le JavaScript de l'interface est introuvable dans la page.");
  process.exit(2);
}

/* --------------------------------------------------------------- jsdom */
const dom = new JSDOM(page, {
  url: BASE + "/",
  runScripts: "dangerously",
  resources: "usable",   // jsdom charge /static/app.js depuis le serveur
  pretendToBeVisual: true,
  virtualConsole,
  beforeParse(window) {
    const ctx = new Proxy({}, {
      get(target, prop) {
        if (prop === "canvas") return null;
        if (!(prop in target)) target[prop] = () => {};
        return target[prop];
      },
      set(target, prop, value) { target[prop] = value; return true; },
    });
    window.HTMLCanvasElement.prototype.getContext = () => ctx;
    Object.defineProperty(window.HTMLElement.prototype, "clientWidth", { get: () => 900 });
    Object.defineProperty(window.HTMLElement.prototype, "clientHeight", { get: () => 560 });
    window.Element.prototype.scrollIntoView = function () {};
    window.requestAnimationFrame = (cb) => setTimeout(cb, 0);
    // Les appels de l'interface passent par le vrai serveur
    window.fetch = (chemin, options) => fetch(new URL(String(chemin), BASE + "/"), options);
  },
});

const { window } = dom;
const doc = window.document;
const texte = (sel) => {
  const el = doc.querySelector(sel);
  return el ? el.textContent.replace(/\s+/g, " ").trim() : "";
};
const cliquer = (sel) => {
  const el = doc.querySelector(sel);
  if (el) el.click();
  return !!el;
};
const soumettre = (sel) => {
  const form = doc.querySelector(sel);
  if (form) form.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  return !!form;
};

/* ------------------------------------------------------- contrôles */
await new Promise((resoudre) => {
  if (dom.window.document.readyState === "complete") return resoudre();
  dom.window.addEventListener("load", resoudre, { once: true });
  setTimeout(resoudre, 5000);
});
await attendre(1200);  // chargement : diagnostic + graphique

verifier("page servie avec ses onglets", doc.querySelectorAll("nav.tabs button").length === 7);
verifier("badges d'état remplis", texte("#badges").length > 10, texte("#badges"));
verifier("graphique alimenté (statut)", /bougies/.test(texte("#chart-status")) || /indisponibles/.test(texte("#chart-status")), texte("#chart-status"));

// Capture d'image : dépôt d'un vrai fichier PNG (lecture par FileReader)
const png = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
  "base64"
);
const fichier = new window.File([new Uint8Array(png)], "capture-eurusd.png", { type: "image/png" });
const champImage = doc.querySelector("#image-input");
Object.defineProperty(champImage, "files", { value: { 0: fichier, length: 1 }, configurable: true });
champImage.dispatchEvent(new window.Event("change", { bubbles: true }));
await attendre(500);
verifier("capture déposée et lue par l'interface", /Capture prête/.test(texte("#dropzone")), texte("#dropzone"));

// Analyse (parcours principal)
doc.querySelector("#symbol").value = "AAPL";
cliquer("#btn-analyse");
await attendre(2500);
verifier("traçabilité de la capture affichée", /Capture reçue/.test(texte("#vision")), texte("#vision"));
verifier("bloc vision renseigné (état explicite)",
  /Capture reçue|Aucune capture/.test(texte("#vision")), texte("#vision"));
verifier("verdict affiché", texte("#verdict").length > 20, texte("#verdict"));
verifier("conclusion rendue", texte("#answer").length > 50, texte("#answer"));
verifier("facteurs du score expliqués", texte("#factors").length > 20, texte("#factors"));
verifier("sources de cours affichées", texte("#sources").length > 10, texte("#sources"));
verifier("bouton d'analyse réutilisable",
  /Analyser et prédire/.test(texte("#btn-analyse")) && !doc.querySelector("#btn-analyse").disabled,
  texte("#btn-analyse"));

// Connaissances
cliquer('nav.tabs button[data-tab="tab-connaissances"]');
await attendre(700);
verifier("documents indexés listés", texte("#docs-table").length > 10, texte("#docs-table"));
verifier("statistiques de la base", texte("#kb-stats").length > 10, texte("#kb-stats"));
doc.querySelector("#search-query").value = "divergence RSI";
soumettre("#form-search");
await attendre(900);
verifier("recherche documentaire dans l'onglet", /Source 1|extrait|Aucun extrait/.test(texte("#kb-results")), texte("#kb-results"));

// Historique
cliquer('nav.tabs button[data-tab="tab-historique"]');
await attendre(700);
verifier("historique listé", /Ouvrir|Aucune analyse/.test(texte("#reports-table")), texte("#reports-table"));

// Chat
cliquer('nav.tabs button[data-tab="tab-chat"]');
doc.querySelector("#chat-question").value = "Qu'est-ce qu'une épaule-tête-épaules ?";
soumettre("#form-chat");
await attendre(1500);
verifier("réponse du chat affichée", /Assistant/.test(texte("#chat-history")), texte("#chat-history"));

// Notifications
cliquer('nav.tabs button[data-tab="tab-notifications"]');
await attendre(700);
verifier("état des alertes affiché", texte("#notif-status").length > 10, texte("#notif-status"));
verifier("état de la veille affiché", texte("#veille-etat").length > 10, texte("#veille-etat"));

// Navigation et thème
verifier("onglet actif correctement marqué",
  doc.querySelector('nav.tabs button[aria-selected="true"]').dataset.tab === "tab-notifications",
  doc.querySelector('nav.tabs button[aria-selected="true"]').dataset.tab);
cliquer('nav.tabs button[data-tab="tab-analyse"]');
await attendre(150);
verifier("retour à l'onglet Analyse", !doc.querySelector("#tab-analyse").hidden && doc.querySelector("#tab-graphique").hidden);
const themeAvant = doc.documentElement.dataset.theme;
cliquer("#btn-theme");
verifier("thème commutable", doc.documentElement.dataset.theme !== themeAvant,
  themeAvant + " → " + doc.documentElement.dataset.theme);
verifier("aucun identifiant manquant (contrôles présents)", !!doc.querySelector("#result-card") && !!doc.querySelector("#toasts"));

/* ------------------------------------------------------------ rapport */
let echecs = 0;
console.log("\n=== CONTRÔLES D'INTERFACE (" + BASE + ") ===");
for (const c of controles) {
  if (!c.ok) echecs++;
  console.log((c.ok ? "  OK   " : "  ÉCHEC") + "  " + c.nom + (c.ok || !c.detail ? "" : "  → " + c.detail));
}
console.log(`\n${controles.length - echecs}/${controles.length} contrôles réussis`);
if (erreurs.length) {
  console.log("\n=== ERREURS JAVASCRIPT ===");
  [...new Set(erreurs)].slice(0, 20).forEach((e) => console.log("  - " + e));
} else {
  console.log("Aucune erreur JavaScript détectée.");
}
process.exit(echecs || erreurs.length ? 1 : 0);
