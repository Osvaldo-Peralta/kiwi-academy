// Router principal de la SPA (hash-based, sin dependencias).
//
// Rutas:
//   #/                                     -> dashboard (con sesión) o bienvenida (invitado)
//   #/auth                                 -> entrada de sesión / modo dev
//   #/curso/:courseId                      -> roadmap del curso (público; progreso si hay sesión)
//   #/leccion/:courseId/:moduleId/:lessonId-> lección (pública; práctica requiere sesión)
//   #/curso/:courseId/examen/:examId       -> examen (requiere sesión)
//   #/historial                            -> historial de intentos (requiere sesión)

import { loadAuthConfig, getCurrentUser, onAuthChange, signOut } from "./auth.js";
import { el, toast } from "./util.js";
import { renderDashboard, renderWelcome } from "./views/dashboard.js";
import { renderCourse } from "./views/course.js";
import { renderLesson } from "./views/lesson.js";
import { renderExam } from "./views/exam.js";
import { renderHistorial } from "./views/historial.js";
import { renderAuth } from "./views/auth.js";

const app = document.getElementById("app");

const ROUTES = [
  { re: /^#\/$/, view: renderHome, nav: "dashboard" },
  { re: /^#\/auth$/, view: (m, root) => renderAuth(root), nav: "auth" },
  { re: /^#\/curso\/([\w-]+)$/, view: (m, root) => renderCourse(root, m[1]), nav: "course" },
  { re: /^#\/leccion\/([\w-]+)\/([\w-]+)\/([\w-]+)$/, view: (m, root) => renderLesson(root, m[1], m[2], m[3]), nav: "course" },
  { re: /^#\/curso\/([\w-]+)\/examen\/([\w-]+)$/, view: (m, root) => renderExam(root, m[1], m[2]), nav: "course" },
  { re: /^#\/historial$/, view: (m, root) => renderHistorial(root), nav: "historial" },
];

async function renderHome(m, root) {
  const user = await getCurrentUser();
  if (user) await renderDashboard(root);
  else await renderWelcome(root);
}

async function updateChip() {
  const chip = document.getElementById("session-chip");
  if (!chip) return;
  chip.innerHTML = "";
  let user = null;
  try { user = await getCurrentUser(); } catch { /* sin backend aún */ }
  if (user) {
    chip.append(
      el("span", { class: "chip-user", title: user.id }, `👤 ${user.label}${user.anonymous ? " (invitado)" : ""}`),
      el("button", {
        class: "chip-btn", title: "Cerrar sesión",
        onclick: async () => { await signOut(); toast("Sesión cerrada"); updateChip(); route(); },
      }, "salir"),
    );
  } else {
    chip.append(el("a", { class: "chip-user", href: "#/auth" }, "iniciar sesión"));
  }
}

async function route() {
  const hash = location.hash || "#/";
  app.classList.remove("wide");

  let navActive = null;
  for (const r of ROUTES) {
    const m = hash.match(r.re);
    if (m) { navActive = r.nav; break; }
  }
  document.querySelectorAll("#mainnav a").forEach(a => {
    a.classList.toggle("active", a.dataset.nav === navActive);
  });

  app.innerHTML = "";
  app.appendChild(el("div", { class: "boot-loading" }, el("div", { class: "spinner" }), "Cargando…"));

  try {
    for (const r of ROUTES) {
      const m = hash.match(r.re);
      if (m) {
        app.innerHTML = "";
        await r.view(m, app);
        window.scrollTo({ top: 0 });
        return;
      }
    }
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

async function boot() {
  try {
    await loadAuthConfig();
  } catch (e) {
    // backend caído: las vistas mostrarán su estado de error igualmente
    console.warn("No se pudo cargar /api/v1/config:", e.message);
  }
  onAuthChange(() => { updateChip(); route(); });
  window.addEventListener("hashchange", route);
  await updateChip();
  if (!location.hash) location.hash = "#/";
  await route();
}

boot();
