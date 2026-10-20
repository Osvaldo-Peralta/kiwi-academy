// Prueba de humo del frontend con jsdom contra la API real.
// Requisitos: backend corriendo en http://127.0.0.1:8000 y `npm install`.
// Uso: node tests/frontend_smoke.mjs
import { JSDOM } from "jsdom";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const JS = path.resolve(__dirname, "..", "frontend", "js");

const dom = new JSDOM(`<!DOCTYPE html><html><body>
  <header><nav id="mainnav"><a data-nav="dashboard"></a><a data-nav="course"></a><a data-nav="historial"></a></nav></header>
  <main id="app"></main>
  <div id="toast-container"></div>
  <span id="user-id-label"></span>
</body></html>`, { url: "http://localhost/", pretendToBeVisual: true });

const realFetch = globalThis.fetch;
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.navigator = dom.window.navigator;
globalThis.localStorage = dom.window.localStorage;
// usuario único por corrida: test idempotente que no contamina estados previos
dom.window.localStorage.setItem("kiwi.userId", `smoketest-${Date.now()}`);
globalThis.location = dom.window.location;
globalThis.fetch = (url, opts) => realFetch(new URL(url, "http://127.0.0.1:8000"), opts);
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

// ------------------------------------------------------------------ 1. Dashboard
const { renderDashboard } = await import(`${JS}/views/dashboard.js`);
await renderDashboard(root);
check("Dashboard renderiza stats", root.querySelectorAll(".stat").length >= 4);
check("Dashboard muestra hero/acción", !!root.querySelector(".hero"));
check("Dashboard muestra curso", root.textContent.includes("Analista SOC"));

// ------------------------------------------------------------------ 2. Curso
const { renderCourse } = await import(`${JS}/views/course.js`);
await renderCourse(root, "soc-n1");
check("Curso renderiza 4 fases", root.querySelectorAll(".phase-block").length === 4);
check("Curso marca lecciones draft como bloqueadas", root.querySelectorAll(".lesson-row.locked").length === 9);
check("Curso muestra tarjeta de examen", !!root.querySelector(".exam-card"));
check("Curso lista recursos externos", root.querySelectorAll(".resources a").length >= 3);

// ------------------------------------------------------------------ 3. Lección + ejercicio interactivo
const { renderLesson } = await import(`${JS}/views/lesson.js`);
await renderLesson(root, "soc-n1", "01-fundamentos", "01-modelo-osi-protocolos");
check("Lección renderiza tabs", root.querySelectorAll(".tabs button").length === 3);
check("Teoría renderiza markdown (tablas)", root.querySelectorAll(".md table").length >= 1);
check("Teoría renderiza código", root.querySelectorAll(".md pre").length >= 1);

const markBtn = [...root.querySelectorAll("button")].find(b => b.textContent.includes("Marcar teoría como leída"));
check("Botón 'marcar teoría' existe", !!markBtn);
markBtn?.dispatchEvent(new dom.window.Event("click", { bubbles: true }));
await sleep(400);

const exTab = [...root.querySelectorAll(".tabs button")].find(b => b.textContent.includes("Ejercicios"));
exTab.dispatchEvent(new dom.window.Event("click", { bubbles: true }));
await sleep(150);
check("Ejercicios renderizan tarjetas", root.querySelectorAll(".ex-card").length === 8);

const card1 = root.querySelector("#ex-l1-e1");
const opt = [...card1.querySelectorAll(".opt")].find(o => o.textContent.includes("Capa 4"));
check("Opción correcta visible en l1-e1", !!opt);
opt?.dispatchEvent(new dom.window.Event("click", { bubbles: true }));
const checkBtn = [...card1.querySelectorAll("button")].find(b => b.textContent === "Comprobar");
checkBtn?.dispatchEvent(new dom.window.Event("click", { bubbles: true }));
await sleep(600);
check("Feedback positivo aparece tras responder bien", !!card1.querySelector(".fb.ok"));
check("Ejercicio marcado como dominado", card1.classList.contains("mastered"));

// ------------------------------------------------------------------ 4. Examen completo (intro -> start -> submit -> results)
const { renderExam } = await import(`${JS}/views/exam.js`);
await renderExam(root, "soc-n1", "fase-1-examen");
check("Examen: intro renderizada", root.textContent.includes("Comenzar examen"));
const startBtn = [...root.querySelectorAll("button")].find(b => b.textContent.includes("Comenzar examen"));
startBtn.dispatchEvent(new dom.window.Event("click", { bubbles: true }));
await sleep(300);
check("Examen: shell con timer", !!root.querySelector(".timer"));
check("Examen: paleta con 15 botones", root.querySelectorAll(".palette-grid button").length === 15);
check("Examen: tarjeta de pregunta visible", !!root.querySelector(".exam-qcard"));

const anyOpt = root.querySelector(".exam-qcard .opt");
anyOpt?.dispatchEvent(new dom.window.Event("click", { bubbles: true }));
await sleep(100);
check("Examen: paleta marca pregunta respondida", !!root.querySelector(".palette-grid button.answered"));

const submitBtn = [...root.querySelectorAll("button")].find(b => b.textContent.includes("Entregar examen"));
submitBtn.dispatchEvent(new dom.window.Event("click", { bubbles: true }));
await sleep(1500);
check("Examen: resultados con anillo de puntaje", !!root.querySelector(".score-ring"));
check("Examen: revisión pregunta por pregunta", root.querySelectorAll(".review-item").length === 15);
check("Examen: exportadores de reporte", [...root.querySelectorAll("button")].some(b => b.textContent.includes("Copiar reporte")));
check("Examen: veredicto visible", !!root.querySelector(".verdict"));

// ------------------------------------------------------------------ 5. Historial
const { renderHistorial } = await import(`${JS}/views/historial.js`);
await renderHistorial(root);
check("Historial lista intentos", root.querySelectorAll(".hist-table tbody tr").length >= 2);

console.log(failures === 0 ? "\n🎉 FRONTEND OK — todas las verificaciones pasaron" : `\n💥 ${failures} verificaciones fallaron`);
process.exit(failures === 0 ? 0 : 1);
