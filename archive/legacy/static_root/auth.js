(function () {
  async function apiFetch(url, opts = {}) {
    const cfg = Object.assign(
      {
        credentials: "include",
        headers: {},
      },
      opts,
    );

    if (
      cfg.body &&
      typeof cfg.body === "object" &&
      !(cfg.body instanceof FormData)
    ) {
      cfg.headers["Content-Type"] = "application/json";
      cfg.body = JSON.stringify(cfg.body);
    }

    const res = await fetch(url, cfg);
    const txt = await res.text();
    let data = {};
    try {
      data = txt ? JSON.parse(txt) : {};
    } catch (e) {
      data = { raw: txt };
    }
    if (!res.ok) {
      const err = data || { error: "Request failed" };
      if (typeof err === "object" && err !== null && err.status == null) {
        err.status = res.status;
      }
      throw err;
    }
    return data;
  }

  async function authLogin({ username, password }) {
    if (!username || !password)
      throw { error: "username and password required" };
    console.log("authLogin:", username);
    return await apiFetch("/api/auth/login", {
      method: "POST",
      body: { username, password },
    });
  }

  async function authRegister({ username, password }) {
    if (!username || !password)
      throw { error: "username and password required" };
    console.log("authRegister:", username);
    return await apiFetch("/api/auth/register", {
      method: "POST",
      body: { username, password },
    });
  }

  async function authMe() {
    try {
      const res = await apiFetch("/api/auth/me", { method: "GET" });
      return res;
    } catch (e) {
      console.warn("authMe error", e);
      return { user: null };
    }
  }

  async function refreshCurrentUser() {
    const res = await authMe();
    return res.user || null;
  }

  window.authApi = window.authApi || {};
  window.authApi.apiFetch = apiFetch;
  window.authApi.authLogin = authLogin;
  window.authApi.authRegister = authRegister;
  window.authApi.authMe = authMe;
  window.authApi.refreshCurrentUser = refreshCurrentUser;

  window.apiFetch = apiFetch;
  window.authLogin = authLogin;
  window.authRegister = authRegister;
  window.authMe = authMe;
  window.refreshCurrentUser = refreshCurrentUser;

  window.__auth_redirecting = window.__auth_redirecting || false;

  window.addEventListener("error", function (e) {
    console.warn("Unhandled error:", e && e.message);
  });
  window.addEventListener("unhandledrejection", function (e) {
    console.warn("Unhandled rejection:", e && e.reason);
  });

  console.log("auth.js loaded");
})();
