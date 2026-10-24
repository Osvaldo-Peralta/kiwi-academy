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

// ── Iteración 2: examen con respuesta libre contestada -> veredicto PENDIENTE
await renderExam(root, "soc-n1", "fase-1-examen");
click([...root.querySelectorAll("button")].find(b => b.textContent.includes("Comenzar examen")));
await sleep(400);
const palBtns = root.querySelectorAll(".palette-grid button");
click(palBtns[14]);                                   // pregunta 15 = respuesta libre
await sleep(100);
const ta = root.querySelector(".exam-qcard textarea");
check("Iter2: pregunta libre accesible desde la paleta", !!ta);
ta.value = "Brute-force seguido de C2 en 4444: aislar el host, bloquear las IPs, escalar como incidente y preservar evidencia.";
ta.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
const submit2 = [...root.querySelectorAll("button")].find(b => b.textContent.includes("Entregar examen"));
click(submit2);
await sleep(1800);
check("Iter2: veredicto PENDIENTE DE REVISIÓN HUMANA en la UI", root.textContent.includes("PENDIENTE DE REVISIÓN HUMANA"));
check("Iter2: resultado provisional explicado (parte automática)", root.textContent.includes("PROVISIONAL") || root.textContent.includes("provisional"));

// ── Iteración 2/3: rechazos controlados vía API (validación + sesión)
const tok = await auth.getToken();
const examQ = await (await realFetch(`${API}/api/v1/courses/soc-n1/exams/fase-1-examen`, {
  headers: { Authorization: `Bearer ${tok}` },
})).json();
// payload malformado (responses vacío) -> 422 ANTES de evaluar la sesión
const r422empty = await realFetch(`${API}/api/v1/courses/soc-n1/exams/fase-1-examen/attempts`, {
  method: "POST",
  headers: { "Content-Type": "application/json", Authorization: `Bearer ${tok}` },
  body: JSON.stringify({ session_id: "sesion-falsa", responses: [] }),
});
check("Iter2: respuestas malformadas -> rechazo controlado 422", r422empty.status === 422);
// respuestas válidas + sesión falsa -> 409
const r409 = await realFetch(`${API}/api/v1/courses/soc-n1/exams/fase-1-examen/attempts`, {
  method: "POST",
  headers: { "Content-Type": "application/json", Authorization: `Bearer ${tok}` },
  body: JSON.stringify({
    session_id: "sesion-falsa",
    responses: examQ.questions.map(q => ({ exercise_id: q.id, answer: null })),
  }),
});
check("Iter2: envío sin sesión válida -> 409", r409.status === 409);

const sess = await (await realFetch(`${API}/api/v1/courses/soc-n1/exams/fase-1-examen/sessions`, {
  method: "POST", headers: { Authorization: `Bearer ${tok}` },
})).json();
const dupResp = examQ.questions.map(q => ({ exercise_id: q.id, answer: null }));
dupResp.push({ exercise_id: dupResp[0].exercise_id, answer: null });
const r422 = await realFetch(`${API}/api/v1/courses/soc-n1/exams/fase-1-examen/attempts`, {
  method: "POST",
  headers: { "Content-Type": "application/json", Authorization: `Bearer ${tok}` },
  body: JSON.stringify({ session_id: sess.session_id, responses: dupResp }),
});
check("Iter2: ids duplicados -> rechazo controlado 422", r422.status === 422);

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

// ------------------------------------------------------------------ FASE C2: H-01 rev2 — modo supabase, resultados de signOut
// Mocks que modelan el contrato REAL verificado en el bundle vendorizado
// (supabase-js 2.118.0):
//   - getSession() lee del storage en cada llamada (clave sb-<ref>-auth-token)
//   - signOut({scope:"local"}) puede: (1) devolver {error:null} tras borrar,
//     (2) devolver {error} SIN borrar (early-return por getSession fallido),
//     (3) lanzar excepción (errores no-Auth propagados por admin.signOut).
const SB_KEY = "sb-fakeref-auth-token";           // ref = "fakeref" (hostname)
const SB_KEY_DERIVADA = "sb-fakeref-auth-token-user";
configOverride = { auth_mode: "supabase", supabase: { url: "https://fakeref.supabase.co", anon_key: "anon-publica" } };
await auth.loadAuthConfig(true);
const apiMod = (await import(`${JS}/api.js`)).api;

function installMockSession() {
  dom.window.localStorage.setItem(SB_KEY, JSON.stringify({
    access_token: "sb-token-muerto", refresh_token: "rt-muerto",
    user: { id: "u-fake-1", email: "persona@example.com", is_anonymous: false },
  }));
  dom.window.localStorage.setItem(SB_KEY_DERIVADA, JSON.stringify({ id: "u-fake-1" }));
}
function mockClient(signOutImpl) {
  return {
    auth: {
      // modelo fiel del vendor: getSession lee del storage en cada llamada
      getSession: async () => ({ data: { session: JSON.parse(dom.window.localStorage.getItem(SB_KEY) || "null") } }),
      signOut: signOutImpl,
      onAuthStateChange: () => ({ data: { subscription: { unsubscribe() {} } } }),
    },
  };
}

let hashChangeCount = 0;
dom.window.addEventListener("hashchange", () => hashChangeCount++);
let notifyCount = 0;
auth.onAuthChange(() => notifyCount++);

// ── Escenario 1: signOut devuelve { error: null } -> limpieza confirmada + notificación
installMockSession();
let signOutCalls = [];
let hashAtSignOut = null;
auth._setSbClientForTests(mockClient(async (opts) => {
  signOutCalls.push(opts || {});
  hashAtSignOut = dom.window.location.hash;         // evidencia de orden: limpieza ANTES de navegar
  dom.window.localStorage.removeItem(SB_KEY);       // modela _removeSession (éxito)
  dom.window.localStorage.removeItem(SB_KEY_DERIVADA);
  return { error: null };
}));
dom.window.location.hash = "#/";
await sleep(80);
check("C2a: sesión supabase activa antes del fallo", (await auth.getCurrentUser())?.label === "persona@example.com");
const hashBase1 = hashChangeCount;
let c2aErr = null;
try { await apiMod.dashboard(); } catch (e) { c2aErr = e; }   // server (modo test) rechaza "sb-token-muerto" -> 401
await sleep(80);
check("C2a: backend responde 401 al token supabase inválido", c2aErr?.status === 401);
check("C2a: signOut invocado con scope=local", signOutCalls.length === 1 && signOutCalls[0].scope === "local");
check("C2a: limpieza ocurrió ANTES de la navegación (hash aún no era #/auth)", hashAtSignOut === "#/");
check("C2a: sesión limpiada y cambio notificado", (await auth.getCurrentUser()) === null && notifyCount >= 1);
check("C2a: una única redirección a #/auth", dom.window.location.hash === "#/auth" && hashChangeCount - hashBase1 === 1);

// ── Escenario 2: signOut devuelve { error } SIN borrar -> fallo detectado + purga de recuperación
installMockSession();
auth._setSbClientForTests(mockClient(async () => ({ error: { name: "AuthUnknownError", message: "estado de sesión corrupto" } })));
check("C2b: sesión persistida de nuevo activa", (await auth.getCurrentUser())?.label === "persona@example.com");
let c2bErr = null;
try { await apiMod.dashboard(); } catch (e) { c2bErr = e; }
await sleep(80);
check("C2b: 401 y signOut devolvió error (sin excepción)", c2bErr?.status === 401);
check("C2b: purga eliminó sesión persistida y claves derivadas", dom.window.localStorage.getItem(SB_KEY) === null && dom.window.localStorage.getItem(SB_KEY_DERIVADA) === null);
check("C2b: la UI no trata la sesión rechazada como válida", (await auth.getCurrentUser()) === null);
await renderAuth(root);
check("C2b: #/auth muestra entrada, no 'Sesión activa'", !root.textContent.includes("Sesión activa") && root.textContent.includes("Iniciar sesión"));

// ── Escenario 3: signOut LANZA excepción -> manejada, sin sesión aparentemente válida, sin bucle de redirección
installMockSession();
auth._setSbClientForTests(mockClient(async () => { throw new Error("network unreachable"); }));
dom.window.location.hash = "#/";
await sleep(80);
const hashBase3 = hashChangeCount;
let e1 = null, e2 = null;
try { await apiMod.dashboard(); } catch (e) { e1 = e; }
try { await apiMod.attempts(5); } catch (e) { e2 = e; }   // segunda falla con hash ya en #/auth
await sleep(80);
check("C2c: excepción manejada (dos 401 sin crash)", e1?.status === 401 && e2?.status === 401);
check("C2c: sesión purgada tras excepción (storage y getCurrentUser)", dom.window.localStorage.getItem(SB_KEY) === null && (await auth.getCurrentUser()) === null);
check("C2c: exactamente UNA redirección, sin bucle", dom.window.location.hash === "#/auth" && hashChangeCount - hashBase3 === 1);

// ── Escenario 4 (rev3): resultados MALFORMADOS de signOut -> no confirman éxito -> purga
// (observación de auditoría: confirmed exige estructura exacta {error: null})
installMockSession();
auth._setSbClientForTests(mockClient(async () => ({})));                 // objeto sin propiedad 'error'
try { await apiMod.dashboard(); } catch (e) { /* 401 esperado */ }
await sleep(80);
check("C2d: resultado {} -> purga ejecutada, sesión no queda como limpia", dom.window.localStorage.getItem(SB_KEY) === null && (await auth.getCurrentUser()) === null);

installMockSession();
auth._setSbClientForTests(mockClient(async () => undefined));            // resultado ausente
try { await apiMod.dashboard(); } catch (e) { /* 401 esperado */ }
await sleep(80);
check("C2d: resultado undefined -> purga ejecutada", dom.window.localStorage.getItem(SB_KEY) === null && (await auth.getCurrentUser()) === null);

installMockSession();
auth._setSbClientForTests(mockClient(async () => ({ error: false })));   // valor inesperado (no null)
try { await apiMod.dashboard(); } catch (e) { /* 401 esperado */ }
await sleep(80);
check("C2d: resultado {error:false} -> purga ejecutada", dom.window.localStorage.getItem(SB_KEY) === null && (await auth.getCurrentUser()) === null);

console.log(failures === 0 ? "\n🎉 FRONTEND OK — todas las verificaciones pasaron" : `\n💥 ${failures} verificaciones fallaron`);
process.exit(failures === 0 ? 0 : 1);
