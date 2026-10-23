// Cliente HTTP de la API v1. Todo el frontend habla SOLO por aquí.
//
// Iteración 1: la identidad viaja ÚNICAMENTE como Authorization: Bearer <JWT>.
// La cabecera X-User-Id fue eliminada (el backend ya no la lee). En modo
// supabase el token lo gestiona supabase-js (refresh automático vía
// getSession()); en modo local/test es el token del emisor dev.
// Un 401 con razón "expired"/"invalid" limpia la sesión local y redirige a
// #/auth (el usuario vuelve a entrar; nada se pierde en el servidor).

import { getToken, clearInvalidSession, loadAuthConfig } from "./auth.js";

const BASE = "/api/v1";

async function request(path, options = {}) {
  const token = await getToken();
  const res = await fetch(`${BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers || {}),
    },
  });
  let data = null;
  try { data = await res.json(); } catch { /* 204 / vacío */ }
  if (!res.ok) {
    const err = data?.error || {};
    if (res.status === 401 && err.reason !== "missing") {
      // token presente pero rechazado (expirado/inválido): limpiar sesión
      clearInvalidSession();
      if (location.hash !== "#/auth") location.hash = "#/auth";
    }
    const e = new Error(err.message || `Error ${res.status}`);
    e.status = res.status;
    e.code = err.code;
    e.reason = err.reason;
    throw e;
  }
  return data;
}

export const api = {
  // auth/config
  getConfig: () => loadAuthConfig(true),

  // catálogo (público)
  listCourses: () => request("/courses"),
  getCourse: (cid) => request(`/courses/${cid}`),
  getCatalog: (cid) => request(`/courses/${cid}/catalog`),

  // lecciones
  getLesson: (cid, mid, lid) => request(`/courses/${cid}/modules/${mid}/lessons/${lid}`),
  markLessonProgress: (cid, mid, lid, body) =>
    request(`/courses/${cid}/modules/${mid}/lessons/${lid}/progress`, { method: "POST", body: JSON.stringify(body) }),
  attemptExercise: (cid, mid, lid, exId, answer) =>
    request(`/courses/${cid}/modules/${mid}/lessons/${lid}/exercises/${exId}/attempt`, {
      method: "POST", body: JSON.stringify({ answer }),
    }),

  // exámenes
  getExam: (cid, examId) => request(`/courses/${cid}/exams/${examId}`),
  submitExam: (cid, examId, payload) =>
    request(`/courses/${cid}/exams/${examId}/attempts`, { method: "POST", body: JSON.stringify(payload) }),
  getReport: (cid, attemptId, fmt = "markdown") =>
    request(`/courses/${cid}/attempts/${attemptId}/report?format=${fmt}`),

  // progreso (privado)
  dashboard: () => request("/progress"),
  attempts: (limit = 30) => request(`/attempts?limit=${limit}`),
};
