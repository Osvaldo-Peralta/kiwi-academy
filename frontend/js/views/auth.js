// Vista: #/auth — entrada de sesión (supabase) o selector dev (local/test).
import { el, toast } from "../util.js";
import { getAuthMode, signIn, signUp, signInGuest, devSignIn, getCurrentUser } from "../auth.js";

export async function renderAuth(root) {
  root.innerHTML = "";
  const mode = await getAuthMode();
  const user = await getCurrentUser();

  if (user) {
    root.appendChild(
      el("div", { class: "exam-intro" },
        el("div", { class: "card", style: "text-align:center;padding:30px" },
          el("div", { style: "font-size:40px" }, "✅"),
          el("h2", { style: "margin:8px 0" }, `Sesión activa: ${user.label}`),
          el("p", { style: "color:var(--text-dim);font-size:13.5px" }, "Ya tienes identidad verificada."),
          el("div", { style: "display:flex;gap:10px;justify-content:center;margin-top:16px" },
            el("a", { class: "btn primary", href: "#/" }, "Ir al panel"),
          ),
        ),
      ),
    );
    return;
  }

  if (mode === "supabase") renderSupabaseAuth(root);
  else renderDevAuth(root, mode);
}

// ---------------------------------------------------------------------------
function renderSupabaseAuth(root) {
  let tab = "login";
  const card = el("div", { class: "card" });
  const wrap = el("div", { class: "exam-intro" },
    el("div", { class: "page-head", style: "text-align:center" },
      el("div", { style: "font-size:40px" }, "🥝"),
      el("h1", {}, "Tu cuenta en Kiwi Academy"),
      el("div", { class: "sub" }, "Inicia sesión para guardar tu progreso, ejercicios y exámenes."),
    ),
    card,
  );
  root.appendChild(wrap);

  function paint() {
    card.innerHTML = "";
    const tabs = el("div", { class: "tabs", style: "margin-bottom:16px" },
      el("button", { class: tab === "login" ? "active" : "", onclick: () => { tab = "login"; paint(); } }, "Iniciar sesión"),
      el("button", { class: tab === "signup" ? "active" : "", onclick: () => { tab = "signup"; paint(); } }, "Crear cuenta"),
    );
    const email = el("input", { class: "txt", type: "email", placeholder: "tu@email.com", autocomplete: "email" });
    const pass = el("input", { class: "txt", type: "password", placeholder: "contraseña (mín. 6 caracteres)", autocomplete: tab === "login" ? "current-password" : "new-password" });
    const errBox = el("div", { class: "auth-error", style: "display:none" });
    const submit = el("button", { class: "btn primary lg", style: "width:100%;margin-top:12px" },
      tab === "login" ? "Entrar" : "Registrarme");

    submit.addEventListener("click", async () => {
      submit.disabled = true; errBox.style.display = "none";
      try {
        if (tab === "login") await signIn(email.value.trim(), pass.value);
        else await signUp(email.value.trim(), pass.value);
        toast(tab === "login" ? "Sesión iniciada ✓" : "Cuenta creada ✓");
        location.hash = "#/";
      } catch (e) {
        errBox.textContent = e.message;
        errBox.style.display = "block";
        submit.disabled = false;
      }
    });
    for (const input of [email, pass]) {
      input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") submit.click(); });
    }

    const guest = el("button", { class: "btn ghost", style: "width:100%;margin-top:10px" },
      "👤 Continuar como invitado (anónimo)");
    const guestNote = el("p", { class: "auth-note" },
      "El progreso de invitado se conserva al vincular la cuenta más adelante. Si el acceso anónimo no está disponible, podrás registrarte o explorar el contenido público en modo lectura.");

    guest.addEventListener("click", async () => {
      guest.disabled = true; errBox.style.display = "none";
      try {
        await signInGuest();
        toast("Sesión anónima iniciada ✓");
        location.hash = "#/";
      } catch (e) {
        errBox.textContent = e.message;
        errBox.style.display = "block";
        guest.disabled = false;
      }
    });

    card.append(tabs,
      el("label", { class: "auth-label" }, "Email"), email,
      el("label", { class: "auth-label" }, "Contraseña"), pass,
      errBox, submit,
      el("div", { style: "border-top:1px solid var(--border);margin:16px 0 12px" }),
      guest, guestNote,
    );
  }
  paint();
}

// ---------------------------------------------------------------------------
function renderDevAuth(root, mode) {
  const userId = el("input", { class: "txt", type: "text", value: "dev-osvaldo", placeholder: "dev-tu-nombre" });
  const errBox = el("div", { class: "auth-error", style: "display:none" });
  const btn = el("button", { class: "btn primary lg", style: "width:100%;margin-top:12px" }, "Entrar en modo desarrollo");
  btn.addEventListener("click", async () => {
    btn.disabled = true; errBox.style.display = "none";
    try {
      await devSignIn(userId.value.trim() || "dev-osvaldo");
      toast("Sesión dev iniciada ✓");
      location.hash = "#/";
    } catch (e) {
      errBox.textContent = e.message;
      errBox.style.display = "block";
      btn.disabled = false;
    }
  });
  userId.addEventListener("keydown", (e) => { if (e.key === "Enter") btn.click(); });

  root.appendChild(
    el("div", { class: "exam-intro" },
      el("div", { class: "page-head", style: "text-align:center" },
        el("div", { style: "font-size:40px" }, "🛠️"),
        el("h1", {}, "Modo desarrollo"),
        el("div", { class: "sub" },
          `KIWI_AUTH_MODE=${mode} — el backend emite un JWT de desarrollo (misma verificación que en producción, emisor local). Usuarios en el namespace dev-*.`),
      ),
      el("div", { class: "card" },
        el("label", { class: "auth-label" }, "Usuario de desarrollo"),
        userId, errBox, btn,
        el("p", { class: "auth-note" },
          "En producción (modo supabase) este emisor no existe: la identidad la emite Supabase Auth y el backend la verifica con JWKS/HS256."),
      ),
    ),
  );
}
