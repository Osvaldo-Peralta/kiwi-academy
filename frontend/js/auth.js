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

// ── Recuperación segura de sesión persistida (H-01, revisión 2) ──────────
// Basada en el código REAL del bundle vendorizado (supabase-js 2.118.0):
//  - storageKey por defecto: `sb-<ref>-auth-token` (ref = primer segmento del
//    hostname de la URL del proyecto; visto en el constructor del cliente).
//  - getSession() -> _useSession() -> __loadSession() -> storage.getItem():
//    la sesión se lee del storage EN CADA llamada (no hay caché en memoria
//    del cliente que sobreviva al borrado).
//  - _removeSession() borra storageKey, storageKey+"-user" y derivados
//    (p. ej. "-code-verifier").
//  - _signOut() puede devolver {error} SIN haber eliminado la sesión
//    (early-return cuando getSession falla) y, aun con scope:"local", hace
//    POST /logout?scope=local si hay access_token (errores de red -> devuelve
//    {error} aunque en esa ruta sí llama a _removeSession).
// Por tanto: si signOut no confirma éxito (error devuelto o excepción), se
// purgan todas las claves localStorage con el prefijo del storageKey. Tras la
// purga, getSession() devuelve sesión nula de forma determinista (lee del
// storage), dejando la UI en estado seguro sin reintentos ni bucles.
function purgePersistedSupabaseSession() {
  let prefix = null;
  try {
    const url = cfg?.supabase?.url;
    if (url) {
      const ref = new URL(url).hostname.split(".")[0];
      if (ref) prefix = `sb-${ref}-auth-token`;
    }
  } catch { /* URL inválida: se usa el patrón genérico */ }
  const removed = [];
  try {
    for (let i = localStorage.length - 1; i >= 0; i--) {
      const k = localStorage.key(i);
      if (!k) continue;
      const hit = prefix
        ? k.startsWith(prefix)
        : (k.startsWith("sb-") && k.includes("-auth-token"));
      if (hit) {
        localStorage.removeItem(k);
        removed.push(k);
      }
    }
  } catch { /* almacenamiento no disponible: no hay nada que purgar */ }
  return removed;
}

// Limpieza de sesión cuando el backend rechaza nuestro token (inválido/
// expirado/irrecuperable). H-01: cubre AMBOS modos —
//   local/test: olvida el token dev (memoria + localStorage)
//   supabase:   signOut({scope:"local"}) verificando el RESULTADO (la librería
//               puede devolver {error} sin lanzar excepción); si la limpieza
//               no queda confirmada, purga el storage persistido (ver arriba).
//               Nunca se notifica "sesión limpia" sin confirmación o purga.
// Al final notifica a los suscriptores (chip de sesión, router) para que la
// UI refleje inmediatamente el estado real.
export async function clearInvalidSession() {
  devToken = null;
  devUser = null;
  localStorage.removeItem(DEV_TOKEN_KEY);
  localStorage.removeItem(DEV_USER_KEY);
  if (sbClient) {
    let confirmed = false;
    try {
      const res = await sbClient.auth.signOut({ scope: "local" });
      // Contrato vendorizado (2.118.0): signOut devuelve {error|null} y solo
      // a veces lanza. Éxito confirmado == objeto devuelto sin .error.
      confirmed = !!res && !res.error;
      if (!confirmed) {
        // Solo el nombre/tipo del error: nunca el objeto completo (podría
        // contener datos de sesión). Sin secretos ni PII en logs.
        console.warn(
          "auth: signOut(local) devolvió error; purgando sesión persistida.",
          res?.error?.name || "(sin nombre)",
        );
      }
    } catch (e) {
      console.warn(
        "auth: signOut(local) lanzó excepción; purgando sesión persistida.",
        e?.name || "Error",
      );
    }
    if (!confirmed) purgePersistedSupabaseSession();
  }
  notifyAuthChange();
}

// Hook exclusivo para las pruebas smoke: fuerza el token dev en memoria,
// permitiendo simular una sesión corrupta sin tocar la lógica de producción.
export function _setDevTokenForTests(tok) {
  devToken = tok;
}

// Hook exclusivo para las pruebas smoke: inyecta/restaura el cliente
// supabase (permite simular resultados exitosos y fallidos de signOut).
export function _setSbClientForTests(client) {
  sbClient = client;
}
