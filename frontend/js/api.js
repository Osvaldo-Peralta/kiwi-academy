// Cliente HTTP de la API v1. Todo el frontend habla SOLO por aquí:
// mañana se cambia la URL base (despliegue) o se añade auth en un solo sitio.

const BASE = "/api/v1";

// Usuario local. Cuando haya multiusuario real, esto sale de un login.
export const USER_ID = localStorage.getItem("kiwi.userId") || "default";

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-User-Id": USER_ID,
      ...(options.headers || {}),
    },
  });
  let data = null;
  try { data = await res.json(); } catch { /* 204 / vacío */ }
  if (!res.ok) {
    const msg = data?.error?.message || `Error ${res.status}`;
    throw new Error(msg);
  }
  return data;
}

export const api = {
  // catálogo
  listCourses: () => request("/courses"),
  getCourse: (cid) => request(`/courses/${cid}`),

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

  // progreso
  dashboard: () => request("/progress"),
  attempts: (limit = 30) => request(`/attempts?limit=${limit}`),
};
