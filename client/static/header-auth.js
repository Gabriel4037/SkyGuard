(function () {
    // choose check function (prefer authApi.refreshCurrentUser)
    const checkUserFn = (window.authApi && window.authApi.refreshCurrentUser) || window.refreshCurrentUser || (async () => {
      if (window.authMe) {
        const r = await window.authMe();
        return r.user || null;
      }
      return null;
    });

    // single redirect guard
    window.__auth_redirecting = window.__auth_redirecting || false;
    function safeReplace(url) {
      if (window.__auth_redirecting) return;
      window.__auth_redirecting = true;
      setTimeout(() => {
        try { window.location.replace(url); } catch (e) { console.warn('safeReplace error', e); }
      }, 120);
    }

    // main init: check auth and update header UI
    (async function initAuthCheck() {
      try {
        // slight delay to allow cookie/session stabilization
        await new Promise(r => setTimeout(r, 120));

        const user = await checkUserFn();
        if (!user) {
          // not logged in -> allow brief time for user interaction, then redirect
          setTimeout(() => {
            if (!window.__auth_redirecting) safeReplace('/login.html');
          }, 800);
        } else {
          // logged in -> update header UI
          const headerUser = document.getElementById('headerUser');
          if (headerUser) headerUser.textContent = user.username || ('id:' + (user.id || '?'));

          const logoutBtn = document.getElementById('headerLogoutBtn');
          if (logoutBtn) logoutBtn.style.display = 'inline-block';
          if (window.setAdminState) window.setAdminState(user.role === 'admin');
        }
      } catch (err) {
        console.warn('auth check failed', err);
        if (window.setAdminState) window.setAdminState(false);
        // on error, fallback: redirect after short delay
        setTimeout(() => {
          if (!window.__auth_redirecting) safeReplace('/login.html');
        }, 800);
      }
    })();

    // bind logout button (works regardless of admin)
    const logoutBtnEl = document.getElementById('headerLogoutBtn');
    if (logoutBtnEl) {
      logoutBtnEl.addEventListener('click', async () => {
        try {
          if (window.authApi && window.authApi.apiFetch) {
            await window.authApi.apiFetch('/api/auth/logout', { method: 'POST' });
          } else {
            await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' });
          }
        } catch (e) {
          console.warn('logout error', e);
        } finally {
          safeReplace('/login.html');
        }
      });
    }
})();
