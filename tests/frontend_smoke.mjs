// Prueba de humo del frontend con jsdom contra la API real (Iteración 1: auth).
// Requisitos:
//   1) backend corriendo en http://127.0.0.1:8000 en modo test:
//        KIWI_AUTH_MODE=test uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
//   2) npm install (jsdom)
// Uso: node tests/frontend_smoke.mjs
import { JSDOM } from "jsdom";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const JS = path.resolve(__dirname, "..", "frontend", "js");
const API = "http://127.0.0.1:8000";

const dom = new JSDOM(`<!DOCTYPE html><html><body>
  <header><nav id="mainnav"><a data-nav="dashboard"></a><a data-nav="course"></a><a data-nav="historial"></a></nav></header>
  <main id="app"></main>
  <div id="toast-container"></div>
  <div id="session-chip"></div>
</body></html>`, { url: "http://localhost/", pretendToBeVisual: true });

const realFetch = globalThis.fetch;
const seenHeaders = [];   // evidencia de cabeceras enviadas por api.js
let configOverride = null; // fase C2: simula config de modo supabase
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.navigator = dom.window.navigator;
globalThis.localStorage = dom.window.localStorage;
globalThis.location = dom.window.location;
globalThis.fetch = (url, opts) => {
  if (configOverride && String(url).includes("/api/v1/config")) {
    return Promise.resolve(new Response(JSON.stringify(configOverride), {
      status: 200, headers: { "Content-Type": "application/json" },
    }));
  }
  seenHeaders.push({ url: String(url), headers: { ...(opts?.headers || {}) } });
  return realFetch(new URL(url, API), opts);
};
globalThis.confirm = () => true;
dom.window.confirm = () => true;
dom.window.scrollTo = () => {};
dom.window.HTMLElement.prototype.scrollIntoView = () => {};

const sleep = (ms) => new Promise(r => setTimeout(r, ms));
let failures = 0;
function check(name, cond) {
  console.log(`${cond ? "✅" : "❌"} ${name}`);
  if (!cond) failures++;
}

const root = document.getElementById("app");
const click = (node) => node?.dispatchEvent(new dom.window.Event("click", { bubbles: true }));

// usuario único por corrida (namespace dev-* obligatorio en modos local/test)
const SMOKE_USER = `dev-smoke-${Date.now()}`;

// ------------------------------------------------------------------ FASE A: invitado (sin sesión)
const { renderWelcome, renderDashboard } = await import(`${JS}/views/dashboard.js`);
const { renderCourse } = await import(`${JS}/views/course.js`);
const { renderLesson } = await import(`${JS}/views/lesson.js`);
const { renderExam } = await import(`${JS}/views/exam.js`);
const { renderHistorial } = await import(`${JS}/views/historial.js`);
const { renderAuth } = await import(`${JS}/views/auth.js`);
const auth = await import(`${JS}/auth.js`);

await renderWelcome(root);
check("Invitado: bienvenida lista cursos públicos", root.textContent.includes("Kiwi Academy") && root.querySelectorAll(".course-card").length >= 1);

await renderCourse(root, "soc-n1");
check("Invitado: curso visible con banner de modo lectura", root.querySelectorAll(".phase-block").length === 4 && !!root.querySelector(".guest-banner"));
check("Invitado: examen ofrece iniciar sesión", [...root.querySelectorAll("a.btn")].some(a => a.textContent.includes("Inicia sesión")));

await renderLesson(root, "soc-n1", "01-fundamentos", "01-modelo-osi-protocolos");
check("Invitado: teoría pública legible", root.querySelectorAll(".md table").length >= 1);
const exTab = [...root.querySelectorAll(".tabs button")].find(b => b.textContent.includes("Ejercicios"));
click(exTab);
await sleep(150);
check("Invitado: ejercicios muestran banner de sesión", !!root.querySelector(".guest-banner"));
const guestCheckBtn = [...root.querySelectorAll(".ex-actions .btn.primary")][0];
check("Invitado: botón Comprobar deshabilitado", guestCheckBtn?.disabled === true);

// lección draft protegida también desde el frontend (404 del backend)
await renderLesson(root, "soc-n1", "02-deteccion", "01-lectura-logs");
check("Invitado: lección draft -> 404 del backend", root.textContent.includes("no encontrada") || root.textContent.includes("No encontrada") || root.textContent.includes("404"));

await renderExam(root, "soc-n1", "fase-1-examen");
check("Invitado: examen exige sesión para comenzar", [...root.querySelectorAll("a.btn")].some(a => a.textContent.includes("Inicia sesión para presentar")));

await renderHistorial(root);
check("Invitado: historial exige sesión", root.textContent.includes("El historial es personal"));

await renderAuth(root);
check("Modo test: vista dev con emisor", root.textContent.includes("Modo desarrollo") && !!root.querySelector("input.txt"));

// backend: endpoint privado sin token -> 401 (evidencia directa)
const noAuth = await realFetch(`${API}/api/v1/progress`);
check("API: /progress sin token -> 401", noAuth.status === 401);

// api.js NO envía X-User-Id nunca
check("Frontend: ninguna petición envió X-User-Id",
  seenHeaders.every(h => !Object.keys(h.headers).some(k => k.toLowerCase() === "x-user-id")));

// ------------------------------------------------------------------ FASE B: autenticado (token dev del modo test)
await auth.devSignIn(SMOKE_USER);
check("devSignIn obtiene token JWT", (await auth.getToken())?.split(".").length === 3);

// todas las peticiones autenticadas llevan Authorization
seenHeaders.length = 0;
await renderDashboard(root);
check("Auth: dashboard personal renderiza", root.querySelectorAll(".stat").length >= 4 && root.textContent.includes("Panel de control"));
check("API: peticiones privadas llevan Authorization Bearer",
  seenHeaders.some(h => String(h.headers["Authorization"] || "").startsWith("Bearer ")));
check("API: seguimos sin enviar X-User-Id autenticados",
  seenHeaders.every(h => !Object.keys(h.headers).some(k => k.toLowerCase() === "x-user-id")));

await renderCourse(root, "soc-n1");
check("Auth: curso sin banner de invitado", !root.querySelector(".guest-banner") && root.querySelectorAll(".phase-block").length === 4);

await renderLesson(root, "soc-n1", "01-fundamentos", "01-modelo-osi-protocolos");
const markBtn = [...root.querySelectorAll("button")].find(b => b.textContent.includes("Marcar teoría como leída"));
check("Auth: botón marcar teoría habilitado", !!markBtn && markBtn.disabled === false);
click(markBtn);
await sleep(400);

const exTab2 = [...root.querySelectorAll(".tabs button")].find(b => b.textContent.includes("Ejercicios"));
click(exTab2);
await sleep(150);
check("Auth: 8 tarjetas de ejercicio", root.querySelectorAll(".ex-card").length === 8);

const card1 = root.querySelector("#ex-l1-e1");
const opt = [...card1.querySelectorAll(".opt")].find(o => o.textContent.includes("Capa 4"));
click(opt);
const checkBtn = [...card1.querySelectorAll("button")].find(b => b.textContent === "Comprobar");
click(checkBtn);
await sleep(600);
check("Auth: ejercicio correcto -> feedback positivo", !!card1.querySelector(".fb.ok"));
check("Auth: ejercicio dominado", card1.classList.contains("mastered"));

await renderExam(root, "soc-n1", "fase-1-examen");
const startBtn = [...root.querySelectorAll("button")].find(b => b.textContent.includes("Comenzar examen"));
check("Auth: examen permite comenzar", !!startBtn);
click(startBtn);
await sleep(300);
check("Auth: shell con timer y paleta 15", !!root.querySelector(".timer") && root.querySelectorAll(".palette-grid button").length === 15);
click(root.querySelector(".exam-qcard .opt"));
await sleep(100);
check("Auth: paleta marca respondida", !!root.querySelector(".palette-grid button.answered"));
const submitBtn = [...root.querySelectorAll("button")].find(b => b.textContent.includes("Entregar examen"));
click(submitBtn);
await sleep(1500);
check("Auth: resultados con anillo y revisión 15", !!root.querySelector(".score-ring") && root.querySelectorAll(".review-item").length === 15);
check("Auth: exportadores de reporte", [...root.querySelectorAll("button")].some(b => b.textContent.includes("Copiar reporte")));

await renderHistorial(root);
check("Auth: historial lista intentos", root.querySelectorAll(".hist-table tbody tr").length >= 2);

// cierre de sesión limpia el token
await auth.signOut();
check("signOut limpia token", (await auth.getToken()) === null);

// ------------------------------------------------------------------ FASE C: H-01 sesión inválida/irrecuperable
// C1 — modo local/test: token corrupto -> 401 -> limpieza completa + redirect
await auth.devSignIn(SMOKE_USER);
auth._setDevTokenForTests("token.invalido.dev");
dom.window.location.hash = "#/";
let c1Error = null;
try {
  const { api } = await import(`${JS}/api.js`);
  await api.dashboard();
} catch (e) { c1Error = e; }
check("C1: petición con token inválido -> error 401", c1Error?.status === 401);
check("C1: sesión dev limpiada (memoria)", (await auth.getToken()) === null);
check("C1: sesión dev limpiada (localStorage)", dom.window.localStorage.getItem("kiwi.dev.token") === null);
check("C1: redirigido a #/auth", dom.window.location.hash === "#/auth");
check("C1: getCurrentUser ya no muestra sesión activa", (await auth.getCurrentUser()) === null);

// C2 — modo supabase (cliente simulado): sesión muerta -> signOut local + UI coherente
let signOutCalls = [];
let fakeSession = { access_token: "sb-token-muerto", user: { id: "u-fake-1", email: "persona@example.com", is_anonymous: false } };
const fakeSbClient = {
  auth: {
    getSession: async () => ({ data: { session: fakeSession } }),
    signOut: async (opts) => { signOutCalls.push(opts || {}); fakeSession = null; },
    onAuthStateChange: () => ({ data: { subscription: { unsubscribe() {} } } }),
  },
};
dom.window.supabase = { createClient: () => fakeSbClient };
configOverride = { auth_mode: "supabase", supabase: { url: "https://fakeref.supabase.co", anon_key: "anon-publica" } };
await auth.loadAuthConfig(true);
const guest = await auth.getCurrentUser();
check("C2: sesión supabase se muestra activa antes del fallo", guest?.label === "persona@example.com");

let c2Error = null;
try {
  const { api } = await import(`${JS}/api.js`);
  await api.dashboard();   // el server (modo test) rechazará "sb-token-muerto" -> 401
} catch (e) { c2Error = e; }
check("C2: backend rechaza el token supabase inválido (401)", c2Error?.status === 401);
check("C2: clearInvalidSession hizo signOut scope=local", signOutCalls.length === 1 && signOutCalls[0].scope === "local");
check("C2: getCurrentUser ya no muestra la sesión muerta", (await auth.getCurrentUser()) === null);

await renderAuth(root);
check("C2: la UI de auth NO la muestra como activa", !root.textContent.includes("Sesión activa") && root.textContent.includes("Iniciar sesión"));

console.log(failures === 0 ? "\n🎉 FRONTEND OK — todas las verificaciones pasaron" : `\n💥 ${failures} verificaciones fallaron`);
process.exit(failures === 0 ? 0 : 1);
