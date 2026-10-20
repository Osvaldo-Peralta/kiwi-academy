// Vista: lección (teoría + ejemplos + ejercicios con feedback inmediato)
import { api } from "../api.js";
import { el, escapeHtml, toast, KIND_BADGE, KIND_LABEL } from "../util.js";
import { renderMarkdown, contentBlockHtml } from "../markdown.js";
import { createExerciseWidget } from "../components/exercises.js";

export async function renderLesson(root, courseId, moduleId, lessonId) {
  root.innerHTML = "";
  let lesson;
  try {
    lesson = await api.getLesson(courseId, moduleId, lessonId);
  } catch (e) {
    root.appendChild(el("div", { class: "empty-state" }, el("div", { class: "big" }, "🚫"), e.message));
    return;
  }

  const prog = lesson.progress || {};
  const total = lesson.exercise_count || 0;
  let done = prog.exercises_done
    ?? (prog.exercises_mastered || 0) + (prog.reviewed_exercises?.length || 0);

  const isDraft = lesson.status === "draft";

  // header
  root.appendChild(
    el("div", { class: "page-head" },
      el("div", { class: "crumbs" },
        el("a", { href: "#/" }, "panel"), " / ",
        el("a", { href: `#/curso/${courseId}` }, courseId), " / ",
        moduleId, " / ", lessonId),
      el("div", { class: "lesson-head" },
        el("div", { class: "grow" },
          el("h1", {}, lesson.title),
          el("div", { class: "sub", style: "margin-top:4px;display:flex;gap:8px;align-items:center;flex-wrap:wrap" },
            el("span", { class: `badge ${KIND_BADGE[lesson.kind] || "dim"}` }, KIND_LABEL[lesson.kind] || lesson.kind),
            el("span", { style: "color:var(--text-dim);font-size:12.5px" }, `~${lesson.estimated_minutes} min`),
            isDraft ? el("span", { class: "badge warn" }, "en construcción") : null,
          ),
        ),
      ),
    ),
  );

  if (lesson.summary) {
    root.appendChild(el("p", { style: "color:var(--text-dim);font-size:14px;margin:-10px 0 16px" }, lesson.summary));
  }

  if (lesson.objectives?.length) {
    root.appendChild(
      el("div", { class: "card", style: "margin-bottom:18px" },
        el("h3", {}, "🎯 Al terminar esta lección sabrás:"),
        el("ul", { style: "margin:8px 0 0 20px;font-size:13.5px;color:var(--text-dim)" },
          ...lesson.objectives.map(o => el("li", { style: "margin:4px 0" }, o))),
      ),
    );
  }

  if (isDraft) {
    const theoryHtml = (lesson.theory || []).map(b => contentBlockHtml(b)).join("<hr style='border:none;border-top:1px solid var(--border);margin:18px 0'>");
    root.appendChild(el("div", { class: "card" }, el("div", { html: theoryHtml })));
    return;
  }

  // barra de dominio
  const masteryWrap = el("div", { class: "mastery-line" },
    el("span", {}, "Dominio de la lección"),
    el("div", { class: "pbar" }, el("i", { style: `width:${masteryPct()}%` })),
    el("span", { style: "font-family:var(--mono)" }, `${done}/${total} ejercicios`),
    prog.theory_completed ? el("span", { class: "badge green" }, "✓ teoría") : el("span", { class: "badge dim" }, "teoría pendiente"),
  );
  root.appendChild(masteryWrap);

  // tabs
  let activeTab = "teoria";
  const tabsEl = el("div", { class: "tabs" });
  const contentEl = el("div");

  const tabs = [
    { id: "teoria", label: `📖 Teoría (${(lesson.theory || []).length})` },
    { id: "ejemplos", label: `🔬 Ejemplos (${(lesson.examples || []).length})` },
    { id: "ejercicios", label: `✏️ Ejercicios (${total})` },
  ];
  const buttons = {};
  for (const t of tabs) {
    buttons[t.id] = el("button", { onclick: () => setTab(t.id) }, t.label);
    tabsEl.appendChild(buttons[t.id]);
  }
  root.appendChild(tabsEl);
  root.appendChild(contentEl);

  // banner de finalización
  const banner = el("div", { class: "completion-banner", style: "display:none" },
    el("div", { class: "big" }, "🏅"),
    el("div", {},
      el("h3", {}, "¡Lección completada!"),
      el("p", {}, "Teoría leída y todos los ejercicios dominados. Cuando termines las 3 lecciones de la fase, presenta el examen."),
    ),
    el("a", { class: "btn primary", style: "margin-left:auto", href: `#/curso/${courseId}` }, "Volver al roadmap"),
  );
  root.appendChild(banner);

  function masteryPct() { return total ? Math.round(100 * done / total) : 0; }

  function refreshMastery() {
    masteryWrap.querySelector(".pbar > i").style.width = `${masteryPct()}%`;
    masteryWrap.querySelector("span[style*='mono']").textContent = `${done}/${total} ejercicios`;
    const complete = done >= total && total > 0 && prog.theory_completed;
    banner.style.display = complete ? "flex" : "none";
  }

  function setTab(id) {
    activeTab = id;
    for (const [k, b] of Object.entries(buttons)) b.classList.toggle("active", k === id);
    contentEl.innerHTML = "";
    if (id === "teoria") renderTheory();
    else if (id === "ejemplos") renderExamples();
    else renderExercises();
  }

  // -------------------------------------------------- teoría
  function renderTheory() {
    if (lesson.theory.length > 2) {
      contentEl.appendChild(el("nav", { class: "theory-nav" },
        ...lesson.theory.map((b, i) => el("a", { href: `#sec-${b.id || i}`, onclick: (e) => {
          e.preventDefault();
          document.getElementById(`sec-${b.id || i}`)?.scrollIntoView({ behavior: "smooth" });
        } }, b.section || `Sección ${i + 1}`)),
      ));
    }
    lesson.theory.forEach((b, i) => {
      contentEl.appendChild(
        el("section", { class: "section-block", id: `sec-${b.id || i}` },
          b.section ? el("h2", {}, b.section) : null,
          el("div", { html: contentBlockHtml(b) }),
        ),
      );
    });
    contentEl.appendChild(
      el("div", { style: "margin-top:22px;display:flex;gap:12px;align-items:center;flex-wrap:wrap" },
        el("button", {
          class: "btn primary", disabled: prog.theory_completed,
          onclick: async (ev) => {
            ev.target.disabled = true;
            prog.theory_completed = true;
            await api.markLessonProgress(courseId, moduleId, lessonId, { theory_completed: true });
            toast("Teoría marcada como leída ✓");
            refreshMastery();
          },
        }, prog.theory_completed ? "✓ Teoría completada" : "Marcar teoría como leída"),
        el("button", { class: "btn ghost", onclick: () => setTab("ejemplos") }, "Ver ejemplos →"),
      ),
    );
    refreshMastery();
  }

  // -------------------------------------------------- ejemplos
  function renderExamples() {
    if (!lesson.examples?.length) {
      contentEl.appendChild(el("p", { style: "color:var(--text-dim)" }, "Esta lección aún no tiene ejemplos publicados."));
      return;
    }
    lesson.examples.forEach((b, i) => {
      contentEl.appendChild(
        el("section", { class: "section-block" },
          el("h2", {}, b.section || `Ejemplo ${i + 1}`),
          el("div", { html: contentBlockHtml(b) }),
        ),
      );
    });
    contentEl.appendChild(
      el("div", { style: "margin-top:22px;display:flex;gap:12px" },
        el("button", {
          class: "btn primary", disabled: prog.examples_viewed,
          onclick: async (ev) => {
            ev.target.disabled = true;
            prog.examples_viewed = true;
            await api.markLessonProgress(courseId, moduleId, lessonId, { examples_viewed: true });
            toast("Ejemplos marcados como vistos ✓");
          },
        }, prog.examples_viewed ? "✓ Ejemplos vistos" : "Marcar ejemplos como vistos"),
        el("button", { class: "btn ghost", onclick: () => setTab("ejercicios") }, "Ir a los ejercicios →"),
      ),
    );
  }

  // -------------------------------------------------- ejercicios
  function renderExercises() {
    if (!lesson.exercises?.length) {
      contentEl.appendChild(el("p", { style: "color:var(--text-dim)" }, "Sin ejercicios todavía."));
      return;
    }
    contentEl.appendChild(el("p", { style: "color:var(--text-dim);font-size:13.5px;margin-bottom:14px" },
      "Modo práctica: comprueba cada ejercicio y recibe feedback inmediato. Puedes reintentar tantas veces como quieras — un ejercicio cuenta como dominado cuando lo aciertas."));

    const doneSet = new Set([...(prog.mastered_exercises || []), ...(prog.reviewed_exercises || [])]);

    lesson.exercises.forEach((ex) => {
      const widget = createExerciseWidget(ex, { mode: "practice" });
      if (doneSet.has(ex.id)) widget.setSolved(true);

      const actions = el("div", { class: "ex-actions" });
      const checkBtn = el("button", { class: "btn primary sm", onclick: () => check() }, "Comprobar");
      const retryBtn = el("button", { class: "btn ghost sm", style: "display:none", onclick: () => { widget.reset(); retryBtn.style.display = "none"; checkBtn.disabled = false; } }, "↺ Reintentar");
      actions.append(checkBtn, retryBtn,
        el("span", { class: "spacer" }),
        ex.hint ? el("details", { style: "font-size:12.5px;color:var(--text-dim)" },
          el("summary", { style: "cursor:pointer" }, "💡 pista"),
          el("span", {}, ex.hint)) : null,
      );
      widget.node.appendChild(actions);
      contentEl.appendChild(widget.node);

      async function check() {
        const answer = widget.getAnswer();
        if (answer === null || answer === "" || (Array.isArray(answer) && !answer.length) ||
            (typeof answer === "object" && !Array.isArray(answer) && !Object.keys(answer).length)) {
          toast("Responde algo antes de comprobar", "err"); return;
        }
        checkBtn.disabled = true;
        let res = null;
        try {
          res = await api.attemptExercise(courseId, moduleId, lessonId, ex.id, answer);
          widget.showResult(res);
          if (res.correct) {
            if (!doneSet.has(ex.id)) { doneSet.add(ex.id); done++; }
            toast(`+${res.points_earned} pts — ejercicio dominado ✓`);
          } else if (res.correct === false) {
            retryBtn.style.display = "inline-flex";
          } else {
            // free_text: queda respondida (revisión vía reporte); permite re-enviar
            if (!doneSet.has(ex.id)) { doneSet.add(ex.id); done++; }
            retryBtn.style.display = "inline-flex";
            toast("Respuesta registrada para revisión", "info");
          }
          // sincroniza progreso del servidor
          if (res.lesson_progress) {
            done = Math.max(done, res.lesson_progress.exercises_done ?? 0);
            prog.theory_completed = res.lesson_progress.theory_completed;
          }
          refreshMastery();
        } catch (e) {
          toast(e.message, "err");
        } finally {
          // correcto => se bloquea (ya dominado); incorrecto => se podrá reintentar
          checkBtn.disabled = !!(res && res.correct);
        }
      }
    });
    refreshMastery();
  }

  setTab("teoria");
}
