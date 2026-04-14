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
  const autoClip = document.getElementById('autoClip');
  const clipMode = document.getElementById('clipMode');
  const clipSec = document.getElementById('clipSec');
  const bgColor = document.getElementById('bgColor');
  const langSelect = document.getElementById('langSelect');
  const btnSyncNow = document.getElementById('btnSyncNow');
  const btnUpdateModel = document.getElementById('btnUpdateModel');
  const nodeActionHint = document.getElementById('nodeActionHint');

  function openSettings() {
    settingsBackdrop.classList.add('show');
    settingsBackdrop.setAttribute('aria-hidden', 'false');
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
    if (tradOption) tradOption.textContent = 'Traditional Chinese';
    if (simpOption) simpOption.textContent = 'Simplified Chinese';
    applyI18n();
    const eventModeCap = document.querySelector('[data-i18n="eventModeCap"]');
    if (eventModeCap && savedLang === 'en') {
      eventModeCap.textContent = '"Save while drone appears" ends after the drone is gone (short delay). Safety cap: 2 minutes.';
    }
  }
  bgColor.addEventListener('input', () => {
    localStorage.setItem('bg', bgColor.value);
    applyBgColor(bgColor.value);
  });
  langSelect.addEventListener('change', () => setLang(langSelect.value));

  async function runNodeAction(url, successPrefix) {
    try {
      const response = await fetch(url, {
        method: 'POST',
        credentials: 'include'
      });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || 'Action failed');
      const suffix = data.downloaded_model || data.path || `${data.synced_logs ?? ''}`;
      const msg = `${successPrefix}${suffix}`.trim();
      if (nodeActionHint) nodeActionHint.textContent = msg;
      toast('Node', msg);
    } catch (error) {
      const msg = error.message || 'This action is not available here.';
      if (nodeActionHint) nodeActionHint.textContent = msg;
      toast('Node', msg);
    }
  }

  if (btnSyncNow) btnSyncNow.addEventListener('click', () => runNodeAction('/api/node/sync', 'Sync complete: '));
  if (btnUpdateModel) btnUpdateModel.addEventListener('click', () => runNodeAction('/api/node/model/update', 'Model update: '));

  if (btnSyncNow) btnSyncNow.textContent = t('syncNow');
  if (btnUpdateModel) btnUpdateModel.textContent = t('updateModel');

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
    if (clipText === '-' || clipText === 'saving...' || clipText === 'off') tdL.style.color = 'var(--muted)';

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


