  // Client UI wiring: page navigation, settings modal, theme handling,
  // and small status widgets around the detector workflow.
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

  // Update the compact status label shown in the page header.
  function setStatus(text, ok=true) {
    statusText.textContent = text;
    statusText.style.color = ok ? 'var(--accent)' : 'var(--danger)';
  }

  // Switch between dashboard, live camera, video file, and log pages.
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
  const highThreatSeconds = document.getElementById('highThreatSeconds');
  const bgColor = document.getElementById('bgColor');
  const langSelect = document.getElementById('langSelect');
  const modelStatusHint = document.getElementById('modelStatusHint');
  let currentClientSettings = null;
  let settingsSaveTimer = null;
  let settingsHydrating = false;

  // Wrapper for authenticated JSON calls. Falls back to fetch for early page load.
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

  // Format server timestamps in the user's local browser format.
  function formatDateTime(value) {
    if (!value) return '-';
    const dt = new Date(value);
    if (Number.isNaN(dt.getTime())) return String(value);
    return dt.toLocaleString();
  }

  // Keep model/policy messages in one small hint area in the settings modal.
  function setModelHint(text) {
    if (modelStatusHint) modelStatusHint.textContent = text || '';
  }

  // Show read-only threat policy values controlled by the central admin.
  function updatePolicyHint(settings) {
    const cap = Math.round(Number(settings?.detection_confidence_cap ?? 0.4) * 100);
    const medium = Math.round(Number(settings?.medium_confidence ?? 0.75) * 100);
    setModelHint(`Admin policy: detection cap ${cap}%, medium confidence ${medium}%.`);
  }

  // Collect editable settings from the modal and clamp values to safe ranges.
  function collectClientSettings() {
    const detectionCap = Math.max(0.05, Math.min(0.95, Number(currentClientSettings?.detection_confidence_cap ?? 0.4)));
    return {
      fps: Math.max(1, Math.min(15, Number(fpsInput.value) || 6)),
      conf: Math.max(0.05, Math.min(detectionCap, Number(confInput.value) || 0.4)),
      max_dim: Math.max(320, Math.min(1280, Number(maxDimInput.value) || 640)),
      auto_clip: !!autoClip.checked,
      clip_mode: clipMode.value === 'fixed' ? 'fixed' : 'event',
      clip_sec: Math.max(3, Math.min(60, Number(clipSec.value) || 8)),
      high_threat_seconds: Math.max(1, Math.min(120, Number(currentClientSettings?.high_threat_seconds ?? 3))),
      detection_confidence_cap: detectionCap,
      medium_confidence: Number(currentClientSettings?.medium_confidence ?? 0.75),
      medium_box_pct: Number(currentClientSettings?.medium_box_pct ?? 8),
      model_check_interval_seconds: Math.max(10, Math.min(3600, Number(modelCheckIntervalInput.value) || 30))
    };
  }

  // Fill the settings modal from the saved backend settings.
  function applyClientSettings(settings) {
    settingsHydrating = true;
    currentClientSettings = { ...(settings || {}) };
    const detectionCap = Math.max(0.05, Math.min(0.95, Number(settings?.detection_confidence_cap ?? 0.4)));
    fpsInput.value = String(settings?.fps ?? 6);
    confInput.max = String(detectionCap);
    confInput.title = `Admin detection confidence cap: ${Math.round(detectionCap * 100)}%`;
    confInput.value = String(Math.min(Number(settings?.conf ?? 0.4), detectionCap));
    maxDimInput.value = String(settings?.max_dim ?? 640);
    modelCheckIntervalInput.value = String(settings?.model_check_interval_seconds ?? 30);
    autoClip.checked = !!settings?.auto_clip;
    clipMode.value = settings?.clip_mode === 'fixed' ? 'fixed' : 'event';
    clipSec.value = String(settings?.clip_sec ?? 8);
    highThreatSeconds.value = String(settings?.high_threat_seconds ?? 3);
    highThreatSeconds.readOnly = true;
    highThreatSeconds.title = 'Controlled by central admin threat policy';
    updatePolicyHint(settings || {});
    settingsHydrating = false;
  }

  // Load saved settings from the local client Flask service.
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

  // Tell detection/camera scripts that runtime settings have changed.
  function emitSettingsChanged(settings, previous) {
    window.dispatchEvent(new CustomEvent('client-settings-changed', {
      detail: {
        settings,
        previous: previous || null
      }
    }));
  }

  // Save settings with a small debounce so inputs do not spam the API.
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

  // Render current and pending model versions in the settings modal.
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
    if (status?.message) setModelHint(status.message);
  }

  // Ask the local backend for model status and pending update state.
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

  // Open settings and refresh dynamic fields each time.
  function openSettings() {
    settingsBackdrop.classList.add('show');
    settingsBackdrop.setAttribute('aria-hidden', 'false');
    refreshClientSettings();
    refreshModelStatus();
  }

  // Close the settings modal without changing the current page.
  function closeSettings() {
    settingsBackdrop.classList.remove('show');
    settingsBackdrop.setAttribute('aria-hidden', 'true');
  }
  btnSettings.addEventListener('click', openSettings);
  btnCloseSettings.addEventListener('click', closeSettings);
  settingsBackdrop.addEventListener('click', (e) => { if (e.target === settingsBackdrop) closeSettings(); });

  // Theme + language persistence
  // Apply the chosen background color through a CSS variable.
  function applyBgColor(hex) {
    document.documentElement.style.setProperty('--bg', hex);
  }

  // Restore saved theme/language choices on page load.
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
    clipSec,
    highThreatSeconds
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

  // Small notification box used for settings and detector messages.
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

  // Convert timestamps into a compact table-friendly time string.
  function fmtHHMMSS(ts) {
    const d = new Date(ts);
    const hh = String(d.getHours()).padStart(2,'0');
    const mm = String(d.getMinutes()).padStart(2,'0');
    const ss = String(d.getSeconds()).padStart(2,'0');
    return `${hh}:${mm}:${ss}`;
  }

  // Add one row to the recent log table on the dashboard.
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

  // Show or hide admin-only controls after the current role is known.
  function setAdminState(next) {
    isAdmin = !!next;
  }

  window.setAdminState = setAdminState;


