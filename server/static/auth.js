(function () {
  function safeReplace(url) {
    if (window.__auth_redirecting) return;
    window.__auth_redirecting = true;
    setTimeout(() => {
      window.location.replace(url);
    }, 120);
  }

  function wait(delayMs) {
    return new Promise((resolve) => setTimeout(resolve, delayMs));
  }

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
    return await apiFetch("/api/auth/login", {
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

  async function waitForSessionReady(retries = 10, delayMs = 120) {
    for (let i = 0; i < retries; i += 1) {
      const current = await refreshCurrentUser();
      if (current) return current;
      await wait(delayMs);
    }
    return null;
  }

  async function authLogout() {
    return await apiFetch("/api/auth/logout", { method: "POST" });
  }

  async function requireRole(role, { redirectUrl = "/login.html" } = {}) {
    const user = await refreshCurrentUser();
    if (!user || (role && user.role !== role)) {
      safeReplace(redirectUrl);
      return null;
    }
    return user;
  }

  async function requireAdmin(options) {
    return await requireRole("admin", options);
  }

  window.authApi = window.authApi || {};
  window.authApi.apiFetch = apiFetch;
  window.authApi.authLogin = authLogin;
  window.authApi.authMe = authMe;
  window.authApi.refreshCurrentUser = refreshCurrentUser;
  window.authApi.waitForSessionReady = waitForSessionReady;
  window.authApi.authLogout = authLogout;
  window.authApi.requireRole = requireRole;
  window.authApi.requireAdmin = requireAdmin;
  window.authApi.safeReplace = safeReplace;

  window.apiFetch = apiFetch;
  window.authLogin = authLogin;
  window.authMe = authMe;
  window.refreshCurrentUser = refreshCurrentUser;
  window.waitForSessionReady = waitForSessionReady;
  window.authLogout = authLogout;
  window.requireRole = requireRole;
  window.requireAdmin = requireAdmin;
  window.safeReplace = safeReplace;

  window.__auth_redirecting = window.__auth_redirecting || false;

  window.addEventListener("error", function (e) {
    console.warn("Unhandled error:", e && e.message);
  });
  window.addEventListener("unhandledrejection", function (e) {
    console.warn("Unhandled rejection:", e && e.reason);
  });
})();
