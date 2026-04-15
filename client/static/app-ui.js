  // =======================
  // UI Refs + navigation
  // =======================
  const statusText = document.getElementById('statusText');
  const lastMs = document.getElementById('lastMs');
  const logBody = document.getElementById('logBody');

  const pageDashboard = document.getElementById('pageDashboard');
  const pageLive = document.getElementById('pageLive');
  const pageFile = document.getElementById('pageFile');
  const pageLogs = document.getElementById('pageLogs');

  function setStatus(text, ok=true) {
    statusText.textContent = text;
    statusText.style.color = ok ? 'var(--accent)' : 'var(--danger)';
  }

  function showPage(which) {
    pageDashboard.classList.remove('active');
    pageLive.classList.remove('active');
    pageFile.classList.remove('active');
    pageLogs.classList.remove('active');
    if (which === 'dashboard') pageDashboard.classList.add('active');
    if (which === 'live') pageLive.classList.add('active');
    if (which === 'file') pageFile.classList.add('active');
    if (which === 'logs') pageLogs.classList.add('active');
  }

  document.getElementById('btnDashboard').addEventListener('click', () => showPage('dashboard'));
  document.getElementById('btnLive').addEventListener('click', () => showPage('live'));
  document.getElementById('btnFile').addEventListener('click', () => showPage('file'));
  document.getElementById('btnLogs').addEventListener('click', async () => { showPage('logs'); await loadStoredLogs(); });

  // =======================
  // Settings modal + theme
  // =======================
  const settingsBackdrop = document.getElementById('settingsBackdrop');
  const btnSettings = document.getElementById('btnSettings');
  const btnCloseSettings = document.getElementById('btnCloseSettings');

  const fpsInput = document.getElementById('fpsInput');
  const confInput = document.getElementById('confInput');
  const maxDimInput = document.getElementById('maxDimInput');
  const modelCheckIntervalInput = document.getElementById('modelCheckIntervalInput');
  const currentModelVersion = document.getElementById('currentModelVersion');
  const pendingModelVersion = document.getElementById('pendingModelVersion');
  const lastModelCheckAt = document.getElementById('lastModelCheckAt');
  const lastSyncAt = document.getElementById('lastSyncAt');
  const autoClip = document.getElementById('autoClip');
  const clipMode = document.getElementById('clipMode');
  const clipSec = document.getElementById('clipSec');
  const bgColor = document.getElementById('bgColor');
  const langSelect = document.getElementById('langSelect');
  const modelStatusHint = document.getElementById('modelStatusHint');
  let currentClientSettings = null;
  let settingsSaveTimer = null;
  let settingsHydrating = false;

  function apiJson(url, options) {
    if (window.authApi && typeof window.authApi.apiFetch === 'function') {
      return window.authApi.apiFetch(url, options);
    }
    return fetch(url, options).then(async (response) => {
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw payload;
      return payload;
    });
  }

  function formatDateTime(value) {
    if (!value) return '-';
    const dt = new Date(value);
    if (Number.isNaN(dt.getTime())) return String(value);
    return dt.toLocaleString();
  }

  function setModelHint(text) {
    if (modelStatusHint) modelStatusHint.textContent = text || '';
  }

  function collectClientSettings() {
    return {
      fps: Math.max(1, Math.min(15, Number(fpsInput.value) || 6)),
      conf: Math.max(0.05, Math.min(0.95, Number(confInput.value) || 0.4)),
      max_dim: Math.max(320, Math.min(1280, Number(maxDimInput.value) || 640)),
      auto_clip: !!autoClip.checked,
      clip_mode: clipMode.value === 'fixed' ? 'fixed' : 'event',
      clip_sec: Math.max(3, Math.min(60, Number(clipSec.value) || 8)),
      model_check_interval_seconds: Math.max(10, Math.min(3600, Number(modelCheckIntervalInput.value) || 30))
    };
  }

  function applyClientSettings(settings) {
    settingsHydrating = true;
    currentClientSettings = { ...(settings || {}) };
    fpsInput.value = String(settings?.fps ?? 6);
    confInput.value = String(settings?.conf ?? 0.4);
    maxDimInput.value = String(settings?.max_dim ?? 640);
    modelCheckIntervalInput.value = String(settings?.model_check_interval_seconds ?? 30);
    autoClip.checked = !!settings?.auto_clip;
    clipMode.value = settings?.clip_mode === 'fixed' ? 'fixed' : 'event';
    clipSec.value = String(settings?.clip_sec ?? 8);
    settingsHydrating = false;
  }

  async function refreshClientSettings() {
    try {
      const payload = await apiJson('/api/client/settings', { method: 'GET' });
      applyClientSettings(payload.settings || {});
      return payload.settings || {};
    } catch (error) {
      const status = error?.status || 0;
      if (status !== 401) {
        console.warn('Failed to load client settings', error);
      }
      return null;
    }
  }

  function emitSettingsChanged(settings, previous) {
    window.dispatchEvent(new CustomEvent('client-settings-changed', {
      detail: {
        settings,
        previous: previous || null
      }
    }));
  }

  async function saveClientSettings({ immediate = false } = {}) {
    if (settingsHydrating) return;
    if (settingsSaveTimer) {
      clearTimeout(settingsSaveTimer);
      settingsSaveTimer = null;
    }

    const run = async () => {
      const previous = currentClientSettings ? { ...currentClientSettings } : null;
      const nextSettings = collectClientSettings();
      try {
        const payload = await apiJson('/api/client/settings', {
          method: 'POST',
          body: nextSettings
        });
        applyClientSettings(payload.settings || nextSettings);
        emitSettingsChanged(payload.settings || nextSettings, previous);
      } catch (error) {
        const message = error?.error || error?.message || t('saveSettingsFailed');
        toast(t('settingsTitle'), message);
      }
    };

    if (immediate) {
      await run();
      return;
    }

    settingsSaveTimer = setTimeout(() => {
      settingsSaveTimer = null;
      run();
    }, 250);
  }

  function renderModelStatus(status) {
    const current = status?.current || {};
    const pending = status?.pending || null;
    if (currentModelVersion) {
      currentModelVersion.value = current.version || current.filename || '-';
    }
    if (pendingModelVersion) {
      pendingModelVersion.value = pending ? (pending.version || pending.filename || '-') : '-';
    }
    if (lastModelCheckAt) {
      lastModelCheckAt.value = formatDateTime(status?.last_model_check_at);
    }
    if (lastSyncAt) {
      lastSyncAt.value = formatDateTime(status?.last_sync_at);
    }
    setModelHint(
      status?.message ||
      'Auto check and auto sync.'
    );
  }

  async function refreshModelStatus() {
    try {
      const payload = await apiJson('/api/client/model/status', { method: 'GET' });
      renderModelStatus(payload.status || {});
      return payload.status || null;
    } catch (error) {
      const status = error?.status || 0;
      if (status !== 401) {
        console.warn('Failed to load model status', error);
      }
      return null;
    }
  }

  function openSettings() {
    settingsBackdrop.classList.add('show');
    settingsBackdrop.setAttribute('aria-hidden', 'false');
    refreshClientSettings();
    refreshModelStatus();
  }
  function closeSettings() {
    settingsBackdrop.classList.remove('show');
    settingsBackdrop.setAttribute('aria-hidden', 'true');
  }
  btnSettings.addEventListener('click', openSettings);
  btnCloseSettings.addEventListener('click', closeSettings);
  settingsBackdrop.addEventListener('click', (e) => { if (e.target === settingsBackdrop) closeSettings(); });

  // Theme + language persistence
  function applyBgColor(hex) {
    document.documentElement.style.setProperty('--bg', hex);
  }
  function initThemeLang() {
    const savedBg = localStorage.getItem('bg') || '#0b0f17';
    bgColor.value = savedBg;
    applyBgColor(savedBg);

    const savedLang = getLang();
    langSelect.value = savedLang;
    const tradOption = langSelect.querySelector('option[value="zh-Hant"]');
    const simpOption = langSelect.querySelector('option[value="zh-Hans"]');
    if (tradOption) tradOption.textContent = t('langTraditional');
    if (simpOption) simpOption.textContent = t('langSimplified');
    applyI18n();
    const eventModeCap = document.querySelector('[data-i18n="eventModeCap"]');
    if (eventModeCap && savedLang === 'en') {
      eventModeCap.textContent = 'Event mode ends after the target disappears.';
    }
  }
  bgColor.addEventListener('input', () => {
    localStorage.setItem('bg', bgColor.value);
    applyBgColor(bgColor.value);
  });
  langSelect.addEventListener('change', () => setLang(langSelect.value));

  [
    fpsInput,
    confInput,
    maxDimInput,
    modelCheckIntervalInput,
    clipSec
  ].forEach((input) => {
    if (!input) return;
    input.addEventListener('input', () => saveClientSettings());
    input.addEventListener('change', () => saveClientSettings({ immediate: true }));
  });

  [autoClip, clipMode].forEach((input) => {
    if (!input) return;
    input.addEventListener('change', () => saveClientSettings({ immediate: true }));
  });

  window.getClientSettings = () => ({ ...(currentClientSettings || collectClientSettings()) });
  window.refreshClientSettings = refreshClientSettings;
  window.refreshClientModelStatus = refreshModelStatus;
  window.addEventListener('focus', () => {
    refreshModelStatus();
  });
  setInterval(() => {
    refreshModelStatus();
  }, 15000);
  setTimeout(() => {
    refreshClientSettings();
    refreshModelStatus();
  }, 300);

  // =======================
  // Toast Alerts
  // =======================
  const toastWrap = document.getElementById('toastWrap');
  function toast(title, body) {
    const el = document.createElement('div');
    el.className = 'toast';
    el.innerHTML = `
      <div class="badge"></div>
      <div>
        <div class="t-title">${title}</div>
        <div class="t-body">${body}</div>
      </div>
    `;
    toastWrap.appendChild(el);
    setTimeout(() => {
      el.style.opacity = '0';
      el.style.transform = 'translateY(8px)';
      el.style.transition = 'all .25s ease';
      setTimeout(() => el.remove(), 260);
    }, 2800);
  }

  // =======================
  // Logs (dashboard recent)
  // =======================
  document.getElementById('btnClearLogs').addEventListener('click', () => logBody.innerHTML = '');

  function fmtHHMMSS(ts) {
    const d = new Date(ts);
    const hh = String(d.getHours()).padStart(2,'0');
    const mm = String(d.getMinutes()).padStart(2,'0');
    const ss = String(d.getSeconds()).padStart(2,'0');
    return `${hh}:${mm}:${ss}`;
  }

  function addRecentLogRow({ timeText, event, source='-', clipText='-', pendingId=null }) {
    const tr = document.createElement('tr');

    const tdT = document.createElement('td'); tdT.textContent = timeText || '-';
    const tdE = document.createElement('td'); tdE.textContent = event || '-';
    const tdS = document.createElement('td'); tdS.textContent = source || '-';
    const tdL = document.createElement('td'); tdL.textContent = clipText || '-';
    if (clipText === '-' || clipText === t('saving') || clipText === t('clipOff')) tdL.style.color = 'var(--muted)';

    tr.appendChild(tdT); tr.appendChild(tdE); tr.appendChild(tdS); tr.appendChild(tdL);
    logBody.prepend(tr);

    return { tr, tdT, tdE, tdS, tdL, pendingId };
  }

  // =======================
  // Stored Logs Page (admin edit/delete + download)
  // =======================
  const storedLogBody = document.getElementById('storedLogBody');
  const btnReloadStoredLogs = document.getElementById('btnReloadStoredLogs');
  if (btnReloadStoredLogs) {
    btnReloadStoredLogs.addEventListener('click', async () => {
      if (typeof loadStoredLogs === 'function') await loadStoredLogs();
    });
  }

  let isAdmin = false;

  function setAdminState(next) {
    isAdmin = !!next;
  }

  window.setAdminState = setAdminState;


