// Vista: roadmap del curso. Público (catálogo) + overlay de progreso si hay
// sesión autenticada. Los datos personales SOLO llegan del dashboard privado.
import { api } from "../api.js";
import { getCurrentUser } from "../auth.js";
import { el, KIND_BADGE, KIND_LABEL } from "../util.js";

export async function renderCourse(root, courseId) {
  root.innerHTML = "";

  const [cat, user] = await Promise.all([api.getCatalog(courseId), getCurrentUser()]);
  let dashCourse = null;
  if (user) {
    try {
      const dash = await api.dashboard();
      dashCourse = (dash.courses || []).find(c => c.id === courseId) || null;
    } catch (e) {
      dashCourse = null; // sin overlay; el contenido público sigue visible
    }
  }

  root.appendChild(
    el("div", { class: "page-head" },
      el("div", { class: "crumbs" }, el("a", { href: "#/" }, "panel"), " / ", courseId),
      el("h1", {}, `🛡️ ${cat.course.title}`),
      el("div", { class: "sub" },
        `${cat.course.meta?.perfil || ""} · dedicación ${cat.course.meta?.dedicacion || cat.course.estimated_weeks} · meta: ${cat.course.meta?.meta || "—"}`),
    ),
  );

  if (!user) {
    root.appendChild(
      el("div", { class: "guest-banner" },
        el("span", {}, "👤 Estás en modo lectura (sin sesión). "),
        el("a", { href: "#/auth" }, "Inicia sesión"),
        el("span", {}, " para guardar progreso, hacer ejercicios y exámenes."),
      ),
    );
  }

  if (cat.course.meta?.ventaja_dev) {
    root.appendChild(
      el("div", { class: "card", style: "margin-bottom:20px;border-color:#2a4a3d;background:linear-gradient(120deg,#10201a,#11161d)" },
        el("h3", { style: "color:var(--accent)" }, "⚡ Tu ventaja de dev"),
        el("p", { style: "font-size:13.5px;color:var(--text-dim)" }, cat.course.meta.ventaja_dev),
      ),
    );
  }

  if (dashCourse) {
    const s = dashCourse.stats || {};
    root.appendChild(
      el("div", { class: "grid cols-3", style: "margin-bottom:22px" },
        miniStat(`${s.lessons_completed}/${s.published_lessons ?? 0}`, "lecciones completadas"),
        miniStat(`${s.exercises_mastered}/${s.exercises_total ?? 0}`, "ejercicios dominados"),
        miniStat(`${s.completion_percent ?? 0}%`, "progreso global"),
      ),
    );
  }

  for (const mod of cat.modules) {
    const dashMod = dashCourse?.modules?.find(m => m.id === mod.id) || null;
    root.appendChild(moduleBlock(mod, dashMod, courseId, !!user));
  }
}

function miniStat(v, k) {
  return el("div", { class: "stat cyan" }, el("div", { class: "num" }, v), el("div", { class: "lbl" }, k));
}

function moduleBlock(mod, dashMod, courseId, hasSession) {
  const lessons = mod.lessons || [];
  const published = lessons.filter(l => l.status === "published");
  const dashLessons = dashMod?.lessons || [];
  const done = published.filter(l => dashLessons.find(d => d.id === l.id)?.progress_status === "completed").length;
  const pct = published.length ? Math.round(100 * done / published.length) : 0;

  const block = el("div", { class: "phase-block" },
    el("div", { class: "phase-head" },
      el("div", { class: "phase-num" }, `F${mod.phase}`),
      el("div", {},
        el("h2", {}, mod.title),
        el("div", { class: "sub" }, `${mod.subtitle} · ${mod.duration}`),
      ),
      el("div", { style: "margin-left:auto;display:flex;align-items:center;gap:12px" },
        hasSession && pct === 100 ? el("span", { class: "badge green" }, "✓ completado") : null,
        hasSession ? el("span", { style: "font-family:var(--mono);font-size:12px;color:var(--text-dim)" }, `${pct}%`) : null,
      ),
    ),
    hasSession ? el("div", { class: "pbar", style: "margin-bottom:12px" }, el("i", { style: `width:${pct}%` })) : null,
  );

  const card = el("div", { class: "card", style: "padding:0;overflow:hidden" });

  for (const l of lessons) {
    const isDraft = l.status !== "published";
    const dl = dashLessons.find(d => d.id === l.id);
    const st = dl?.progress_status || "not_started";
    const icon = isDraft ? "🔒" : st === "completed" ? "✓" : st === "in_progress" ? "◐" : "";
    const stCls = st === "completed" ? "done" : st === "in_progress" ? "doing" : "";

    const row = el("div", { class: `lesson-row${isDraft ? " locked" : ""}` },
      el("div", { class: `st ${stCls}` }, icon),
      el("div", {},
        el("div", { class: "ttl" }, l.title),
        el("div", { class: "sub" },
          isDraft ? "Contenido en construcción — próximamente"
            : `${KIND_LABEL[l.kind] || l.kind} · ~${l.estimated_minutes} min · ${l.exercise_count} ejercicios${hasSession && dl ? ` · ${dl.exercises_done ?? dl.exercises_mastered}/${dl.exercises_total} dominados` : ""}`),
      ),
      el("div", { class: "right" },
        el("span", { class: `badge ${KIND_BADGE[l.kind] || "dim"}` }, KIND_LABEL[l.kind] || l.kind),
        isDraft ? el("span", { class: "badge dim" }, "pronto") :
          !hasSession ? null :
          st === "completed" ? el("span", { class: "badge green" }, "completada") :
          st === "in_progress" ? el("span", { class: "badge cyan" }, "en curso") : null,
      ),
    );
    if (!isDraft) {
      row.addEventListener("click", () => { location.hash = `#/leccion/${courseId}/${mod.id}/${l.id}`; });
    }
    card.appendChild(row);
  }

  // examen del módulo
  if (mod.exam) {
    const dashExam = dashMod?.exam || null;
    const passed = dashExam?.passed;
    card.appendChild(
      el("div", { class: "exam-card" },
        el("span", { class: "ico" }, passed ? "🏆" : "📝"),
        el("div", {},
          el("div", { class: "ttl" }, mod.exam.title),
          el("div", { class: "sub" },
            passed ? `Aprobado con ${dashExam.best_percent}% (mejor de ${dashExam.attempts} intento${dashExam.attempts === 1 ? "" : "s"})`
              : dashExam?.attempts ? `Mejor puntaje: ${dashExam.best_percent}% · aprobación: ${mod.exam.pass_score}% · ${dashExam.attempts} intento(s)`
              : `${mod.exam.question_count} preguntas · aprobación: ${mod.exam.pass_score}%${mod.exam.time_limit_minutes ? ` · ${mod.exam.time_limit_minutes} min` : ""}`),
        ),
        el("div", { class: "right" },
          dashExam ? (passed ? el("span", { class: "badge green" }, "✓ aprobado")
            : dashExam.last_status === "pending_review" ? el("span", { class: "badge warn" }, "⏳ último: revisión pendiente")
            : el("span", { class: "badge warn" }, "pendiente")) : null,
          hasSession
            ? el("a", { class: "btn sm primary", href: `#/curso/${courseId}/examen/${mod.exam.id}` }, passed ? "Repetir" : "Presentar")
            : el("a", { class: "btn sm ghost", href: "#/auth" }, "Inicia sesión"),
        ),
      ),
    );
  }

  block.appendChild(card);

  if (mod.outcome) {
    block.appendChild(el("p", { style: "font-size:13px;color:var(--text-dim);margin-top:10px" }, "🎯 ", el("em", {}, mod.outcome)));
  }
  if (mod.resources?.length) {
    block.appendChild(
      el("div", { class: "resources" },
        el("span", { style: "font-size:12px;color:var(--text-faint);align-self:center" }, "Recursos:"),
        ...mod.resources.map(r => r.url
          ? el("a", { href: r.url, target: "_blank", rel: "noopener" }, `${r.title}${r.note ? ` — ${r.note}` : ""}`)
          : el("span", { style: "font-size:12.5px;color:var(--text-dim)" }, r.title)),
      ),
    );
  }
  return block;
}
