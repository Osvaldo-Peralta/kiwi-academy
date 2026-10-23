// Gestión de sesión (Iteración 1).
//
// Modos (informados por GET /api/v1/config):
// - "supabase": sesión real con supabase-js vendorizado (login, registro,
//   invitado anónimo si el proyecto lo permite). Tokens con refresh automático.
// - "local"/"test": emisor dev del backend (POST /api/v1/dev/token). La
//   validación JWT en el servidor es la misma; solo cambia el emisor.
//
// La identidad NUNCA se toma de valores editables por el usuario: el backend
// solo acepta el JWT verificado. El localStorage guarda tokens (no identidades
// de confianza): si el token no vale, el backend lo rechaza igualmente.
//
// Este módulo NO importa api.js (api.js importa de aquí) para evitar ciclos:
// las llamadas propias usan fetch directo.

const CONFIG_URL = "/api/v1/config";
const DEV_TOKEN_KEY = "kiwi.dev.token";
const DEV_USER_KEY = "kiwi.dev.user";

let cfg = null;
let sbClient = null;
let devToken = localStorage.getItem(DEV_TOKEN_KEY) || null;
let devUser = localStorage.getItem(DEV_USER_KEY) || null;
let authChangeCallbacks = [];

export function notifyAuthChange() {
  for (const cb of authChangeCallbacks) {
    try { cb(); } catch (e) { console.error(e); }
  }
}

export function onAuthChange(cb) {
  authChangeCallbacks.push(cb);
  if (sbClient) {
    sbClient.auth.onAuthStateChange(() => cb());
  }
}

export async function loadAuthConfig(force = false) {
  if (cfg && !force) return cfg;
  const res = await fetch(CONFIG_URL);
  if (!res.ok) throw new Error(`No se pudo cargar /api/v1/config (${res.status})`);
  cfg = await res.json();
  if (cfg.auth_mode === "supabase" && cfg.supabase?.url && cfg.supabase?.anon_key
      && window.supabase && !sbClient) {
    sbClient = window.supabase.createClient(cfg.supabase.url, cfg.supabase.anon_key, {
      auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
    });
    sbClient.auth.onAuthStateChange(() => notifyAuthChange());
  }
  return cfg;
}

export async function getAuthMode() {
  return (await loadAuthConfig()).auth_mode;
}

// ---------------------------------------------------------------------------
// Token para la API
// ---------------------------------------------------------------------------

export async function getToken() {
  const c = await loadAuthConfig();
  if (c.auth_mode === "supabase") {
    if (!sbClient) return null;
    const { data } = await sbClient.auth.getSession();
    return data?.session?.access_token || null;
  }
  return devToken; // local / test
}

export async function getCurrentUser() {
  const c = await loadAuthConfig();
  if (c.auth_mode === "supabase") {
    if (!sbClient) return null;
    const { data } = await sbClient.auth.getSession();
    const u = data?.session?.user;
    if (!u) return null;
    return {
      id: u.id,
      label: u.email || (u.is_anonymous ? "invitado" : u.id.slice(0, 8)),
      anonymous: !!u.is_anonymous,
    };
  }
  if (devToken && devUser) return { id: devUser, label: devUser, dev: true };
  return null;
}

// ---------------------------------------------------------------------------
// Acciones de sesión — modo supabase
// ---------------------------------------------------------------------------

function requireSb() {
  if (!sbClient) throw new Error("Cliente Supabase no inicializado (falta URL/anon key en /config)");
  return sbClient;
}

export async function signIn(email, password) {
  const { data, error } = await requireSb().auth.signInWithPassword({ email, password });
  if (error) throw new Error(translateAuthError(error));
  notifyAuthChange();
  return data;
}

export async function signUp(email, password) {
  const { data, error } = await requireSb().auth.signUp({ email, password });
  if (error) throw new Error(translateAuthError(error));
  notifyAuthChange();
  return data;
}

export async function signInGuest() {
  const { data, error } = await requireSb().auth.signInAnonymously();
  if (error) {
    const e = new Error(translateAuthError(error));
    e.code = "anonymous_unavailable";
    throw e;
  }
  notifyAuthChange();
  return data;
}

export async function signOut() {
  if (sbClient) await sbClient.auth.signOut();
  devToken = null; devUser = null;
  localStorage.removeItem(DEV_TOKEN_KEY);
  localStorage.removeItem(DEV_USER_KEY);
  notifyAuthChange();
}

function translateAuthError(error) {
  const msg = String(error?.message || error);
  if (/invalid login credentials/i.test(msg)) return "Credenciales inválidas";
  if (/email not confirmed/i.test(msg)) return "Email sin confirmar (revisa tu bandeja)";
  if (/anonymous sign ?ins? (is|are) disabled|anonymous_sign_ins/i.test(msg) || /anonymous/i.test(msg))
    return "El acceso como invitado no está disponible en este momento. Regístrate para guardar tu progreso.";
  if (/rate limit/i.test(msg)) return "Demasiados intentos; espera unos minutos.";
  if (/password should be/i.test(msg)) return "La contraseña es demasiado corta (mínimo 6 caracteres).";
  if (/already registered|already been registered/i.test(msg)) return "Ese email ya está registrado; inicia sesión.";
  return msg;
}

// ---------------------------------------------------------------------------
// Acciones de sesión — modo local/test (emisor dev del backend)
// ---------------------------------------------------------------------------

export async function devSignIn(userId) {
  const c = await loadAuthConfig();
  if (!c.dev_token_url) throw new Error("El modo actual no tiene emisor dev");
  const id = userId.startsWith("dev-") ? userId : `dev-${userId}`;
  const res = await fetch(c.dev_token_url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: id }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body?.error?.message || `El emisor dev rechazó la solicitud (${res.status})`);
  }
  const data = await res.json();
  devToken = data.access_token;
  devUser = data.user_id;
  localStorage.setItem(DEV_TOKEN_KEY, devToken);
  localStorage.setItem(DEV_USER_KEY, devUser);
  notifyAuthChange();
  return data;
}

// Limpieza de sesión cuando el backend rechaza nuestro token (inválido/
// expirado/irrecuperable). H-01: cubre AMBOS modos —
//   local/test: olvida el token dev (memoria + localStorage)
//   supabase:   fuerza el cierre LOCAL de la sesión en supabase-js
//               (scope "local": el token ya es inválido, no tiene sentido
//               intentar revocarlo en el servidor) — sin esto, getSession()
//               seguiría devolviendo la sesión muerta y la interfaz la
//               mostraría como activa.
// Al final notifica a los suscriptores (chip de sesión, router) para que la
// UI refleje inmediatamente el estado real.
export async function clearInvalidSession() {
  devToken = null;
  devUser = null;
  localStorage.removeItem(DEV_TOKEN_KEY);
  localStorage.removeItem(DEV_USER_KEY);
  if (sbClient) {
    try {
      await sbClient.auth.signOut({ scope: "local" });
    } catch (e) {
      console.warn("clearInvalidSession: signOut local falló", e);
    }
  }
  notifyAuthChange();
}

// Hook exclusivo para las pruebas smoke: fuerza el token dev en memoria,
// permitiendo simular una sesión corrupta sin tocar la lógica de producción.
export function _setDevTokenForTests(tok) {
  devToken = tok;
}
