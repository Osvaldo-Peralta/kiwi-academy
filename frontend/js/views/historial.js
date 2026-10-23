// Vista: historial completo de intentos (requiere sesión)
import { api } from "../api.js";
import { getCurrentUser } from "../auth.js";
import { el, fmtDate, toast } from "../util.js";

export async function renderHistorial(root) {
  root.innerHTML = "";
  const user = await getCurrentUser();
  if (!user) {
    root.appendChild(
      el("div", { class: "empty-state" },
        el("div", { class: "big" }, "🔒"),
        el("h2", {}, "El historial es personal"),
        el("p", { style: "margin:8px 0 16px;color:var(--text-dim)" },
          "Inicia sesión para ver tus evaluaciones e informes."),
        el("a", { class: "btn primary", href: "#/auth" }, "Iniciar sesión"),
      ),
    );
    return;
  }
  root.appendChild(
    el("div", { class: "page-head" },
      el("h1", {}, "🕘 Historial de evaluaciones"),
      el("div", { class: "sub" }, "Cada ejercicio y examen que has intentado. Los exámenes incluyen reporte exportable."),
    ),
  );

  const { attempts } = await api.attempts(100);
  if (!attempts.length) {
    root.appendChild(el("div", { class: "empty-state" }, el("div", { class: "big" }, "🌱"),
      "Todavía no hay actividad. Empieza una lección para ver tu historial aquí."));
    return;
  }

  const card = el("div", { class: "card", style: "padding:0;overflow-x:auto" });
  const table = el("table", { class: "hist-table" },
    el("thead", {}, el("tr", {},
      el("th", {}, "Tipo"), el("th", {}, "Referencia"), el("th", {}, "Resultado"),
      el("th", {}, "Puntaje"), el("th", {}, "Fecha"), el("th", {}, ""))),
  );
  const tbody = el("tbody");

  for (const a of attempts) {
    const scope = a.scope || {};
    const isExam = scope.type === "exam";
    const pct = a.score?.percent ?? 0;
    const pendingReview = isExam && a.score?.status === "pending_review";
    const badgeCls = pendingReview ? "warn"
      : isExam ? (a.score?.passed ? "green" : "red")
      : (a.score?.passed ? "green" : pct > 0 ? "warn" : "dim");

    const row = el("tr", {},
      el("td", {}, isExam ? "📝 Examen" : "✏️ Ejercicio"),
      el("td", {},
        el("span", { class: "mono" }, scope.exam_id || scope.lesson_id || ""),
        scope.exercise_id ? el("span", { class: "mono", style: "color:var(--text-faint)" }, ` · ${scope.exercise_id}`) : null,
      ),
      el("td", {}, el("span", { class: `badge ${badgeCls}` },
        pendingReview ? "revisión pendiente"
        : isExam ? (a.score?.passed ? "aprobado" : "no aprobado")
        : (a.score?.passed ? "correcto" : a.score?.pending ? "revisión" : pct > 0 ? "parcial" : "incorrecto"))),
      el("td", {}, el("span", { class: "mono" }, pendingReview ? `${pct}% prov.` : `${pct}% (${a.score?.points_earned ?? 0}/${a.score?.points_total ?? 0})`)),
      el("td", {}, el("span", { class: "mono" }, fmtDate(a.submitted_at))),
      el("td", {}, isExam && scope.course_id
        ? el("button", { class: "btn ghost sm", onclick: () => openReport(scope.course_id, a.id) }, "📄 reporte")
        : null),
    );
    tbody.appendChild(row);
  }
  table.appendChild(tbody);
  card.appendChild(table);
  root.appendChild(card);

  async function openReport(courseId, attemptId) {
    try {
      const { report } = await api.getReport(courseId, attemptId, "markdown");
      const backdrop = el("div", { class: "modal-backdrop", onclick: (e) => { if (e.target === backdrop) backdrop.remove(); } });
      backdrop.appendChild(
        el("div", { class: "modal" },
          el("header", {}, `Reporte ${attemptId}`, el("button", { class: "x", onclick: () => backdrop.remove() }, "×")),
          el("div", { class: "modal-body" },
            el("pre", { class: "report" }, report),
            el("div", { style: "display:flex;gap:10px;margin-top:12px" },
              el("button", { class: "btn primary sm", onclick: async () => {
                try { await navigator.clipboard.writeText(report); toast("Reporte copiado ✓", "info"); }
                catch { toast("No se pudo copiar automáticamente", "err"); }
              } }, "📋 Copiar"),
            ),
          ),
        ),
      );
      document.body.appendChild(backdrop);
    } catch (e) {
      toast(e.message, "err");
    }
  }
}
