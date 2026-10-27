/* Shared same-origin account client. No credentials or session tokens in storage. */
(() => {
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) location.reload();
  });
  const byId = (id) => document.getElementById(id);
  const el = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const eye =
    '<svg viewBox="0 0 24 24" class="icon" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/><path class="eye-slash" d="m3 3 18 18"/></svg>';
  const person =
    '<svg viewBox="0 0 24 24" class="icon" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><circle cx="12" cy="8" r="3.5"/><path d="M5 21v-2a7 7 0 0 1 14 0v2"/></svg>';
  let activeUser = null,
    mode = "login",
    challenge = null,
    resendAt = 0;
  let profileData = null;
  const action = (label, handler, className = "text-button") => {
    const button = el("button", label, className);
    button.type = "button";
    button.onclick = handler;
    return button;
  };
  async function request(path, options = {}) {
    const response = await fetch(path, {
      credentials: "same-origin",
      ...options,
      headers: {
        "X-Requested-With": "tekSER",
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...options.headers,
      },
    });
    let data;
    try {
      data = await response.json();
    } catch {
      throw new Error("Серверден жауап алынбады.");
    }
    if (!response.ok) {
      const detail = data.detail;
      const error = new Error(
        typeof detail === "string"
          ? detail
          : detail?.message || "Сұрауды орындау мүмкін болмады.",
      );
      error.fields = detail?.fields || {};
      error.status = response.status;
      throw error;
    }
    return data;
  }
  const post = (path, data = {}, method = "POST") =>
    request(path, { method, body: JSON.stringify(data) });
  window.accountAPI = request;
  function changeUser(user) {
    const previous = sessionStorage.getItem("tekser-account-id");
    if (previous !== String(user?.id || ""))
      sessionStorage.removeItem("essay-result-id");
    if (user) sessionStorage.setItem("tekser-account-id", String(user.id));
    else sessionStorage.removeItem("tekser-account-id");
    activeUser = user;
    window.tekserUser = user;
    renderMenu();
  }
  function clearPrivateState() {
    [
      "essay-result-id",
      "tekser-draft",
      "tekser-challenge",
      "tekser-account-id",
    ].forEach((key) => sessionStorage.removeItem(key));
  }
  function renderMenu() {
    const root = byId("account-nav");
    if (!root) return;
    root.replaceChildren();
    if (!activeUser) {
      const link = el("a", undefined, "avatar-button");
      link.href = "/auth";
      link.setAttribute("aria-label", "Кіру және тіркелу");
      link.innerHTML = person;
      root.append(link);
      return;
    }
    const toggle = action(
      activeUser.username[0].toUpperCase(),
      () => {
        menu.hidden = !menu.hidden;
        toggle.setAttribute("aria-expanded", String(!menu.hidden));
      },
      "avatar-button",
    );
    toggle.id = "profile-toggle";
    toggle.setAttribute("aria-label", "Профиль мәзірі");
    toggle.setAttribute("aria-expanded", "false");
    toggle.setAttribute("aria-controls", "profile-menu");
    const menu = el("div", undefined, "profile-menu");
    menu.id = "profile-menu";
    menu.hidden = true;
    menu.append(
      el("strong", activeUser.username),
      el("small", "Пошта расталған", "verified-text"),
    );
    [
      ["Менің профилім", "/profile"],
      ["Менің эсселерім", "/#history"],
      ["Қауіпсіздік", "/profile#security"],
      ...(activeUser.role === "admin" ? [["Әкімші панелі", "/admin"]] : []),
    ].forEach(([label, href]) => {
      const link = el("a", label);
      link.href = href;
      menu.append(link);
    });
    menu.append(
      action(
        "Аккаунттан шығу",
        async () => {
          try {
            await post("/api/auth/logout");
            clearPrivateState();
            location.assign("/");
          } catch (error) {
            message(error.message, true);
          }
        },
        "menu-logout",
      ),
    );
    root.append(toggle, menu);
  }
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".account-nav")) {
      const menu = byId("profile-menu");
      if (menu) {
        menu.hidden = true;
        byId("profile-toggle").setAttribute("aria-expanded", "false");
      }
    }
  });
  document.addEventListener("keydown", (event) => {
    if (
      event.key === "Escape" &&
      byId("profile-menu") &&
      !byId("profile-menu").hidden
    ) {
      byId("profile-menu").hidden = true;
      byId("profile-toggle").setAttribute("aria-expanded", "false");
      byId("profile-toggle").focus();
    }
  });
  function message(text = "", failed = false) {
    const box = byId("account-message");
    if (!box) {
      if (failed) alert(text);
      return;
    }
    box.textContent = text;
    box.hidden = !text;
    box.className = failed ? "account-alert error-alert" : "account-alert";
  }
  function formData(form) {
    return Object.fromEntries(new FormData(form));
  }
  function mark(input, valid, text) {
    input.classList.toggle("field-valid", valid);
    input.classList.toggle("field-invalid", !valid);
    input.setAttribute("aria-invalid", String(!valid));
    const help = byId(input.getAttribute("aria-describedby"));
    if (help) {
      help.textContent = text;
      help.className = "field-help " + (valid ? "valid-text" : "invalid-text");
    }
  }
  function bindForm(id, handler) {
    const form = byId(id);
    if (!form) return;
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (form.dataset.busy === "true") return;
      message();
      const data = formData(form);
      if (data.password_confirmation !== undefined) {
        const password = data.new_password ?? data.password;
        if (password !== data.password_confirmation) {
          message("Құпиясөздер сәйкес емес.", true);
          return;
        }
      }
      form.dataset.busy = "true";
      form.setAttribute("aria-busy", "true");
      const submit = form.querySelector('[type="submit"]');
      const label = submit.textContent;
      submit.disabled = true;
      submit.textContent = "Өңделуде…";
      try {
        await handler(data, form);
      } catch (error) {
        message(error.message, true);
        for (const [field, text] of Object.entries(error.fields || {})) {
          const input = form.elements.namedItem(field);
          if (input instanceof HTMLInputElement) mark(input, false, text);
          if (field === "form") message(text, true);
        }
      } finally {
        submit.disabled = false;
        submit.textContent = label;
        form.dataset.busy = "false";
        form.setAttribute("aria-busy", "false");
      }
    });
  }
  function setMode(next, focus = true) {
    mode = next;
    message();
    ["login", "register", "forgot", "code"].forEach((name) => {
      byId(name + "-form").hidden = name !== next;
    });
    byId("auth-shell").classList.toggle("mode-register", next === "register");
    const registration = next === "register";
    byId("switch-title").textContent = registration
      ? "Қайта оралыңыз."
      : "Алғашқы эссеңізден бастаңыз.";
    byId("switch-description").textContent = registration
      ? "Жұмыстарыңыз бен талдауларыңыз жеке кабинетте сақталады."
      : "Ойыңызды жазыңыз. Нақты кері байланыс алыңыз. Келесі нұсқаңызды жақсартыңыз.";
    byId("switch-auth").textContent = registration ? "Кіру" : "Тіркелу";
    if (focus)
      byId(next + "-form")
        .querySelector("input")
        ?.focus({ preventScroll: true });
  }
  function saveChallenge(data, purpose) {
    challenge = { ...data, purpose, resendAt: Date.now() + 60000 };
    sessionStorage.setItem("tekser-challenge", JSON.stringify(challenge));
    showCode();
  }
  function showCode() {
    resendAt = challenge.resendAt;
    byId("profile-view").hidden = true;
    byId("auth-view").hidden = false;
    setMode("code");
    byId("code-description").textContent =
      challenge.purpose === "reset"
        ? "Аккаунт бар болса, код поштаға жіберілді. Кодты және жаңа құпиясөзді енгізіңіз."
        : "Код " +
          challenge.email +
          " мекенжайына жіберілді. Пошта расталғаннан кейін әрекет аяқталады.";
    const reset = challenge.purpose === "reset";
    byId("reset-password-fields").hidden = !reset;
    ["reset-password", "reset-confirmation"].forEach((id) => {
      byId(id).required = reset;
      byId(id).disabled = !reset;
    });
    byId("verification-code").value = "";
    tickResend();
  }
  function tickResend() {
    const button = byId("resend-code");
    if (!button) return;
    const seconds = Math.max(0, Math.ceil((resendAt - Date.now()) / 1000));
    button.disabled = seconds > 0;
    button.textContent = seconds
      ? `Қайта жіберу: ${seconds} секунд`
      : "Кодты қайта жіберу";
  }
  function nextPath() {
    const next = new URLSearchParams(location.search).get("next");
    return ["/", "/profile", "/admin"].includes(next) ? next : "/profile";
  }
  async function showProfile() {
    byId("auth-view").hidden = true;
    byId("profile-view").hidden = false;
    profileData = await request("/api/account");
    const user = profileData.user;
    changeUser(user);
    byId("profile-avatar").textContent = user.username[0].toUpperCase();
    byId("profile-name").textContent = user.username;
    byId("profile-email").textContent = user.email;
    byId("profile-created").textContent =
      "Тіркелген күні: " +
      new Date(user.created_at).toLocaleDateString("kk-KZ");
    byId("profile-username").value = user.username;
    byId("profile-stats").textContent =
      `${profileData.essay_count} эссе` +
      (profileData.last_essay_at
        ? " · Соңғы жұмыс: " +
          new Date(profileData.last_essay_at).toLocaleDateString("kk-KZ")
        : " · Алғашқы эссеңізді жазыңыз.");
    byId("delete-form").closest("details").hidden = user.role === "admin";
    if (location.hash === "#security") byId("security").scrollIntoView();
  }
  const adminLabels = {
    overview: "Жалпы шолу",
    users: "Пайдаланушылар",
    pending: "Растау сұраулары",
    services: "Қызметтердің күйі",
    audit: "Әрекеттер журналы",
  };
  function table(root, headers, rows) {
    const wrap = el("div", undefined, "admin-table-wrap"),
      table = el("table", undefined, "score-table");
    const head = el("tr");
    headers.forEach((label) => head.append(el("th", label)));
    const thead = el("thead");
    thead.append(head);
    table.append(thead);
    const body = el("tbody");
    rows.forEach((cells) => {
      const row = el("tr");
      cells.forEach((value) => {
        const cell = el("td");
        cell.append(
          value instanceof Node
            ? value
            : document.createTextNode(String(value ?? "—")),
        );
        row.append(cell);
      });
      body.append(row);
    });
    table.append(body);
    wrap.append(table);
    root.append(wrap);
    if (!rows.length) root.append(el("p", "Әзірге жазба жоқ.", "muted"));
  }
  let adminToken = 0;
  async function showAdmin(section = "overview", search = "", offset = 0) {
    const version = ++adminToken;
    const root = byId("admin-content");
    root.replaceChildren(el("p", "Жүктелуде…", "muted"));
    document
      .querySelectorAll("[data-admin]")
      .forEach((button) =>
        button.setAttribute(
          "aria-current",
          String(button.dataset.admin === section),
        ),
      );
    try {
      const data = await request(
        `/api/admin/${section}` +
          (section === "users"
            ? `?q=${encodeURIComponent(search)}&offset=${offset}`
            : ""),
      );
      if (version !== adminToken) return;
      root.replaceChildren(el("h2", adminLabels[section]));
      if (section === "overview") {
        const stats = el("div", undefined, "admin-stats");
        const labels = {
          users: "Пайдаланушылар",
          essays: "Эсселер",
          pending_registrations: "Растауды күтуде",
          failed_analyses: "Сәтсіз талдаулар",
          blocked: "Бұғатталған",
          unowned: "Бұрынғы ортақ эсселер",
        };
        Object.entries(labels).forEach(([key, label]) => {
          const card = el("article");
          card.append(el("strong", data[key]), el("span", label));
          stats.append(card);
        });
        root.append(
          stats,
          el(
            "p",
            "Бұрынғы ортақ эсселер сақталған. Оларды бекіту сервердегі көшіру пәрменімен орындалады.",
            "muted",
          ),
        );
      } else if (section === "users") {
        const form = el("form", undefined, "admin-search"),
          input = el("input");
        input.type = "search";
        input.value = search;
        input.placeholder = "Логин немесе email";
        input.setAttribute("aria-label", "Пайдаланушыларды іздеу");
        const button = el("button", "Іздеу", "secondary");
        button.type = "submit";
        form.append(input, button);
        form.onsubmit = (event) => {
          event.preventDefault();
          showAdmin("users", input.value);
        };
        root.append(form);
        table(
          root,
          ["Логин", "Email", "Рөлі", "Тіркелген күні", "Күйі", "Әрекет"],
          data.map((user) => [
            user.username,
            user.email,
            user.role === "admin" ? "Әкімші" : "Пайдаланушы",
            new Date(user.created_at).toLocaleDateString("kk-KZ"),
            user.blocked ? "Бұғатталған" : "Белсенді",
            user.role === "admin"
              ? "—"
              : action(
                  user.blocked ? "Ашу" : "Бұғаттау",
                  async () => {
                    if (
                      !confirm(
                        `${user.username}: ${user.blocked ? "бұғаттауды ашу" : "аккаунтты бұғаттау"}?`,
                      )
                    )
                      return;
                    try {
                      await post(
                        `/api/admin/users/${user.id}`,
                        { blocked: !user.blocked },
                        "PATCH",
                      );
                      await showAdmin("users", search, offset);
                    } catch (error) {
                      message(error.message, true);
                    }
                  },
                  "secondary",
                ),
          ]),
        );
        if (offset)
          root.append(
            action("Алдыңғы бет", () =>
              showAdmin("users", search, Math.max(0, offset - 30)),
            ),
          );
        if (data.length === 30)
          root.append(
            action("Келесі бет", () => showAdmin("users", search, offset + 30)),
          );
      } else if (section === "pending") {
        table(
          root,
          ["Логин", "Email", "Әрекет", "Күйі", "Қате енгізу"],
          data.map((row) => [
            row.username,
            row.email,
            {
              register: "Тіркелу",
              reset: "Қалпына келтіру",
              email: "Поштаны ауыстыру",
            }[row.purpose],
            row.expired ? "Мерзімі өткен" : "Код күтілуде",
            row.attempts,
          ]),
        );
      } else if (section === "services") {
        root.append(
          el(
            "p",
            "Деректер базасы: " + (data.database ? "Қосылған" : "Қосылмады"),
          ),
          el(
            "p",
            "SMTP: " + (data.smtp_configured ? "Бапталған" : "Бапталмаған"),
          ),
          el(
            "p",
            "Gemini: " +
              (data.gemini_configured
                ? "Кілт енгізілген"
                : "Кілт енгізілмеген") +
              " · " +
              data.model,
          ),
          el(
            "p",
            "Баптаудың болуы хаттың жеткізілгенін немесе API сұрауының сәтті орындалғанын растамайды.",
            "muted",
          ),
          el("h3", "Пошта сұраулары"),
        );
        table(
          root,
          ["Уақыты", "Әрекет", "SMTP нәтижесі"],
          data.deliveries.map((row) => [
            new Date(row.created_at).toLocaleString("kk-KZ"),
            {
              register: "Тіркелу",
              reset: "Қалпына келтіру",
              email: "Поштаны ауыстыру",
            }[row.purpose],
            row.status === "accepted" ? "SMTP қабылдады" : "Жіберілмеді",
          ]),
        );
        root.append(el("h3", "Талдау қателері"));
        table(
          root,
          ["Эссе №", "Бөлім", "Қате"],
          data.errors.map((row) => [
            row.essay_id,
            row.section === "grade" ? "Бағалау" : "Тақырып",
            row.error,
          ]),
        );
      } else {
        const labels = {
          user_blocked: "Аккаунт бұғатталды",
          user_unblocked: "Бұғаттау алынды",
          password_changed: "Құпиясөз өзгертілді",
          password_reset: "Құпиясөз қалпына келтірілді",
          username_changed: "Логин өзгертілді",
          email_changed: "Пошта өзгертілді",
          account_deleted: "Аккаунт жойылды",
          admin_initialized: "Алғашқы әкімші тағайындалды",
          legacy_essays_claimed: "Бұрынғы эсселер бекітілді",
        };
        table(
          root,
          ["Уақыты", "Орындаушы №", "Аккаунт №", "Әрекет"],
          data.map((row) => [
            new Date(row.created_at).toLocaleString("kk-KZ"),
            row.actor_id,
            row.target_id,
            labels[row.action] || row.action,
          ]),
        );
      }
    } catch (error) {
      if (version === adminToken)
        root.replaceChildren(el("p", error.message, "invalid-text"));
    }
  }
  function initializeForms() {
    document.querySelectorAll("[data-password]").forEach((button) => {
      button.innerHTML = eye;
      button.onclick = () => {
        const input = byId(button.dataset.password),
          show = input.type === "password";
        input.type = show ? "text" : "password";
        button.setAttribute("aria-pressed", String(show));
        button.setAttribute(
          "aria-label",
          show ? "Құпиясөзді жасыру" : "Құпиясөзді көрсету",
        );
      };
    });
    document.querySelectorAll("[data-mode]").forEach((button) => {
      button.onclick = () => setMode(button.dataset.mode);
    });
    byId("switch-auth").onclick = () =>
      setMode(mode === "register" ? "login" : "register");
    bindForm("login-form", async (data) => {
      const result = await post("/api/auth/login", data);
      changeUser(result.user);
      location.assign(nextPath());
    });
    bindForm("register-form", async (data) =>
      saveChallenge(await post("/api/auth/register", data), "register"),
    );
    bindForm("forgot-form", async (data) =>
      saveChallenge(
        await post("/api/auth/forgot", { ...data, password: "" }),
        "reset",
      ),
    );
    bindForm("code-form", async (data) => {
      const result = await post("/api/auth/verify", {
        ...data,
        challenge_id: challenge.challenge_id,
      });
      sessionStorage.removeItem("tekser-challenge");
      byId("code-form").reset();
      const purpose = challenge.purpose;
      challenge = null;
      if (purpose === "reset") {
        changeUser(null);
        setMode("login");
        message(result.message);
      } else if (purpose === "email") {
        await showProfile();
        message(result.message);
      } else {
        changeUser(result.user);
        location.assign(nextPath());
      }
    });
    byId("resend-code").onclick = async () => {
      byId("resend-code").disabled = true;
      try {
        const result = await post("/api/auth/resend", {
          challenge_id: challenge.challenge_id,
        });
        saveChallenge(result, challenge.purpose);
        message("Жаңа код жіберілді. Бұрынғы код жарамсыз.");
      } catch (error) {
        message(error.message, true);
      } finally {
        tickResend();
      }
    };
    byId("code-back").onclick = async () => {
      const email = challenge?.purpose === "email";
      sessionStorage.removeItem("tekser-challenge");
      challenge = null;
      if (email) await showProfile();
      else setMode("login");
    };
    setInterval(tickResend, 1000);
    bindForm("profile-form", async (data) => {
      const result = await post("/api/account", data, "PATCH");
      changeUser(result.user);
      await showProfile();
      message("Логин сақталды.");
    });
    bindForm("email-form", async (data, form) => {
      saveChallenge(await post("/api/account/email", data), "email");
      form.reset();
    });
    bindForm("password-form", async (data, form) => {
      const result = await post("/api/account/password", data);
      changeUser(result.user);
      form.reset();
      message("Құпиясөз өзгертілді. Басқа құрылғылардағы сессиялар аяқталды.");
    });
    byId("logout-all").onclick = async () => {
      try {
        await post("/api/auth/logout-all");
        clearPrivateState();
        location.assign("/auth");
      } catch (error) {
        message(error.message, true);
      }
    };
    bindForm("delete-form", async (data) => {
      if (!confirm("Аккаунт пен барлық эсселерді біржола жоюды растайсыз ба?"))
        return;
      await post("/api/account", data, "DELETE");
      clearPrivateState();
      location.assign("/auth");
    });
    document.querySelectorAll("[data-admin]").forEach((button) => {
      button.onclick = () => showAdmin(button.dataset.admin);
    });
    const timers = new Map();
    const register = byId("register-form");
    register.querySelectorAll("input").forEach((input) =>
      input.addEventListener("input", () => {
        const password = byId("register-password"),
          confirmation = byId("register-confirmation");
        const same = password.value === confirmation.value;
        confirmation.setCustomValidity(
          confirmation.value && !same ? "Құпиясөздер сәйкес емес." : "",
        );
        if ([password, confirmation].includes(input)) {
          password.setCustomValidity(
            password.value && Array.from(password.value).length < 4
              ? "Кемінде 4 таңба қажет."
              : "",
          );
          if (password.value)
            mark(
              password,
              Array.from(password.value).length >= 4,
              Array.from(password.value).length >= 4
                ? "Ұзындығы жеткілікті."
                : "Кемінде 4 таңба қажет.",
            );
          if (confirmation.value)
            mark(
              confirmation,
              same && Array.from(confirmation.value).length >= 4,
              same ? "Құпиясөздер сәйкес." : "Құпиясөздер сәйкес емес.",
            );
          return;
        }
        clearTimeout(timers.get(input.id));
        input.classList.remove("field-valid");
        const value = input.value;
        if (!input.checkValidity()) {
          mark(
            input,
            false,
            input.name === "email"
              ? "Email пішімін тексеріңіз."
              : "3–30 латын әрпі, сан немесе _ белгісі қажет.",
          );
          return;
        }
        timers.set(
          input.id,
          setTimeout(async () => {
            try {
              const result = await post("/api/auth/validate", {
                [input.name]: value,
              });
              if (input.value === value)
                mark(
                  input,
                  result[input.name].valid,
                  result[input.name].message +
                    (input.name === "email" && result.email.valid
                      ? " Пошта кодпен расталады."
                      : ""),
                );
            } catch {
              if (input.value === value) {
                input.classList.remove("field-valid", "field-invalid");
                byId(input.getAttribute("aria-describedby")).textContent =
                  "Қолжетімділікті жібергенде қайта тексереміз.";
              }
            }
          }, 500),
        );
      }),
    );
  }
  window.accountReady = (async () => {
    try {
      const result = await request("/api/auth/me");
      changeUser(result.user);
    } catch (error) {
      changeUser(null);
      window.accountError = error.message;
    }
    if (!byId("account-main")) return activeUser;
    byId("account-loading").hidden = true;
    initializeForms();
    const path = location.pathname;
    if (path === "/auth") {
      if (activeUser) {
        location.replace(nextPath());
        return activeUser;
      }
      byId("auth-view").hidden = false;
      setMode("login", false);
      try {
        challenge = JSON.parse(sessionStorage.getItem("tekser-challenge"));
        if (challenge) showCode();
      } catch {
        sessionStorage.removeItem("tekser-challenge");
      }
      if (window.accountError) message(window.accountError, true);
    } else if (!activeUser) {
      location.replace("/auth?next=" + encodeURIComponent(path));
    } else if (path === "/profile") {
      try {
        await showProfile();
        const saved = JSON.parse(sessionStorage.getItem("tekser-challenge"));
        if (saved?.purpose === "email") {
          challenge = saved;
          showCode();
        }
      } catch (error) {
        message(error.message, true);
      }
    } else if (path === "/admin") {
      if (activeUser.role !== "admin")
        message("Бұл бөлімге әкімші құқығы қажет.", true);
      else {
        byId("admin-view").hidden = false;
        await showAdmin();
      }
    }
    return activeUser;
  })();
})();
