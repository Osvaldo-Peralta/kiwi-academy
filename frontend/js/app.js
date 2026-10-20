// Router principal de la SPA (hash-based, sin dependencias).
//
// Rutas:
//   #/                                     -> dashboard
//   #/curso/:courseId                      -> roadmap del curso
//   #/leccion/:courseId/:moduleId/:lessonId-> lección (teoría/ejemplos/ejercicios)
//   #/curso/:courseId/examen/:examId       -> examen
//   #/historial                            -> historial de intentos
//
// Añadir una vista nueva = añadir una ruta aquí. Nada más.

import { api, USER_ID } from "./api.js";
import { el, toast } from "./util.js";
import { renderDashboard } from "./views/dashboard.js";
import { renderCourse } from "./views/course.js";
import { renderLesson } from "./views/lesson.js";
import { renderExam } from "./views/exam.js";
import { renderHistorial } from "./views/historial.js";

const app = document.getElementById("app");
document.getElementById("user-id-label").textContent = USER_ID;

const ROUTES = [
  { re: /^#\/$/, view: () => renderDashboard(app), nav: "dashboard" },
  { re: /^#\/curso\/([\w-]+)$/, view: (m) => renderCourse(app, m[1]), nav: "course" },
  { re: /^#\/leccion\/([\w-]+)\/([\w-]+)\/([\w-]+)$/, view: (m) => renderLesson(app, m[1], m[2], m[3]), nav: "course" },
  { re: /^#\/curso\/([\w-]+)\/examen\/([\w-]+)$/, view: (m) => renderExam(app, m[1], m[2]), nav: "course" },
  { re: /^#\/historial$/, view: () => renderHistorial(app), nav: "historial" },
];

async function route() {
  const hash = location.hash || "#/";
  app.classList.remove("wide");

  // highlight de navegación
  let navActive = null;
  for (const r of ROUTES) {
    const m = hash.match(r.re);
    if (m) { navActive = r.nav; break; }
  }
  document.querySelectorAll("#mainnav a").forEach(a => {
    a.classList.toggle("active", a.dataset.nav === navActive);
  });

  app.innerHTML = "";
  const loading = el("div", { class: "boot-loading" }, el("div", { class: "spinner" }), "Cargando…");
  app.appendChild(loading);

  try {
    for (const r of ROUTES) {
      const m = hash.match(r.re);
      if (m) {
        await r.view(m);
        window.scrollTo({ top: 0 });
        return;
      }
    }
    // 404
    app.innerHTML = "";
    app.appendChild(
      el("div", { class: "empty-state" },
        el("div", { class: "big" }, "🧭"),
        el("h2", {}, "Ruta no encontrada"),
        el("p", { style: "margin:8px 0 16px" }, el("code", {}, hash)),
        el("a", { class: "btn primary", href: "#/" }, "Volver al panel"),
      ),
    );
  } catch (e) {
    app.innerHTML = "";
    app.appendChild(
      el("div", { class: "empty-state" },
        el("div", { class: "big" }, "⚠️"),
        el("h2", {}, "Algo falló"),
        el("p", { style: "margin:8px 0" }, e.message || String(e)),
        el("p", { style: "font-size:12.5px;color:var(--text-faint)" }, "¿Está el backend corriendo? (python run.py)"),
        el("a", { class: "btn ghost", style: "margin-top:14px", href: "#/" }, "Reintentar"),
      ),
    );
    toast(e.message || "Error de conexión con la API", "err");
  }
}

window.addEventListener("hashchange", route);
window.addEventListener("DOMContentLoaded", () => { if (!location.hash) location.hash = "#/"; route(); });

// chequeo de salud al cargar
api.dashboard().catch(() => {
  toast("No se pudo contactar la API. Asegúrate de correr `python run.py`", "err", 6000);
});
