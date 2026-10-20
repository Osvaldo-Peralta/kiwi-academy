// Vista: roadmap del curso (módulos/fases, lecciones, exámenes, recursos)
import { api } from "../api.js";
import { el, KIND_BADGE, KIND_LABEL } from "../util.js";

export async function renderCourse(root, courseId) {
  root.innerHTML = "";
  const dash = await api.dashboard();
  const course = (dash.courses || []).find(c => c.id === courseId);
  if (!course) { root.appendChild(el("div", { class: "empty-state" }, el("div", { class: "big" }, "🤔"), "Curso no encontrado.")); return; }

  root.appendChild(
    el("div", { class: "page-head" },
      el("div", { class: "crumbs" }, el("a", { href: "#/" }, "panel"), " / ", course.id),
      el("h1", {}, `🛡️ ${course.title}`),
      el("div", { class: "sub" },
        `${course.meta?.perfil || ""} · dedicación ${course.meta?.dedicacion || course.estimated_weeks} · meta: ${course.meta?.meta || "—"}`),
    ),
  );

  if (course.meta?.ventaja_dev) {
    root.appendChild(
      el("div", { class: "card", style: "margin-bottom:20px;border-color:#2a4a3d;background:linear-gradient(120deg,#10201a,#11161d)" },
        el("h3", { style: "color:var(--accent)" }, "⚡ Tu ventaja de dev"),
        el("p", { style: "font-size:13.5px;color:var(--text-dim)" }, course.meta.ventaja_dev),
      ),
    );
  }

  const s = course.stats || {};
  root.appendChild(
    el("div", { class: "grid cols-3", style: "margin-bottom:22px" },
      miniStat(`${s.lessons_completed}/${s.published_lessons ?? 0}`, "lecciones completadas"),
      miniStat(`${s.exercises_mastered}/${s.exercises_total ?? 0}`, "ejercicios dominados"),
      miniStat(`${s.completion_percent ?? 0}%`, "progreso global"),
    ),
  );

  for (const mod of course.modules || []) {
    root.appendChild(moduleBlock(mod, courseId));
  }
}

function miniStat(v, k) {
  return el("div", { class: "stat cyan" }, el("div", { class: "num" }, v), el("div", { class: "lbl" }, k));
}

function moduleBlock(m, courseId) {
  const published = m.lessons.filter(l => l.status === "published");
  const done = published.filter(l => l.progress_status === "completed").length;
  const pct = published.length ? Math.round(100 * done / published.length) : 0;

  const block = el("div", { class: "phase-block" },
    el("div", { class: "phase-head" },
      el("div", { class: "phase-num" }, `F${m.phase}`),
      el("div", {},
        el("h2", {}, m.title),
        el("div", { class: "sub" }, `${m.subtitle} · ${m.duration}`),
      ),
      el("div", { style: "margin-left:auto;display:flex;align-items:center;gap:12px" },
        pct === 100 ? el("span", { class: "badge green" }, "✓ completado") : null,
        el("span", { style: "font-family:var(--mono);font-size:12px;color:var(--text-dim)" }, `${pct}%`),
      ),
    ),
    el("div", { class: "pbar", style: "margin-bottom:12px" }, el("i", { style: `width:${pct}%` })),
  );

  const card = el("div", { class: "card", style: "padding:0;overflow:hidden" });

  for (const l of m.lessons) {
    const isDraft = l.status !== "published";
    const st = l.progress_status;
    const icon = isDraft ? "🔒" : st === "completed" ? "✓" : st === "in_progress" ? "◐" : "";
    const stCls = st === "completed" ? "done" : st === "in_progress" ? "doing" : "";

    const row = el("div", { class: `lesson-row${isDraft ? " locked" : ""}` },
      el("div", { class: `st ${stCls}` }, icon),
      el("div", {},
        el("div", { class: "ttl" }, l.title),
        el("div", { class: "sub" },
          isDraft ? "Contenido en construcción — próximamente"
            : `${KIND_LABEL[l.kind] || l.kind} · ~${l.estimated_minutes} min · ${l.exercises_total} ejercicios${l.exercises_total ? ` · ${l.exercises_done ?? l.exercises_mastered}/${l.exercises_total} dominados` : ""}`),
      ),
      el("div", { class: "right" },
        el("span", { class: `badge ${KIND_BADGE[l.kind] || "dim"}` }, KIND_LABEL[l.kind] || l.kind),
        isDraft ? el("span", { class: "badge dim" }, "pronto") :
          st === "completed" ? el("span", { class: "badge green" }, "completada") :
          st === "in_progress" ? el("span", { class: "badge cyan" }, "en curso") : null,
      ),
    );
    if (!isDraft) {
      row.addEventListener("click", () => { location.hash = `#/leccion/${courseId}/${m.id}/${l.id}`; });
    }
    card.appendChild(row);
  }

  // examen del módulo
  if (m.exam) {
    const passed = m.exam.passed;
    card.appendChild(
      el("div", { class: "exam-card" },
        el("span", { class: "ico" }, passed ? "🏆" : "📝"),
        el("div", {},
          el("div", { class: "ttl" }, m.exam.title),
          el("div", { class: "sub" },
            passed ? `Aprobado con ${m.exam.best_percent}% (mejor de ${m.exam.attempts} intento${m.exam.attempts === 1 ? "" : "s"})`
              : m.exam.attempts ? `Mejor puntaje: ${m.exam.best_percent}% · aprobación: ${m.exam.pass_score}% · ${m.exam.attempts} intento(s)`
              : `Aprobación: ${m.exam.pass_score}% · sin intentos aún`),
        ),
        el("div", { class: "right" },
          passed ? el("span", { class: "badge green" }, "✓ aprobado") : el("span", { class: "badge warn" }, "pendiente"),
          el("a", { class: "btn sm primary", href: `#/curso/${courseId}/examen/${m.exam.id}` }, passed ? "Repetir" : "Presentar"),
        ),
      ),
    );
  }

  block.appendChild(card);

  // resultado del módulo + recursos
  if (m.outcome) {
    block.appendChild(el("p", { style: "font-size:13px;color:var(--text-dim);margin-top:10px" }, "🎯 ", el("em", {}, m.outcome)));
  }
  if (m.resources?.length) {
    block.appendChild(
      el("div", { class: "resources" },
        el("span", { style: "font-size:12px;color:var(--text-faint);align-self:center" }, "Recursos:"),
        ...m.resources.map(r => r.url
          ? el("a", { href: r.url, target: "_blank", rel: "noopener" }, `${r.title}${r.note ? ` — ${r.note}` : ""}`)
          : el("span", { style: "font-size:12.5px;color:var(--text-dim)" }, r.title)),
      ),
    );
  }
  return block;
}
