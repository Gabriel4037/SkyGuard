  // Camera tile management for the client UI. The dashboard and live page share
  // the same camera objects so camera state stays consistent between views.
  // =======================
  // Multi-camera: shared tiles (Dashboard + Live share the same tiles)
  // =======================
  const dashCamGrid = document.getElementById('dashCamGrid');
  const liveCamGrid = document.getElementById('liveCamGrid');

  const dashAddCam = document.getElementById('dashAddCam');
  const dashRefreshDevices = document.getElementById('dashRefreshDevices');
  const dashStartAll = document.getElementById('dashStartAll');
  const dashStopAll = document.getElementById('dashStopAll');
  const dashDetectAll = document.getElementById('dashDetectAll');
  const dashDetectOffAll = document.getElementById('dashDetectOffAll');

  const liveAddCam = document.getElementById('liveAddCam');
  const liveRefreshDevices = document.getElementById('liveRefreshDevices');
  const liveStartAll = document.getElementById('liveStartAll');
  const liveStopAll = document.getElementById('liveStopAll');
  const liveDetectAll = document.getElementById('liveDetectAll');
  const liveDetectOffAll = document.getElementById('liveDetectOffAll');

  let deviceList = [];
  let camTiles = [];
  let camIndex = 0;

  // Refresh camera devices and update each tile's device selector.
  async function refreshDevices() {
    try {
      // ask permission to show labels
      try {
        const tmp = await navigator.mediaDevices.getUserMedia({ video:true, audio:false });
        tmp.getTracks().forEach(t => t.stop());
      } catch (_) {}

      const devices = await navigator.mediaDevices.enumerateDevices();
      deviceList = devices.filter(d => d.kind === 'videoinput');
    } catch (e) {
      deviceList = [];
    }
    for (const t of camTiles) t.populateDevices();
  }

  // Move existing tile DOM nodes to whichever page is active.
  function moveTilesTo(target) {
    const grid = (target === 'dashboard') ? dashCamGrid : liveCamGrid;
    // move all tile elements
    for (const t of camTiles) grid.appendChild(t.el);
  }

  // When switching pages, move camera tiles accordingly
  const origShowPage = showPage;
  showPage = function(which) {
    origShowPage(which);
    if (which === 'dashboard') moveTilesTo('dashboard');
    if (which === 'live') moveTilesTo('live');
  };

  // Build one camera tile with preview, controls, detection, and upload state.
  function createCamTile() {
    camIndex += 1;
    const name = `CAM${camIndex}`;

    // The HTML skeleton is created here because every camera tile has the same
    // controls, but each tile gets its own video/canvas/detector objects below.
    const tile = document.createElement('div');
    tile.className = 'card';
    tile.innerHTML = `
      <div class="card-h">
        <b>${name}</b>
        <div class="btns">
          <button class="ghost btnRemove">${t('remove')}</button>
        </div>
      </div>
      <div class="card-b">
        <div class="row">
          <div>
            <label class="lblDevice">${t('cameraDevice')}</label>
            <select class="selDevice"></select>
          </div>
          <div>
            <label class="lblRes">${t('resolutionHint')}</label>
            <select class="selRes">
              <option value="default">${t('defaultOption')}</option>
              <option value="720">1280x720</option>
              <option value="1080">1920x1080</option>
            </select>
          </div>
        </div>

        <div class="divider"></div>

        <div class="viewer">
          <video class="vid" autoplay playsinline muted></video>
          <canvas class="overlay ov"></canvas>
          <canvas class="record rc"></canvas>
        </div>

        <div class="divider"></div>

        <div class="btns">
          <button class="primary btnStart">${t('start')}</button>
          <button class="danger btnStop">${t('stop')}</button>
          <button class="btnDetOn">${t('detectOn')}</button>
          <button class="danger btnDetOff">${t('detectOff')}</button>
        </div>

        <div class="divider"></div>
        <div class="row" style="align-items:center">
          <div class="hint">
            <span class="sourceLabel">${t('sourceLabel')}</span>: <b>${name}</b>
            <br/>
            <span class="cameraStatusLabel">${t('cameraStatus')}</span>: <b class="cameraStatusText">${t('cameraOff')}</b>
            <br/>
            <span class="detectionStatusLabel">${t('detectionStatus')}</span>: <b class="detectStatusText">${t('detectOffStatus')}</b>
          </div>
          <label class="hint" style="display:flex;align-items:center;gap:8px;margin:0;min-width:170px;">
            <input class="zoneEnabled" type="checkbox" checked />
            <span class="zoneEnabledLabel">${t('protectedZone')}</span>
          </label>
        </div>
      </div>
    `;

    dashCamGrid.appendChild(tile);

    // Store important child elements once so the tile methods do not need to
    // search the DOM repeatedly while detection is running.
    const vid = tile.querySelector('.vid');
    const ov = tile.querySelector('.ov');
    const rc = tile.querySelector('.rc');
    const selDevice = tile.querySelector('.selDevice');
    const selRes = tile.querySelector('.selRes');
    const zoneEnabled = tile.querySelector('.zoneEnabled');
    const cameraStatusText = tile.querySelector('.cameraStatusText');
    const detectStatusText = tile.querySelector('.detectStatusText');

    const detector = new Detector({ name, videoEl: vid, overlayEl: ov, recordEl: rc });
    zoneEnabled.checked = detector.isZoneEnabled();

    const tileObj = {
      name,
      el: tile,
      vid,
      ov,
      rc,
      detector,
      stream: null,
      uploadTimerId: null,
      statusTimerId: null,
      activeCameraId: null,

      updateStatus() {
        cameraStatusText.textContent = this.stream ? t('cameraOn') : t('cameraOff');
        cameraStatusText.style.color = this.stream ? 'var(--accent)' : 'var(--muted)';
        detectStatusText.textContent = this.detector?.isDetecting ? t('detectOnStatus') : t('detectOffStatus');
        detectStatusText.style.color = this.detector?.isDetecting ? 'var(--accent)' : 'var(--muted)';
      },

      populateDevices() {
        const cur = selDevice.value;
        selDevice.innerHTML = '';
        const optAuto = document.createElement('option');
        optAuto.value = '';
        optAuto.textContent = t('autoDefault');
        selDevice.appendChild(optAuto);

        for (let i = 0; i < deviceList.length; i++) {
          const d = deviceList[i];
          const opt = document.createElement('option');
          opt.value = d.deviceId;
          opt.textContent = d.label || `Camera ${i+1}`;
          selDevice.appendChild(opt);
        }
        if (cur) selDevice.value = cur;
      },

      async start() {
        await this.stop();
        const deviceId = selDevice.value || null;
        const res = selRes.value;

        // Build browser camera constraints from the selected device/resolution.
        // The browser may still choose a nearby resolution depending on hardware.
        const constraints = { video: {}, audio:false };
        if (deviceId) constraints.video.deviceId = { exact: deviceId };
        if (res === '720') { constraints.video.width = { ideal: 1280 }; constraints.video.height = { ideal: 720 }; }
        if (res === '1080') { constraints.video.width = { ideal: 1920 }; constraints.video.height = { ideal: 1080 }; }

        try {
          this.stream = await navigator.mediaDevices.getUserMedia(constraints);
          vid.srcObject = this.stream;
          this.updateStatus();
          setTimeout(() => {
            fitOverlayToVideo(vid, ov);
            this.detector.drawIdleZone();
          }, 150);
          const cameraId = `${name}_${Date.now()}`;
          this.activeCameraId = cameraId;
          await registerCamera(name, cameraId);
          await postCameraStatus(cameraId, false);
          // Heartbeat and frame upload timers are stopped together with the
          // camera, so a removed tile does not keep sending stale updates.
          this.statusTimerId = window.setInterval(() => {
            postCameraStatus(cameraId, this.detector && this.detector.isDetecting);
          }, 3000);
          this.uploadTimerId = startStreamUpload(vid, rc, cameraId);
        } catch (e) {
          this.updateStatus();
        }
      },

      async stop() {
        this.detector.stop();
        if (this.statusTimerId) {
          clearInterval(this.statusTimerId);
          this.statusTimerId = null;
        }
        if (this.uploadTimerId) {
          clearInterval(this.uploadTimerId);
          this.uploadTimerId = null;
        }
        this.activeCameraId = null;
        if (this.stream) {
          try { this.stream.getTracks().forEach(t => t.stop()); } catch (_) {}
        }
        this.stream = null;
        vid.srcObject = null;
        this.updateStatus();
        const ctx = ov.getContext('2d');
        ctx.clearRect(0, 0, ov.width, ov.height);
      },

      remove() { this.stop(); tile.remove(); }
    };

    tileObj.applyI18n = function() {
      const removeBtn = tile.querySelector('.btnRemove');
      const deviceLabel = tile.querySelector('.lblDevice');
      const resLabel = tile.querySelector('.lblRes');
      const defaultOption = selRes.querySelector('option[value="default"]');
      const startBtn = tile.querySelector('.btnStart');
      const stopBtn = tile.querySelector('.btnStop');
      const detOnBtn = tile.querySelector('.btnDetOn');
      const detOffBtn = tile.querySelector('.btnDetOff');
      const sourceLabel = tile.querySelector('.sourceLabel');
      const cameraStatusLabel = tile.querySelector('.cameraStatusLabel');
      const detectionStatusLabel = tile.querySelector('.detectionStatusLabel');
      const zoneEnabledLabel = tile.querySelector('.zoneEnabledLabel');

      if (removeBtn) removeBtn.textContent = t('remove');
      if (deviceLabel) deviceLabel.textContent = t('cameraDevice');
      if (resLabel) resLabel.textContent = t('resolutionHint');
      if (defaultOption) defaultOption.textContent = t('defaultOption');
      if (startBtn) startBtn.textContent = t('start');
      if (stopBtn) stopBtn.textContent = t('stop');
      if (detOnBtn) detOnBtn.textContent = t('detectOn');
      if (detOffBtn) detOffBtn.textContent = t('detectOff');
      if (sourceLabel) sourceLabel.textContent = t('sourceLabel');
      if (cameraStatusLabel) cameraStatusLabel.textContent = t('cameraStatus');
      if (detectionStatusLabel) detectionStatusLabel.textContent = t('detectionStatus');
      if (zoneEnabledLabel) zoneEnabledLabel.textContent = t('protectedZone');
      this.updateStatus();
      this.populateDevices();
    };

    tileObj.populateDevices();
    tileObj.applyI18n();

    tile.querySelector('.btnStart').addEventListener('click', () => tileObj.start());
    tile.querySelector('.btnStop').addEventListener('click', () => tileObj.stop());
    tile.querySelector('.btnDetOn').addEventListener('click', () => {
      tileObj.detector.start();
      tileObj.updateStatus();
      postCameraStatus(tileObj.activeCameraId, true);
    });
    tile.querySelector('.btnDetOff').addEventListener('click', () => {
      tileObj.detector.stop();
      tileObj.updateStatus();
      postCameraStatus(tileObj.activeCameraId, false);
    });
    zoneEnabled.addEventListener('change', () => {
      tileObj.detector.setZoneEnabled(zoneEnabled.checked);
    });
    tile.querySelector('.btnRemove').addEventListener('click', () => {
      camTiles = camTiles.filter(x => x !== tileObj);
      tileObj.remove();
    });

    return tileObj;
  }

  // Start all registered camera previews.
  async function startAll() {
    for (const t of camTiles) {
      await t.start();
    }
  }
  // Stop every camera preview and release browser media tracks.
  async function stopAll() {
    for (const t of camTiles) await t.stop();
  }
  // Enable detection for every camera that is already running.
  function detectAllOn() {
    for (const t of camTiles) {
      if (t.stream) {
        t.detector.start();
        t.updateStatus();
        postCameraStatus(t.activeCameraId, true);
      }
    }
  }
  // Disable detection for all camera tiles.
  function detectAllOff() {
    for (const t of camTiles) {
      t.detector.stop();
      t.updateStatus();
      postCameraStatus(t.activeCameraId, false);
    }
  }

  dashAddCam.addEventListener('click', async () => { if (!deviceList.length) await refreshDevices(); camTiles.push(createCamTile()); });
  dashRefreshDevices.addEventListener('click', refreshDevices);
  dashStartAll.addEventListener('click', startAll);
  dashStopAll.addEventListener('click', stopAll);
  dashDetectAll.addEventListener('click', detectAllOn);
  dashDetectOffAll.addEventListener('click', detectAllOff);

  liveAddCam.addEventListener('click', async () => { if (!deviceList.length) await refreshDevices(); camTiles.push(createCamTile()); moveTilesTo('live'); });
  liveRefreshDevices.addEventListener('click', refreshDevices);
  liveStartAll.addEventListener('click', startAll);
  liveStopAll.addEventListener('click', stopAll);
  liveDetectAll.addEventListener('click', detectAllOn);
  liveDetectOffAll.addEventListener('click', detectAllOff);
  window.addEventListener('i18n-updated', () => {
    for (const tile of camTiles) tile.applyI18n?.();
  });

  // =======================
  // File mode detector + alert
  // =======================
  const fileVideo = document.getElementById('fileVideo');
  const fileOverlay = document.getElementById('fileOverlay');
  const fileRecord = document.getElementById('fileRecord');
  const fileDetector = new Detector({ name: 'FILE', videoEl: fileVideo, overlayEl: fileOverlay, recordEl: fileRecord });

  const fileInput = document.getElementById('fileInput');
  fileInput.addEventListener('change', () => {
    const f = fileInput.files && fileInput.files[0];
    if (!f) return;
    const url = URL.createObjectURL(f);
    fileVideo.src = url;
    fileVideo.play().catch(() => {});
    setTimeout(() => {
      fitOverlayToVideo(fileVideo, fileOverlay);
      fileDetector.drawIdleZone();
    }, 200);
  });

  document.getElementById('btnStartFileDetect').addEventListener('click', () => fileDetector.start());
  document.getElementById('btnStopFileDetect').addEventListener('click', () => fileDetector.stop());

  // =======================
  // Init
  // =======================
  initThemeLang();

  // Register one local camera id with the client backend and central server.
  async function registerCamera(cameraName, cameraId) {
    const response = await fetch('/api/camera/register', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            camera_name: cameraName,
            camera_id: cameraId
        })
    });
    return await response.json();
}

// Best-effort heartbeat so the central monitor knows if a camera is detecting.
async function postCameraStatus(cameraId, isDetecting) {
    if (!cameraId) return;
    try {
        await fetch('/api/camera/status', {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                camera_id: cameraId,
                is_detecting: !!isDetecting
            })
        });
    } catch (_) {}
}

const monitorUploadState = {
    active: false,
    lastCheckedAt: 0,
    inflight: null
};

// Cache monitor status briefly to avoid asking the backend for every frame.
async function refreshMonitorUploadState(force = false) {
    const now = Date.now();
    if (!force && monitorUploadState.inflight) {
        return monitorUploadState.inflight;
    }
    if (!force && (now - monitorUploadState.lastCheckedAt) < 3000) {
        return { ok: true, active: monitorUploadState.active };
    }

    monitorUploadState.inflight = fetch('/api/client/monitor-status', {
        method: 'GET',
        credentials: 'include'
    }).then(async (response) => {
        const payload = await response.json().catch(() => ({}));
        monitorUploadState.lastCheckedAt = Date.now();
        monitorUploadState.active = !!payload.active;
        return payload;
    }).catch((error) => {
        monitorUploadState.lastCheckedAt = Date.now();
        monitorUploadState.active = false;
        return { ok: false, error: error.message || 'monitor status failed' };
    }).finally(() => {
        monitorUploadState.inflight = null;
    });

    return monitorUploadState.inflight;
}

// Burn a timestamp into frames sent to the central live monitor.
function drawMonitorTimestamp(ctx, width, height) {
    const ts = new Date();
    const stampText =
        `${ts.getFullYear()}-${String(ts.getMonth()+1).padStart(2,'0')}-${String(ts.getDate()).padStart(2,'0')} ` +
        `${String(ts.getHours()).padStart(2,'0')}:${String(ts.getMinutes()).padStart(2,'0')}:${String(ts.getSeconds()).padStart(2,'0')}`;

    ctx.save();
    ctx.font = '16px ui-sans-serif';
    ctx.textBaseline = 'top';
    const padX = 10;
    const padY = 8;
    const boxX = 10;
    const boxY = 10;
    const boxW = ctx.measureText(stampText).width + (padX * 2);
    const boxH = 30;
    ctx.fillStyle = 'rgba(2,6,23,0.72)';
    ctx.fillRect(boxX, boxY, boxW, boxH);
    ctx.fillStyle = 'rgba(229,231,235,0.96)';
    ctx.fillText(stampText, boxX + padX, boxY + padY);
    ctx.restore();
}

// Periodically send low-rate JPEG frames only while central monitoring is active.
function startStreamUpload(videoElement, recordCanvas, cameraId) {
    const canvas = document.createElement('canvas');
    canvas.width = 640;
    canvas.height = 480;
    const ctx = canvas.getContext('2d');
    
    return setInterval(async () => {
        const monitor = await refreshMonitorUploadState(false);
        if (!monitor.ok || !monitor.active) return;
        if (!videoElement.srcObject || videoElement.readyState < 2) return;
        const tile = camTiles.find((item) => item.activeCameraId === cameraId);
        const isDetecting = !!(tile && tile.detector && tile.detector.isDetecting);
        if (tile && tile.detector && !isDetecting) {
            tile.detector.drawIdleZone();
        }
        const hasAnnotatedFrame = !!(
            recordCanvas &&
            recordCanvas.width > 0 &&
            recordCanvas.height > 0
        );

        const onBlobReady = (blob) => {
            if (!blob) return;
            const formData = new FormData();
            formData.append('file', blob);
            formData.append('camera_id', cameraId);
            formData.append('is_detecting', isDetecting ? 'true' : 'false');
            fetch('/api/camera/stream', {
                method: 'POST',
                credentials: 'include',
                body: formData
            }).catch((error) => {
                console.error('Upload failed:', error);
            });
        };

        if (hasAnnotatedFrame) {
            // Prefer the annotation canvas so the central monitor sees the same
            // boxes and zone overlay as the local operator.
            recordCanvas.toBlob(onBlobReady, 'image/jpeg', 0.85);
            return;
        }

        // If detection has not drawn an annotated frame yet, send a plain camera
        // frame with a timestamp so the admin can still see that the feed is live.
        ctx.drawImage(videoElement, 0, 0, canvas.width, canvas.height);
        drawMonitorTimestamp(ctx, canvas.width, canvas.height);
        canvas.toBlob(onBlobReady, 'image/jpeg', 0.8);
    }, 500);
}

  (async () => {
    await refreshDevices();
    camTiles.push(createCamTile()); // initial camera tile
    setStatus(t('idle'), true);
  })();


