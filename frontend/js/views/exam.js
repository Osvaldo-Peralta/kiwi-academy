// Vista: examen (intro -> examen cronometrado -> resultados con revisión)
// Iteración 1: presentar exámenes requiere sesión (identidad JWT).
import { api } from "../api.js";
import { getCurrentUser } from "../auth.js";
import { el, escapeHtml, fmtClock, toast } from "../util.js";
import { createExerciseWidget } from "../components/exercises.js";

export async function renderExam(root, courseId, examId) {
  root.innerHTML = "";
  const user = await getCurrentUser();
  let exam;
  try {
    exam = await api.getExam(courseId, examId);
  } catch (e) {
    root.appendChild(el("div", { class: "empty-state" }, el("div", { class: "big" }, "🚫"), e.message));
    return;
  }
  renderIntro(root, courseId, exam, user);
}

// ------------------------------------------------------------------ intro
function renderIntro(root, courseId, exam, user) {
  root.appendChild(
    el("div", { class: "exam-intro" },
      el("div", { class: "page-head", style: "text-align:center" },
        el("div", { style: "font-size:44px;margin-bottom:8px" }, "📝"),
        el("h1", {}, exam.title),
        el("div", { class: "sub", style: "margin-top:6px" }, exam.description),
      ),
      el("div", { class: "card" },
        el("div", { class: "exam-rules" },
          rule(String(exam.question_count), "preguntas"),
          rule(String(exam.total_points), "puntos totales"),
          rule(`${exam.pass_score}%`, "para aprobar"),
          rule(exam.time_limit_minutes ? `${exam.time_limit_minutes} min` : "∞", "límite de tiempo"),
        ),
        el("ul", { style: "margin:14px 0 0 20px;font-size:13.5px;color:var(--text-dim)" },
          el("li", {}, "Recibes todas las preguntas de una vez y las entregas juntas al final."),
          el("li", {}, "Puedes navegar entre preguntas y dejarlas en cualquier orden."),
          el("li", {}, "⏱ El temporizador es informativo: **el tiempo límite lo hace cumplir el servidor** (manipularlo no permite excederlo)."),
          el("li", {}, "✍️ Las respuestas libres quedan **pendientes de revisión humana**: el examen no se aprueba ni se reprueba hasta que un revisor autorizado las puntúe (recibirás un resultado provisional de la parte automática)."),
          el("li", {}, "Al terminar: corrección automática, feedback por pregunta, áreas débiles y reporte exportable para pedir feedback profundo a tu IA/mentor."),
          el("li", {}, "Intentos ilimitados: cada uno queda en tu historial para medir mejora."),
        ),
        el("div", { style: "display:flex;gap:10px;margin-top:18px;justify-content:center" },
          user
            ? el("button", { class: "btn primary lg", onclick: () => startExam(root, courseId, exam) }, "▶ Comenzar examen")
            : el("a", { class: "btn primary lg", href: "#/auth" }, "🔒 Inicia sesión para presentar"),
          el("a", { class: "btn ghost lg", href: `#/curso/${courseId}` }, "Volver al roadmap"),
        ),
        user ? null : el("p", { class: "auth-note", style: "text-align:center" },
          "Los exámenes guardan resultados e informes asociados a tu identidad verificada: requieren sesión."),
      ),
    ),
  );
}

function rule(v, k) {
  return el("div", { class: "rule" }, el("div", { class: "v" }, v), el("div", { class: "k" }, k));
}

// ------------------------------------------------------------------ examen en curso
async function startExam(root, courseId, exam) {
  // Iteración 2: la sesión (y el reloj) la emite el SERVIDOR
  let session;
  try {
    session = await api.startExamSession(courseId, exam.id);
  } catch (e) {
    toast(`No se pudo iniciar el examen: ${e.message}`, "err", 6000);
    return;
  }

  root.innerHTML = "";
  root.classList.add("wide");
  const answers = {};      // exercise_id -> answer
  const widgets = {};      // exercise_id -> widget
  let current = 0;
  let timerInterval = null;
  let submitted = false;

  const questions = exam.questions || [];

  const timerEl = el("span", { class: "timer" }, exam.time_limit_minutes ? fmtClock(exam.time_limit_minutes * 60) : "—:——");
  const answeredEl = el("span", { class: "qcount" }, `0/${questions.length} respondidas`);
  const submitBtn = el("button", { class: "btn primary", onclick: () => confirmSubmit() }, "Entregar examen");

  const topbar = el("div", { class: "exam-topbar" },
    el("span", {}, "⏱"), timerEl, answeredEl, submitBtn);
  root.appendChild(topbar);

  const shell = el("div", { class: "exam-shell" });
  const qArea = el("div");
  const palette = el("div", { class: "exam-palette card" });
  shell.append(qArea, palette);
  root.appendChild(shell);

  // paleta de navegación
  palette.appendChild(el("h4", {}, "Preguntas"));
  const palGrid = el("div", { class: "palette-grid" });
  const palBtns = [];
  questions.forEach((q, i) => {
    const b = el("button", { onclick: () => show(i) }, String(i + 1));
    palBtns.push(b);
    palGrid.appendChild(b);
  });
  palette.appendChild(palGrid);
  palette.appendChild(el("div", { class: "palette-legend" },
    el("span", {}, el("span", { class: "dot", style: "background:var(--accent-soft);border:1px solid var(--accent-dim)" }), "respondida"),
    el("span", {}, el("span", { class: "dot", style: "background:var(--bg-panel);border:1px solid var(--border)" }), "sin responder"),
  ));

  // timer basado en el DEADLINE DEL SERVIDOR (con compensación de reloj local).
  // Manipular el reloj/temporizador del cliente no extiende el límite: el
  // servidor lo vuelve a comprobar al recibir el envío.
  const clockOffsetMs = session.server_time_unix * 1000 - Date.now();
  if (session.deadline_unix) {
    const tick = () => {
      const remaining = Math.max(0, Math.round(
        (session.deadline_unix * 1000 - (Date.now() + clockOffsetMs)) / 1000));
      timerEl.textContent = fmtClock(remaining);
      timerEl.title = "Tiempo restante según el reloj del servidor";
      if (remaining <= 300) timerEl.classList.add("low");
      if (remaining <= 0) {
        clearInterval(timerInterval);
        toast("⏰ Tiempo agotado (reloj de servidor) — entregando automáticamente", "err", 5000);
        doSubmit();
      }
    };
    tick();
    timerInterval = setInterval(tick, 1000);
  }

  // montar todas las preguntas (se muestran de una en una)
  const qCards = questions.map((q, i) => {
    const widget = createExerciseWidget(q, {
      mode: "exam",
      onChange: (ans) => {
        const hasAns = ans !== null && ans !== "" && !(Array.isArray(ans) && !ans.length) &&
          !(typeof ans === "object" && !Array.isArray(ans) && ans !== null && !Object.keys(ans).length);
        if (hasAns) answers[q.id] = ans; else delete answers[q.id];
        refreshPalette();
      },
    });
    widgets[q.id] = widget;
    const nav = el("div", { class: "exam-qnav" },
      el("button", { class: "btn ghost sm", disabled: i === 0, onclick: () => show(i - 1) }, "← Anterior"),
      el("span", { style: "flex:1" }),
      i < questions.length - 1
        ? el("button", { class: "btn sm", onclick: () => show(i + 1) }, "Siguiente →")
        : el("button", { class: "btn primary sm", onclick: () => confirmSubmit() }, "Revisar y entregar ✓"),
    );
    const card = el("div", {},
      el("div", { class: "exam-qcard" },
        el("div", { class: "qhead" },
          el("span", { class: "qnum" }, `Pregunta ${i + 1}/${questions.length}`),
          el("span", { class: "badge dim" }, typeLabel(q.type)),
          el("span", { class: "qpts" }, `${q.points} pts`),
        ),
        widget.node,
      ),
      nav,
    );
    widget.node.querySelector(".ex-head .num")?.remove(); // la numeración la da la qhead
    return card;
  });

  function show(i) {
    current = i;
    qArea.innerHTML = "";
    qArea.appendChild(qCards[i]);
    refreshPalette();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function refreshPalette() {
    const n = Object.keys(answers).length;
    answeredEl.textContent = `${n}/${questions.length} respondidas`;
    palBtns.forEach((b, i) => {
      b.classList.toggle("answered", !!answers[questions[i].id]);
      b.classList.toggle("current", i === current);
    });
  }

  function confirmSubmit() {
    const missing = questions.length - Object.keys(answers).length;
    const msg = missing > 0
      ? `Tienes ${missing} pregunta(s) sin responder. ¿Entregar de todos modos? (las sin responder puntúan 0)`
      : "¿Entregar el examen para su corrección?";
    if (confirm(msg)) doSubmit();
  }

  async function doSubmit() {
    if (submitted) return;
    submitted = true;
    if (timerInterval) clearInterval(timerInterval);
    submitBtn.disabled = true;
    submitBtn.textContent = "Corrigiendo…";
    try {
      const result = await api.submitExam(courseId, exam.id, {
        session_id: session.session_id,
        responses: questions.map(q => ({ exercise_id: q.id, answer: answers[q.id] ?? null })),
      });
      renderResults(root, courseId, exam, result);
    } catch (e) {
      if (e.status === 409) {
        // Expirado/invalidado por el SERVIDOR: estado claro, sin reintentos
        root.innerHTML = "";
        root.classList.remove("wide");
        root.appendChild(
          el("div", { class: "exam-intro" },
            el("div", { class: "card", style: "text-align:center;padding:30px" },
              el("div", { style: "font-size:40px" }, "⏰"),
              el("h2", { style: "margin:8px 0" }, "Envío rechazado por el servidor"),
              el("p", { style: "color:var(--text-dim);margin-bottom:16px" }, e.message),
              el("a", { class: "btn primary", href: `#/curso/${courseId}/examen/${exam.id}` },
                "Volver al examen"),
            ),
          ),
        );
        return;
      }
      toast(`Error al entregar: ${e.message}`, "err", 6000);
      submitBtn.disabled = false;
      submitBtn.textContent = "Entregar examen";
      submitted = false;
    }
  }

  show(0);
}

function typeLabel(t) {
  return {
    multiple_choice: "opción única", multiple_select: "selección múltiple",
    fill_blank: "completar", ordering: "ordenar", matching: "asociar",
    free_text: "respuesta libre",
  }[t] || t;
}

// ------------------------------------------------------------------ resultados
function renderResults(root, courseId, exam, result) {
  root.innerHTML = "";
  root.classList.remove("wide");
  const s = result.score;
  window.scrollTo({ top: 0 });

  // Iteración 2: tres estados — pending_review (provisional), reviewed, auto_graded
  const st = s.status || (s.passed === null || s.passed === undefined ? "pending_review" : "auto_graded");
  const pending = st === "pending_review";
  const shownPercent = pending ? (s.provisional_percent ?? s.percent ?? 0) : (s.percent ?? 0);

  const R = 150, stroke = 12, r = (R - stroke) / 2, c = 2 * Math.PI * r;
  const off = c * (1 - shownPercent / 100);
  const color = pending ? "var(--warn)" : (s.passed ? "var(--accent)" : "var(--danger)");

  const scoreRing = el("div", { class: "score-ring" });
  scoreRing.innerHTML = `
    <svg width="${R}" height="${R}" viewBox="0 0 ${R} ${R}">
      <circle cx="${R / 2}" cy="${R / 2}" r="${r}" fill="none" stroke="#1b2431" stroke-width="${stroke}"/>
      <circle cx="${R / 2}" cy="${R / 2}" r="${r}" fill="none" stroke="${color}" stroke-width="${stroke}"
        stroke-linecap="round" stroke-dasharray="${c}" stroke-dashoffset="${off}"/>
    </svg>
    <div class="val"><b>${Math.round(shownPercent)}%</b><span>${pending
      ? `${s.points_earned_auto ?? 0}/${s.auto_points_total ?? 0} pts auto · provisional`
      : `${s.points_earned}/${s.points_total} pts`}</span></div>`;

  const verdictText = pending
    ? "⏳ PENDIENTE DE REVISIÓN HUMANA"
    : s.passed
      ? (st === "reviewed" ? "✅ APROBADO (revisión completada)" : "✅ APROBADO")
      : (st === "reviewed" ? "❌ NO APROBADO (revisión completada)" : "❌ NO APROBADO");

  root.appendChild(
    el("div", { class: "card result-hero" },
      scoreRing,
      el("div", { class: `verdict ${pending ? "pending" : s.passed ? "pass" : "fail"}` }, verdictText),
      el("p", { style: "color:var(--text-dim);margin-top:6px;max-width:560px;margin-inline:auto" }, result.feedback_summary),
      pending ? el("div", { class: "pending-box" },
        el("p", {}, `Tus ${s.pending_review} respuesta(s) libre(s) —por valor de ${s.pending_points_total} pts— esperan la puntuación de un revisor autorizado.`),
        el("p", {}, "El porcentaje mostrado es PROVISIONAL y cubre solo la parte automática. El examen no está aprobado ni reprobado hasta completar la revisión; la cobertura de palabras clave es orientativa y no puntúa por sí sola."),
      ) : null,
      (st === "reviewed" && result.review) ? el("div", { class: "pending-box", style: "border-color:#245c46" },
        el("p", {}, `✍️ Revisado por ${result.review.reviewer} el ${(result.review.reviewed_at || "").slice(0, 16).replace("T", " ")} UTC`),
        result.review.comment ? el("p", {}, `Comentario: ${result.review.comment}`) : null,
      ) : null,
      el("div", { style: "display:flex;gap:10px;justify-content:center;margin-top:16px;flex-wrap:wrap" },
        el("a", { class: "btn ghost", href: `#/curso/${courseId}` }, "← Roadmap"),
        el("button", { class: "btn ghost", onclick: () => location.reload() }, "↺ Reintentar examen"),
      ),
    ),
  );

  // feedback: áreas débiles + recomendaciones
  const fbCard = el("div", { class: "card", style: "margin-top:14px" }, el("h3", {}, "🧭 Diagnóstico y plan de mejora"));
  if (result.weak_tags?.length) {
    fbCard.appendChild(el("div", { style: "font-size:13px;color:var(--text-dim);margin-bottom:4px" }, "Áreas por debajo del 70% de acierto:"));
    fbCard.appendChild(el("div", { class: "weak-grid" },
      ...result.weak_tags.map(w => el("span", { class: "weak-chip" }, `${w.tag} · ${Math.round(w.accuracy * 100)}%`))));
  } else {
    fbCard.appendChild(el("p", { style: "font-size:13.5px;color:var(--accent)" }, "Sin áreas débiles detectadas en este intento. 🎉"));
  }
  if (result.recommendations?.length) {
    fbCard.appendChild(el("ol", { class: "rec-list" }, ...result.recommendations.map(r => el("li", {}, r))));
  }
  root.appendChild(fbCard);

  // exportar reporte (loop de feedback con IA)
  const exportCard = el("div", { class: "card", style: "margin-top:14px" },
    el("h3", {}, "🤖 Feedback profundo con tu IA/mentor"),
    el("p", { style: "font-size:13.5px;color:var(--text-dim)" },
      "Exporta el reporte completo (tus respuestas vs. las correctas, con explicaciones) y pégalo en tu chat con Qwen para recibir un diagnóstico personalizado y ejercicios de refuerzo."),
    el("div", { class: "export-row" },
      el("button", { class: "btn primary sm", onclick: () => copyReport(result.report_markdown) }, "📋 Copiar reporte (Markdown)"),
      el("button", { class: "btn ghost sm", onclick: () => downloadReport(result.report_markdown, `${exam.id}-${result.attempt_id}.md`) }, "⬇ Descargar .md"),
      el("button", { class: "btn ghost sm", onclick: () => showReportModal(result.report_markdown) }, "👁 Ver reporte"),
    ),
  );
  root.appendChild(exportCard);

  // revisión pregunta por pregunta
  root.appendChild(el("h3", { style: "margin:22px 0 10px" }, "🔍 Revisión pregunta por pregunta"));
  const qById = {};
  for (const q of exam.questions || []) qById[q.id] = q;

  for (const [i, r] of result.results.entries()) {
    const q = qById[r.exercise_id] || {};
    const rs = r.review_status || "auto";
    const st = rs === "pending" ? "🕵️"
      : rs === "reviewed" ? "✍️"
      : rs === "not_answered" ? "⬜"
      : r.correct === true ? "✅"
      : r.partial ? "🟡" : "❌";
    const bodyEl = el("div", { class: "review-body", style: "display:none" });
    bodyEl.appendChild(kv("Pregunta", q.prompt || r.exercise_id));
    if (q.context?.body) {
      const pre = el("div", { class: "logblock", "data-label": "contexto" }, q.context.body);
      bodyEl.appendChild(pre);
    }
    bodyEl.appendChild(kvHtml("Tu respuesta", `<span class="yours">${escapeHtml(fmtAny(r.submitted))}</span>`));
    bodyEl.appendChild(kvHtml("Puntaje", `${r.points_earned}/${r.points_total} pts`));
    if (r.feedback) bodyEl.appendChild(kv("Feedback del sistema", r.feedback));
    if (r.explanation) bodyEl.appendChild(kv("Explicación", r.explanation));

    const head = el("div", { class: "review-head", onclick: () => {
      const open = bodyEl.style.display !== "none";
      bodyEl.style.display = open ? "none" : "block";
    } },
      el("span", { class: "st" }, st),
      el("span", {}, `P${i + 1} · ${typeLabel(r.type)}`),
      el("span", { style: "color:var(--text-faint);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:340px" }, q.prompt || ""),
      el("span", { class: "pts" }, `${r.points_earned}/${r.points_total}`),
    );
    root.appendChild(el("div", { class: "review-item" }, head, bodyEl));
  }
}

function kv(k, v) {
  return el("div", { class: "kv" }, el("b", {}, k), el("div", {}, String(v ?? "—")));
}
function kvHtml(k, html) {
  return el("div", { class: "kv" }, el("b", {}, k), el("div", { html }));
}
function fmtAny(v) {
  if (v === null || v === undefined || v === "") return "(sin respuesta)";
  if (Array.isArray(v)) return v.join(", ");
  if (typeof v === "object") return Object.entries(v).map(([k, x]) => `${k}→${x}`).join(", ");
  return String(v);
}

async function copyReport(md) {
  try {
    await navigator.clipboard.writeText(md);
    toast("Reporte copiado al portapapeles ✓ — pégalo en tu chat con Qwen", "info", 4500);
  } catch {
    showReportModal(md);
    toast("No se pudo copiar; se abrió el visor para copiar manualmente", "err");
  }
}

function downloadReport(md, filename) {
  const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
  toast(`Descargado: ${filename}`, "info");
}

function showReportModal(md) {
  const backdrop = el("div", { class: "modal-backdrop", onclick: (e) => { if (e.target === backdrop) backdrop.remove(); } });
  const modal = el("div", { class: "modal" },
    el("header", {}, "📄 Reporte de evaluación (Markdown)",
      el("button", { class: "x", onclick: () => backdrop.remove() }, "×")),
    el("div", { class: "modal-body" },
      el("pre", { class: "report" }, md),
      el("div", { style: "display:flex;gap:10px;margin-top:12px" },
        el("button", { class: "btn primary sm", onclick: () => copyReport(md) }, "📋 Copiar todo"),
      ),
    ),
  );
  backdrop.appendChild(modal);
  document.body.appendChild(backdrop);
}
