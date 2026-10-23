// Widgets interactivos por tipo de ejercicio.
// createExerciseWidget(ex, opts) -> { node, getAnswer, showResult, reset }
//
// opts.mode: "practice" (feedback inmediato por ejercicio) | "exam" (sin feedback)
// opts.onChange(answer): callback cada vez que cambia la respuesta (modo examen)

import { el, escapeHtml, shuffle } from "../util.js";
import { contentBlockHtml } from "../markdown.js";

export function createExerciseWidget(ex, opts = {}) {
  const mode = opts.mode || "practice";
  const state = {}; // respuesta actual por tipo
  let feedbackNode = null;

  const body = el("div", { class: "ex-body" });

  // contexto (log / código) si existe
  if (ex.context && ex.context.body) {
    const ctx = el("div");
    ctx.innerHTML = contentBlockHtml(ex.context);
    body.appendChild(ctx);
  }

  // texto auxiliar (fill_blank multi-blank)
  if (ex.text) {
    body.appendChild(el("p", { class: "md", style: "margin-bottom:10px", html: `<em>${escapeHtml(ex.text)}</em>` }));
  }

  const inputArea = el("div");
  body.appendChild(inputArea);

  switch (ex.type) {
    case "multiple_choice": buildChoice(inputArea, ex, state, false, opts); break;
    case "multiple_select": buildChoice(inputArea, ex, state, true, opts); break;
    case "fill_blank": buildFillBlank(inputArea, ex, state, opts); break;
    case "ordering": buildOrdering(inputArea, ex, state, opts); break;
    case "matching": buildMatching(inputArea, ex, state, opts); break;
    case "free_text": buildFreeText(inputArea, ex, state, opts); break;
    default: inputArea.appendChild(el("p", {}, `Tipo de ejercicio no soportado: ${ex.type}`));
  }

  const node = el("div", { class: "ex-card", id: `ex-${ex.id}` },
    el("div", { class: "ex-head" },
      el("span", { class: "num" }, `#${ex.id.split("-").pop() || ex.id}`),
      el("span", { class: "prompt" }, ex.prompt),
      el("span", { class: "tags" },
        el("span", { class: "badge dim" }, `${ex.points} pts`),
        ...(ex.tags || []).slice(0, 3).map(t => el("span", { class: "tag" }, t)),
      ),
    ),
    body,
  );

  return {
    node,
    getAnswer: () => state.answer ?? null,
    showResult(result) {
      clearFeedback();
      const cls = result.correct === true ? "ok" : result.correct === false ? "bad" : "pending";
      const icon = result.correct === true ? "✅" : result.correct === false ? "❌" : "🕵️";
      const title = result.correct === true ? "¡Correcto!"
        : result.correct === false ? "Incorrecto" : "Respuesta registrada — revisión pendiente";
      const parts = [
        el("div", { class: "fb-title" }, `${icon} ${title}`),
        result.feedback ? el("div", {}, result.feedback) : null,
        result.explanation ? el("div", { class: "fb-exp" }, el("strong", {}, "Por qué: "), result.explanation) : null,
        (result.correct === false && result.hint) ? el("div", { class: "fb-exp" }, el("strong", {}, "Pista: "), result.hint) : null,
      ];
      feedbackNode = el("div", { class: `fb ${cls}` }, ...parts.filter(Boolean));
      body.appendChild(feedbackNode);
      if (result.correct === true) node.classList.add("mastered");
    },
    reset() {
      clearFeedback();
      node.classList.remove("mastered");
    },
    clearFeedback,
    setSolved(locked) { if (locked) node.classList.add("mastered"); },
  };

  function clearFeedback() {
    if (feedbackNode) { feedbackNode.remove(); feedbackNode = null; }
  }
}

function notify(opts, state) {
  if (opts.onChange) opts.onChange(state.answer ?? null);
}

// ---------------------------------------------------------------- choice(s)
function buildChoice(area, ex, state, multi, opts) {
  state.answer = multi ? [] : null;
  const options = mode_shuffle(ex.options || []);
  const list = el("div", { class: "opt-list" });
  const optNodes = new Map();

  for (const o of options) {
    const mark = el("span", { class: "mark" }, multi ? "✓" : "●");
    const node = el("div", { class: `opt${multi ? " multi" : ""}`, role: multi ? "checkbox" : "radio" },
      mark,
      el("span", { class: "oid" }, `${o.id})`),
      el("span", {}, o.text),
    );
    node.addEventListener("click", () => {
      if (multi) {
        const arr = state.answer;
        const idx = arr.indexOf(o.id);
        if (idx >= 0) { arr.splice(idx, 1); node.classList.remove("selected"); }
        else { arr.push(o.id); node.classList.add("selected"); }
        state.answer = [...arr];
      } else {
        state.answer = o.id;
        for (const [, n] of optNodes) n.classList.remove("selected");
        node.classList.add("selected");
      }
      notify(opts, state);
    });
    optNodes.set(o.id, node);
    list.appendChild(node);
  }
  area.appendChild(list);
}

function mode_shuffle(options) {
  // baraja en cliente para variar el orden entre intentos
  return shuffle(options);
}

// ---------------------------------------------------------------- fill_blank
function buildFillBlank(area, ex, state, opts) {
  const blanks = countBlanks(ex);
  state.answer = blanks === 1 ? "" : new Array(blanks).fill("");
  const inputs = [];
  for (let b = 1; b <= blanks; b++) {
    const input = el("input", { class: "txt", type: "text", placeholder: blanks > 1 ? `espacio ${b}` : "tu respuesta…", autocomplete: "off", spellcheck: "false" });
    input.addEventListener("input", () => {
      inputs[b - 1] = input.value;
      state.answer = blanks === 1 ? input.value : [...inputs];
      notify(opts, state);
    });
    input.addEventListener("keydown", (e) => { if (e.key === "Enter" && opts.onEnter) opts.onEnter(); });
    inputs.push("");
    area.appendChild(el("div", { class: "blank-row" },
      blanks > 1 ? el("label", {}, `[${b}]`) : null, input));
  }
}

function countBlanks(ex) {
  if (ex.text) {
    const m = ex.text.match(/\[___(\d+)\]/g);
    if (m) return Math.max(...m.map(x => +x.match(/\d+/)[0]));
  }
  return 1;
}

// ---------------------------------------------------------------- ordering
function buildOrdering(area, ex, state, opts) {
  const items = shuffle(ex.items || []);
  state.answer = items.map(i => i.id);
  const list = el("div", { class: "order-list" });
  let dragSrc = null;

  const render = () => {
    list.innerHTML = "";
    state.answer.forEach((id, idx) => {
      const item = items.find(i => i.id === id);
      const node = el("div", { class: "order-item", draggable: "true" },
        el("span", { class: "grip" }, "⠿"),
        el("span", { class: "pos" }, `${idx + 1}.`),
        el("span", {}, item ? item.text : id),
        el("span", { class: "mv" },
          el("button", { title: "subir", onclick: (e) => { e.stopPropagation(); move(idx, -1); } }, "▲"),
          el("button", { title: "bajar", onclick: (e) => { e.stopPropagation(); move(idx, 1); } }, "▼"),
        ),
      );
      node.addEventListener("dragstart", () => { dragSrc = idx; node.classList.add("dragging"); });
      node.addEventListener("dragend", () => node.classList.remove("dragging"));
      node.addEventListener("dragover", (e) => e.preventDefault());
      node.addEventListener("drop", (e) => {
        e.preventDefault();
        if (dragSrc === null || dragSrc === idx) return;
        const [moved] = state.answer.splice(dragSrc, 1);
        state.answer.splice(idx, 0, moved);
        dragSrc = null;
        render(); notify(opts, state);
      });
      list.appendChild(node);
    });
  };

  const move = (idx, dir) => {
    const j = idx + dir;
    if (j < 0 || j >= state.answer.length) return;
    [state.answer[idx], state.answer[j]] = [state.answer[j], state.answer[idx]];
    render(); notify(opts, state);
  };

  render();
  area.appendChild(list);
}

// ---------------------------------------------------------------- matching
function buildMatching(area, ex, state, opts) {
  const lefts = ex.left_items || [];
  const rights = ex.right_items || [];
  state.answer = {};

  for (const l of lefts) {
    const select = el("select", {},
      el("option", { value: "" }, "— elige —"),
      ...rights.map(r => el("option", { value: r.id }, r.text)),
    );
    select.addEventListener("change", () => {
      if (select.value) state.answer[l.id] = select.value;
      else delete state.answer[l.id];
      notify(opts, state);
    });
    area.appendChild(el("div", { class: "match-row" },
      el("span", { class: "left" }, l.text),
      el("span", { class: "arrow" }, "→"),
      select,
    ));
  }
}

// ---------------------------------------------------------------- free_text
function buildFreeText(area, ex, state, opts) {
  state.answer = "";
  const ta = el("textarea", { class: "txt", placeholder: "Escribe tu análisis aquí… En exámenes lo puntúa un revisor humano (la cobertura de palabras clave es solo orientativa y no suma puntos automáticos)." });
  const counter = el("span", { style: "font-size:11px;color:var(--text-faint);font-family:var(--mono)" }, "0 caracteres");
  ta.addEventListener("input", () => {
    state.answer = ta.value;
    counter.textContent = `${ta.value.length} caracteres`;
    notify(opts, state);
  });
  area.appendChild(ta);
  area.appendChild(el("div", { style: "margin-top:6px" }, counter));
}
