// Vista: Panel principal (dashboard con sesión) y bienvenida (invitados).
import { api } from "../api.js";
import { el, fmtDate, ring } from "../util.js";

// ---------------------------------------------------------------------------
// Invitados (sin sesión): catálogo público + CTA de auth
// ---------------------------------------------------------------------------
export async function renderWelcome(root) {
  root.innerHTML = "";
  root.appendChild(
    el("div", { class: "hero" },
      el("div", { style: "font-size:34px" }, "🥝"),
      el("div", { class: "hero-text" },
        el("h2", {}, "Kiwi Academy — aprende ciberseguridad practicando"),
        el("p", {}, "Explora el contenido público o inicia sesión para guardar tu progreso, ejercicios y exámenes."),
      ),
      el("a", { class: "btn primary", href: "#/auth" }, "Iniciar sesión →"),
    ),
  );

  const { courses } = await api.listCourses();
  const grid = el("div", { class: "grid cols-2" });
  for (const c of courses) {
    grid.appendChild(
      el("a", { class: "card course-card", href: `#/curso/${c.id}`, style: "display:block" },
        el("h3", {}, `🎓 ${c.title}`),
        el("p", { style: "font-size:13px;color:var(--text-dim);margin:6px 0" },
          (c.description || "").slice(0, 180) + "…"),
        el("div", { style: "display:flex;gap:8px;flex-wrap:wrap;margin-top:8px" },
          el("span", { class: "badge cyan" }, `${c.modules} módulos`),
          el("span", { class: "badge dim" }, c.level),
          c.estimated_weeks ? el("span", { class: "badge dim" }, c.estimated_weeks) : null,
        ),
      ),
    );
  }
  root.appendChild(grid);
  root.appendChild(
    el("p", { style: "margin-top:18px;font-size:13px;color:var(--text-faint);text-align:center" },
      "Como invitado puedes leer teoría y ejemplos; los ejercicios evaluados y exámenes requieren sesión."),
  );
}

// ---------------------------------------------------------------------------
// Panel personal (requiere sesión)
// ---------------------------------------------------------------------------
export async function renderDashboard(root) {
  root.innerHTML = "";
  const dash = await api.dashboard();

  const o = dash.overall;
  const course = (dash.courses || [])[0];

  root.appendChild(
    el("div", { class: "page-head" },
      el("h1", {}, "Panel de control"),
      el("div", { class: "sub" }, "Tu avance en el laboratorio, de un vistazo."),
    ),
  );

  // hero: continuar donde lo dejaste
  const next = (dash.next_actions || [])[0];
  if (next) {
    root.appendChild(
      el("div", { class: "hero" },
        el("div", { style: "font-size:34px" }, next.kind === "take_exam" ? "📝" : "🎯"),
        el("div", { class: "hero-text" },
          el("h2", {}, next.kind === "take_exam" ? "Hora del examen" : "Continúa tu entrenamiento"),
          el("p", {}, next.label),
        ),
        el("a", { class: "btn primary", href: actionHref(next) }, next.kind === "take_exam" ? "Presentar examen" : "Continuar →"),
      ),
    );
  } else if (course) {
    root.appendChild(
      el("div", { class: "hero" },
        el("div", { style: "font-size:34px" }, "🛡️"),
        el("div", { class: "hero-text" },
          el("h2", {}, "Bienvenido al laboratorio"),
          el("p", {}, "Empieza por la Fase 1 del curso SOC N1."),
        ),
        el("a", { class: "btn primary", href: `#/curso/${course.id}` }, "Ir al curso →"),
      ),
    );
  }

  // stats
  root.appendChild(
    el("div", { class: "grid cols-4", style: "margin-bottom:18px" },
      statCard(`${o.lessons_completed}/${o.lessons_total}`, "Lecciones completas"),
      statCard(o.exercises_mastered, "Ejercicios dominados", "cyan"),
      statCard(`${o.exams_passed}/${o.exams_taken}`, "Exámenes aprobados", "violet"),
      statCard(activityStreak(dash.activity), "Racha (días)", "warn"),
    ),
  );

  const grid = el("div", { class: "grid cols-2" });

  // próximas acciones
  const actions = el("div", { class: "card" }, el("h3", {}, "📌 Próximas acciones"));
  const list = el("div", { class: "action-row" });
  if (!dash.next_actions?.length) list.appendChild(el("p", { style: "color:var(--text-dim);font-size:13.5px" }, "Sin acciones pendientes."));
  for (const a of dash.next_actions || []) {
    list.appendChild(
      el("a", { class: "action-item", href: actionHref(a) },
        el("span", { class: "ico" }, a.kind === "take_exam" ? "📝" : a.kind === "continue_lesson" ? "▶️" : "🚀"),
        el("span", { class: "txt" }, a.label),
        el("span", { class: "meta badge cyan" }, a.kind === "take_exam" ? "examen" : "lección"),
      ),
    );
  }
  actions.appendChild(list);
  grid.appendChild(actions);

  // actividad reciente (14 días)
  const actCard = el("div", { class: "card" }, el("h3", {}, "🔥 Actividad (últimos 14 días)"));
  const actGrid = el("div", { class: "activity-grid" });
  const activity = dash.activity || {};
  for (let i = 13; i >= 0; i--) {
    const d = new Date(); d.setDate(d.getDate() - i);
    const key = d.toISOString().slice(0, 10);
    const n = activity[key] || 0;
    const lvl = n === 0 ? "" : n <= 2 ? "lvl1" : n <= 6 ? "lvl2" : "lvl3";
    actGrid.appendChild(el("div", { class: `day ${lvl}`, "data-tip": `${key}: ${n} evento${n === 1 ? "" : "s"}` }));
  }
  actCard.appendChild(actGrid);
  actCard.appendChild(el("p", { style: "font-size:12px;color:var(--text-faint);margin-top:8px" },
    "Cada ejercicio, lección o examen cuenta. La constancia es lo que te lleva a la vacante."));
  grid.appendChild(actCard);

  root.appendChild(grid);

  // intentos recientes + progreso del curso
  const bottom = el("div", { class: "grid cols-2", style: "margin-top:14px" });

  const recent = el("div", { class: "card" }, el("h3", {}, "🕘 Evaluaciones recientes"));
  if (!dash.recent_attempts?.length) {
    recent.appendChild(el("p", { style: "color:var(--text-dim);font-size:13.5px" }, "Aún no hay intentos. ¡Resuelve tu primer ejercicio!"));
  }
  for (const a of (dash.recent_attempts || []).slice(0, 6)) {
    const pct = a.percent ?? 0;
    recent.appendChild(
      el("div", { class: "attempt-row" },
        el("span", {}, a.type === "exam" ? "📝" : "✏️"),
        el("span", {}, a.ref || "—"),
        el("span", { class: `badge ${a.passed ? "green" : pct > 0 ? "warn" : "dim"}` }, `${pct}%`),
        el("span", { class: "when" }, fmtDate(a.submitted_at)),
      ),
    );
  }
  bottom.appendChild(recent);

  if (course) {
    const cprog = el("div", { class: "card" },
      el("h3", {}, `🎓 ${course.title}`),
      el("p", { style: "font-size:13px;color:var(--text-dim);margin-bottom:12px" }, course.description?.slice(0, 160) + "…"),
    );
    for (const m of course.modules || []) {
      const published = m.lessons.filter(l => l.status === "published");
      const done = published.filter(l => l.progress_status === "completed").length;
      const pct = published.length ? Math.round(100 * done / published.length) : 0;
      cprog.appendChild(
        el("div", { style: "display:flex;align-items:center;gap:12px;margin-bottom:10px" },
          el("div", { style: "flex:1" },
            el("div", { style: "font-size:13px;font-weight:650" }, `Fase ${m.phase}: ${m.title}`),
            el("div", { style: "display:flex;gap:8px;align-items:center;margin-top:4px" },
              el("div", { class: "pbar", style: "flex:1" }, el("i", { style: `width:${pct}%` })),
              el("span", { style: "font-family:var(--mono);font-size:11px;color:var(--text-dim)" }, `${done}/${published.length || m.lessons.length}`),
            ),
          ),
        ),
      );
    }
    cprog.appendChild(el("a", { class: "btn sm ghost", href: `#/curso/${course.id}`, style: "margin-top:6px" }, "Ver roadmap completo →"));
    bottom.appendChild(cprog);
  }

  root.appendChild(bottom);
}

function statCard(num, lbl, cls = "") {
  return el("div", { class: `stat ${cls}` }, el("div", { class: "num" }, String(num)), el("div", { class: "lbl" }, lbl));
}

function actionHref(a) {
  if (a.kind === "take_exam") return `#/curso/${a.course_id}/examen/${a.exam_id}`;
  return `#/leccion/${a.course_id}/${a.module_id}/${a.lesson_id}`;
}

function activityStreak(activity) {
  let streak = 0;
  for (let i = 0; i < 365; i++) {
    const d = new Date(); d.setDate(d.getDate() - i);
    const key = d.toISOString().slice(0, 10);
    if ((activity || {})[key]) streak++;
    else if (i > 0 || streak === 0) break; // hoy sin actividad aún no rompe racha
  }
  return streak;
}
