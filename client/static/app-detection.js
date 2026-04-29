  // Main browser-side detection workflow. It draws overlays, records clips,
  // sends frames to Flask, and writes detection events into local logs.
  // =======================
  // Video overlay + annotation recording
  // =======================
  // Match the canvas overlay size to the visible video element.
  function fitOverlayToVideo(videoEl, overlayEl) {
    if (!videoEl || !overlayEl) return { rect: { width: 1, height: 1 }, dpr: 1 };
    const rect = videoEl.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const cssWidth = Math.max(1, Math.floor(rect.width));
    const cssHeight = Math.max(1, Math.floor(rect.height));

    overlayEl.style.position = 'absolute';
    overlayEl.style.left = '0';
    overlayEl.style.top = '0';
    overlayEl.style.zIndex = '2';
    overlayEl.style.pointerEvents = 'none';
    overlayEl.style.width = cssWidth + 'px';
    overlayEl.style.height = cssHeight + 'px';
    overlayEl.style.transform = 'translateZ(0)';
    overlayEl.style.willChange = 'transform';

    videoEl.style.position = 'relative';
    videoEl.style.zIndex = '1';
    videoEl.style.transform = 'translateZ(0)';

    overlayEl.width = Math.max(1, Math.floor(cssWidth * dpr));
    overlayEl.height = Math.max(1, Math.floor(cssHeight * dpr));
    return { rect: { width: cssWidth, height: cssHeight }, dpr };
  }

  // Keep overlay dimensions correct when video metadata or layout changes.
  function installOverlaySync(videoEl, overlayEl) {
    if (!videoEl || !overlayEl || videoEl.__overlaySyncInstalled) return;
    videoEl.__overlaySyncInstalled = true;

    const sync = () => fitOverlayToVideo(videoEl, overlayEl);
    videoEl.addEventListener('loadedmetadata', sync);
    videoEl.addEventListener('loadeddata', sync);
    videoEl.addEventListener('play', sync);
    videoEl.addEventListener('resize', sync);

    if (typeof ResizeObserver !== 'undefined') {
      const ro = new ResizeObserver(sync);
      ro.observe(videoEl);
      const parent = videoEl.parentElement;
      if (parent) ro.observe(parent);
      videoEl.__overlayResizeObserver = ro;
    }
  }

  // Choose a browser-supported recording format for event clips.
  function pickMimeType() {
    if (typeof MediaRecorder === 'undefined') return '';
    const candidates = [
      'video/webm;codecs=vp9,opus',
      'video/webm;codecs=vp8,opus',
      'video/webm'
    ];
    for (const c of candidates) if (MediaRecorder.isTypeSupported(c)) return c;
    return '';
  }

  async function uploadClipToServer(blob, source, eventId) {
    const fd = new FormData();
    fd.append('file', blob, `clip_${Date.now()}.webm`);
    fd.append('source', source);
    fd.append('event_id', String(eventId || ''));
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), 20000);
    try {
      const resp = await fetch(CLIP_SAVE_ENDPOINT, {
        method: 'POST',
        body: fd,
        signal: controller.signal
      });
      if (!resp.ok) throw new Error('upload failed');
      return await resp.json(); // {ok:true, filename:"..."}
    } finally {
      window.clearTimeout(timeoutId);
    }
  }

  async function frameToDataUrl(videoEl, quality=0.6) {
    const vw = videoEl.videoWidth || 0;
    const vh = videoEl.videoHeight || 0;
    if (!vw || !vh) return null;
    const c = document.createElement('canvas');
    c.width = vw; c.height = vh;
    const ctx = c.getContext('2d');
    ctx.drawImage(videoEl, 0, 0, vw, vh);
    return c.toDataURL('image/jpeg', quality);
  }

  async function reportDetectorState(source, isDetecting) {
    try {
      const apiFetch = window.authApi && typeof window.authApi.apiFetch === 'function'
        ? window.authApi.apiFetch
        : null;
      if (apiFetch) {
        const payload = await apiFetch('/api/client/detector_state', {
          method: 'POST',
          body: {
            source,
            is_detecting: !!isDetecting
          }
        });
        if (payload?.applied_model && typeof window.refreshClientModelStatus === 'function') {
          window.refreshClientModelStatus();
        }
        return;
      }

      await fetch('/api/client/detector_state', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          source,
          is_detecting: !!isDetecting
        })
      });
    } catch (_) {
      // Best-effort idle/reporting hook.
    }
  }

  async function triggerNodeSync() {
    try {
      const apiFetch = window.authApi && typeof window.authApi.apiFetch === 'function'
        ? window.authApi.apiFetch
        : null;
      if (apiFetch) {
        await apiFetch('/api/node/sync', { method: 'POST' });
        return;
      }
      await fetch('/api/node/sync', {
        method: 'POST',
        credentials: 'include'
      });
    } catch (_) {
      // Best-effort sync trigger.
    }
  }

  // Normalize a drawn detection zone so later geometry math is easier.
  function normalizeZone(zone) {
    const src = zone || {};
    const xPct = Math.max(0, Math.min(95, Number(src.x ?? 25)));
    const yPct = Math.max(0, Math.min(95, Number(src.y ?? 25)));
    const wPct = Math.max(5, Math.min(100 - xPct, Number(src.w ?? 50)));
    const hPct = Math.max(5, Math.min(100 - yPct, Number(src.h ?? 50)));
    return {
      enabled: src.enabled !== false,
      x: xPct,
      y: yPct,
      w: wPct,
      h: hPct
    };
  }

  // Convert the saved relative zone into pixel coordinates for the video.
  function zoneToPixels(zone, videoWidth, videoHeight) {
    const z = normalizeZone(zone);
    if (!z.enabled || !videoWidth || !videoHeight) return null;

    return {
      x1: (z.x / 100) * videoWidth,
      y1: (z.y / 100) * videoHeight,
      x2: ((z.x + z.w) / 100) * videoWidth,
      y2: ((z.y + z.h) / 100) * videoHeight,
      enabled: true
    };
  }

  // Check if a detection box overlaps the selected warning zone.
  function detectionIntersectsZone(det, zone) {
    if (!det || !zone) return false;
    const cx = ((det.x1 || 0) + (det.x2 || 0)) / 2;
    const cy = ((det.y1 || 0) + (det.y2 || 0)) / 2;
    const centerInside = cx >= zone.x1 && cx <= zone.x2 && cy >= zone.y1 && cy <= zone.y2;
    const overlaps = (det.x1 || 0) < zone.x2 && (det.x2 || 0) > zone.x1 && (det.y1 || 0) < zone.y2 && (det.y2 || 0) > zone.y1;
    return centerInside || overlaps;
  }

  // Add intrusion and threat labels to detections before drawing/logging.
  function annotateThreats(detections, zone, videoWidth, videoHeight, zoneEnteredAt, inEvent) {
    const now = Date.now();
    const inZone = !!zone && detections.some(det => detectionIntersectsZone(det, zone));
    const nextZoneEnteredAt = inZone ? (zoneEnteredAt || now) : null;
    const dwellMs = nextZoneEnteredAt ? now - nextZoneEnteredAt : 0;

    const settings = (typeof window.getClientSettings === 'function') ? window.getClientSettings() : {};
    const highAfterMs = Math.max(1, Math.min(30, Number(settings.high_threat_seconds ?? 3))) * 1000;
    const mediumConfidence = Math.max(0.05, Math.min(0.99, Number(settings.medium_confidence ?? 0.75)));
    const mediumAreaRatio = Math.max(0.001, Math.min(0.8, Number(settings.medium_box_pct ?? 8) / 100));
    const frameArea = Math.max(1, videoWidth * videoHeight);
    const maxConfidence = detections.reduce((max, det) => Math.max(max, Number(det.confidence || 0)), 0);
    const maxAreaPct = detections.reduce((max, det) => {
      const areaPct = (((det.width || 0) * (det.height || 0)) / frameArea) * 100;
      return Math.max(max, areaPct);
    }, 0);

    let eventThreat = 'Low';
    const annotated = detections.map((det) => {
      const confidence = Number(det.confidence || 0);
      const areaRatio = Math.max(0, ((det.width || 0) * (det.height || 0)) / frameArea);
      const isIntrusion = detectionIntersectsZone(det, zone);
      let threat = 'Low';
      if (isIntrusion && dwellMs >= highAfterMs) {
        threat = 'High';
      } else if (isIntrusion || inEvent || confidence >= mediumConfidence || areaRatio >= mediumAreaRatio) {
        threat = 'Medium';
      }
      if (threat === 'High') eventThreat = 'High';
      else if (threat === 'Medium' && eventThreat !== 'High') eventThreat = 'Medium';
      return {
        ...det,
        in_protected_zone: isIntrusion,
        threat_level: threat
      };
    });

    return {
      detections: annotated,
      intrusion: inZone,
      threat: eventThreat,
      zoneEnteredAt: nextZoneEnteredAt,
      dwellMs,
      maxConfidence,
      maxAreaPct
    };
  }

  function formatSeconds(ms) {
    return `${(Math.max(0, ms || 0) / 1000).toFixed(1)}s`;
  }

  function formatEventText(eventId, intrusion, threat, details = {}) {
    const eventType = intrusion ? 'Intrusion' : 'Detection';
    const zoneText = details.zoneEnabled === false ? 'Zone: off' : (intrusion ? 'Zone: entered' : 'Zone: clear');
    const confText = `Detection conf: ${Math.round((details.confidence || 0) * 100)}%`;
    const sizeText = `Box size: ${(details.areaPct || 0).toFixed(1)}%`;
    const dwellText = intrusion ? `Zone time: ${formatSeconds(details.dwellMs || 0)}` : null;
    return [`DRONE #${eventId}`, `Type: ${eventType}`, `Threat: ${threat}`, zoneText, confText, sizeText, dwellText].filter(Boolean).join(' | ');
  }

  function persistentClipText(value) {
    const text = String(value || '').trim();
    if (!text || text === t('saving') || text.toLowerCase() === 'saving...') return '-';
    return text;
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function videoDisplayGeometry(videoEl, overlayEl) {
    const rect = overlayEl.getBoundingClientRect();
    const vw = videoEl.videoWidth || 0;
    const vh = videoEl.videoHeight || 0;
    if (!vw || !vh || !rect.width || !rect.height) return null;
    const scale = Math.min(rect.width / vw, rect.height / vh);
    const dispW = vw * scale;
    const dispH = vh * scale;
    return {
      rect,
      vw,
      vh,
      scale,
      padX: (rect.width - dispW) / 2,
      padY: (rect.height - dispH) / 2
    };
  }

  function pointerToVideoPoint(event, videoEl, overlayEl) {
    const g = videoDisplayGeometry(videoEl, overlayEl);
    if (!g) return null;
    const cssX = event.clientX - g.rect.left;
    const cssY = event.clientY - g.rect.top;
    const x = (cssX - g.padX) / g.scale;
    const y = (cssY - g.padY) / g.scale;
    return { x: clamp(x, 0, g.vw), y: clamp(y, 0, g.vh), geometry: g };
  }

  function zoneHitMode(point, zonePx, scale) {
    if (!point || !zonePx) return null;
    const edge = Math.max(10, 12 / Math.max(scale || 1, 0.1));
    const inX = point.x >= zonePx.x1 && point.x <= zonePx.x2;
    const inY = point.y >= zonePx.y1 && point.y <= zonePx.y2;
    if (!inX || !inY) return null;

    const nearL = Math.abs(point.x - zonePx.x1) <= edge;
    const nearR = Math.abs(point.x - zonePx.x2) <= edge;
    const nearT = Math.abs(point.y - zonePx.y1) <= edge;
    const nearB = Math.abs(point.y - zonePx.y2) <= edge;

    if (nearL && nearT) return 'resize-nw';
    if (nearR && nearT) return 'resize-ne';
    if (nearL && nearB) return 'resize-sw';
    if (nearR && nearB) return 'resize-se';
    if (nearL) return 'resize-w';
    if (nearR) return 'resize-e';
    if (nearT) return 'resize-n';
    if (nearB) return 'resize-s';
    return 'move';
  }

  // Draw detection boxes, zone overlays, labels, and optional recording canvas.
  function drawOverlay(videoEl, overlayEl, recordEl, detections, extras) {
    const octx = overlayEl.getContext('2d');
    const { rect, dpr } = fitOverlayToVideo(videoEl, overlayEl);

    octx.setTransform(1,0,0,1,0,0);
    octx.clearRect(0, 0, overlayEl.width, overlayEl.height);

    const vw = videoEl.videoWidth || 0;
    const vh = videoEl.videoHeight || 0;
    if (!vw || !vh) return;

    const scale = Math.min(rect.width / vw, rect.height / vh);
    const dispW = vw * scale;
    const dispH = vh * scale;
    const padX = (rect.width - dispW) / 2;
    const padY = (rect.height - dispH) / 2;

    const sx = (dispW * dpr) / vw;
    const sy = (dispH * dpr) / vh;
    const offX = padX * dpr;
    const offY = padY * dpr;

    const ts = new Date();
    const stampText =
      `${ts.getFullYear()}-${String(ts.getMonth()+1).padStart(2,'0')}-${String(ts.getDate()).padStart(2,'0')} ` +
      `${String(ts.getHours()).padStart(2,'0')}:${String(ts.getMinutes()).padStart(2,'0')}:${String(ts.getSeconds()).padStart(2,'0')}`;

    function drawAll(ctx, mapFn, mul, drawRaw, rawEl) {
      if (drawRaw && rawEl) {
        ctx.setTransform(1,0,0,1,0,0);
        ctx.clearRect(0,0,ctx.canvas.width, ctx.canvas.height);
        ctx.drawImage(rawEl, 0, 0, ctx.canvas.width, ctx.canvas.height);
      }

      if (extras?.protectedZone) {
        const z1 = mapFn(extras.protectedZone.x1, extras.protectedZone.y1);
        const z2 = mapFn(extras.protectedZone.x2, extras.protectedZone.y2);
        const zx = z1.x;
        const zy = z1.y;
        const zw = z2.x - z1.x;
        const zh = z2.y - z1.y;
        const active = !!extras.intrusion;
        ctx.save();
        ctx.lineWidth = Math.max(2, 2 * mul);
        ctx.setLineDash([8 * mul, 6 * mul]);
        ctx.strokeStyle = active ? 'rgba(239,68,68,0.96)' : 'rgba(245,158,11,0.88)';
        ctx.fillStyle = active ? 'rgba(239,68,68,0.10)' : 'rgba(245,158,11,0.08)';
        ctx.fillRect(zx, zy, zw, zh);
        ctx.strokeRect(zx, zy, zw, zh);
        ctx.setLineDash([]);
        const zoneTag = active ? `${t('intrusion')} - ${extras.threat || t('threatMedium')}` : t('protectedZone');
        ctx.font = `${Math.max(12, 12 * mul)}px ui-sans-serif`;
        const pad = 6 * mul;
        const th = Math.max(16 * mul, 16);
        const tw = ctx.measureText(zoneTag).width;
        const tagX = Math.max(0, Math.min(zx + pad, ctx.canvas.width - tw - 2 * pad));
        const tagY = Math.max(0, Math.min(zy + pad, ctx.canvas.height - th - 2 * mul));
        ctx.fillStyle = 'rgba(2,6,23,0.78)';
        ctx.fillRect(tagX - pad, tagY - 2*mul, tw + 2*pad, th + 2*mul);
        ctx.fillStyle = active ? 'rgba(254,226,226,0.98)' : 'rgba(254,243,199,0.98)';
        ctx.fillText(zoneTag, tagX, tagY);
        ctx.restore();
      }

      // trail dots
      if (extras?.trail?.length) {
        for (let i = 0; i < extras.trail.length; i++) {
          const p = extras.trail[i];
          const mp = mapFn(p.x, p.y);
          const a = (i + 1) / extras.trail.length;
          const alpha = Math.max(0.08, 0.55 * a);
          const r = Math.max(2, (2 + 3 * a) * mul);
          ctx.fillStyle = `rgba(34,197,94,${alpha})`;
          ctx.beginPath();
          ctx.arc(mp.x, mp.y, r, 0, Math.PI * 2);
          ctx.fill();
        }
      }

      // boxes
      ctx.lineWidth = Math.max(2, 2 * mul);
      ctx.font = `${Math.max(12, 12 * mul)}px ui-sans-serif`;
      ctx.textBaseline = 'top';

      for (const det of (detections||[])) {
        const p1 = mapFn(det.x1, det.y1);
        const p2 = mapFn(det.x2, det.y2);
        const x = p1.x, y = p1.y, w = p2.x - p1.x, h = p2.y - p1.y;

        const conf = det.confidence ?? 0;
        const label = det.label ?? 'obj';

        const threat = det.threat_level || 'Low';
        const intrusion = !!det.in_protected_zone;
        const color = threat === 'High'
          ? { stroke: 'rgba(239,68,68,0.98)', fill: 'rgba(239,68,68,0.16)' }
          : threat === 'Medium'
            ? { stroke: 'rgba(245,158,11,0.98)', fill: 'rgba(245,158,11,0.15)' }
            : { stroke: 'rgba(34,197,94,0.95)', fill: 'rgba(34,197,94,0.15)' };

        ctx.strokeStyle = color.stroke;
        ctx.fillStyle = color.fill;
        ctx.fillRect(x, y, w, h);
        ctx.strokeRect(x, y, w, h);

        const tag = `${intrusion ? t('intrusion') : label} ${(conf*100).toFixed(0)}% ${threat}`;
        const pad = 6 * mul;
        const tw = ctx.measureText(tag).width;
        const th = Math.max(16 * mul, 16);

        ctx.fillStyle = 'rgba(2,6,23,0.75)';
        ctx.fillRect(x, Math.max(0, y - th - 2*mul), tw + 2*pad, th + 2*mul);
        ctx.fillStyle = 'rgba(229,231,235,0.95)';
        ctx.fillText(tag, x + pad, Math.max(0, y - th));
      }

      // curved prediction
      if (extras?.predCurve?.pts?.length) {
        const pts = extras.predCurve.pts;
        ctx.strokeStyle = 'rgba(34,197,94,0.95)';
        ctx.lineWidth = Math.max(3, 3 * mul);
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';

        ctx.beginPath();
        const s = mapFn(extras.predCurve.start.x, extras.predCurve.start.y);
        ctx.moveTo(s.x, s.y);

        for (let i = 0; i < pts.length; i++) {
          const p = mapFn(pts[i].x, pts[i].y);
          const next = (i < pts.length - 1) ? mapFn(pts[i+1].x, pts[i+1].y) : p;
          const mx = (p.x + next.x) / 2;
          const my = (p.y + next.y) / 2;
          ctx.quadraticCurveTo(p.x, p.y, mx, my);
        }
        ctx.stroke();

        const end = mapFn(pts[pts.length-1].x, pts[pts.length-1].y);
        ctx.fillStyle = 'rgba(34,197,94,1.0)';
        ctx.beginPath();
        ctx.arc(end.x, end.y, Math.max(4, 4 * mul), 0, Math.PI * 2);
        ctx.fill();
      }

      // timestamp
      ctx.font = `${Math.max(14, 14 * mul)}px ui-sans-serif`;
      const w = ctx.measureText(stampText).width + 16 * mul;
      const h = 24 * mul;
      ctx.fillStyle = 'rgba(2,6,23,0.70)';
      ctx.fillRect(10 * mul, 10 * mul, w, h);
      ctx.fillStyle = 'rgba(229,231,235,0.95)';
      ctx.fillText(stampText, 18 * mul, 12 * mul);
    }

    // visible overlay
    drawAll(
      octx,
      (x,y) => ({ x: offX + x * sx, y: offY + y * sy }),
      dpr,
      false,
      null
    );

    // record canvas at native video size (burn-in overlay)
    if (recordEl) {
      recordEl.width = vw;
      recordEl.height = vh;
      const rctx = recordEl.getContext('2d');
      drawAll(
        rctx,
        (x,y) => ({ x, y }),
        1,
        true,
        videoEl
      );
    }
  }

  // =======================
  // Detector (alerts + save clips + create stored logs)
  // =======================
  class Detector {
    constructor({ name, videoEl, overlayEl, recordEl, onEventFinalized }) {
      this.name = name;
      this.video = videoEl;
      this.overlay = overlayEl;
      this.recordEl = recordEl;
      this.onEventFinalized = onEventFinalized; // callback for stored logs

      this.timerId = null;
      this.abortCtl = null;
      this.renderFrameId = null;

      this.isDetecting = false;
      this.inEvent = false;
      this.eventId = 0;
      this.eventStartTs = null;
      this.lastSeenTs = 0;
      this.goneDelayMs = 1500;

      this.recorder = null;
      this.recChunks = [];
      this.detectInFlight = false;
      this.pendingClipText = null;
      this.latestDetections = [];
      this.latestExtras = { predCurve: null, trail: [] };
      this.latestDetectMs = null;
      this.zone = this.loadZone();
      this.zoneDrag = null;

      this.eventMaxMs = 2 * 60 * 1000;
      this.eventCapTimer = null;

      this.lastCenter = null;
      this.lastCenterT = null;
      this.vel = { vx: 0, vy: 0 };
      this.acc = { ax: 0, ay: 0 };
      this.predCurve = null;

      this.trail = [];
      this.trailMax = 10;
      this.trailKeepMs = 1400;

      this.recentRow = null; // dashboard recent row
      this.storedLogId = null; // server log id if created
      this.alertedThisEvent = false;
      this.alertedIntrusionThisEvent = false;
      this.zoneEnteredAt = null;
      this.eventThreat = 'Low';
      this.eventIntrusion = false;
      this.handleResize = () => fitOverlayToVideo(this.video, this.overlay);
      this.handleSettingsChanged = (event) => {
        if (!this.isDetecting) return;
        const nextFps = Number(event?.detail?.settings?.fps || fpsInput.value || 6);
        const prevFps = Number(event?.detail?.previous?.fps || nextFps);
        if (nextFps !== prevFps) this.restartTickLoop();
      };
      installOverlaySync(this.video, this.overlay);
      this.installZoneEditor();
      this.renderLoop = () => {
        if (!this.isDetecting) return;
        this.drawCurrentOverlay();
        this.renderFrameId = window.requestAnimationFrame(this.renderLoop);
      };
    }

    zoneStorageKey() {
      return `skyguard.protectedZone.${this.name}`;
    }

    loadZone() {
      try {
        const raw = localStorage.getItem(this.zoneStorageKey());
        if (raw) return normalizeZone(JSON.parse(raw));
      } catch (_) {}
      return normalizeZone({ enabled: true, x: 25, y: 25, w: 50, h: 50 });
    }

    saveZone() {
      try {
        localStorage.setItem(this.zoneStorageKey(), JSON.stringify(normalizeZone(this.zone)));
      } catch (_) {}
    }

    setZoneEnabled(enabled) {
      this.zone = normalizeZone({ ...this.zone, enabled: !!enabled });
      this.zoneEnteredAt = null;
      this.saveZone();
      this.latestExtras = this.buildOverlayExtras({
        intrusion: false,
        threat: this.latestExtras?.threat || 'Low'
      });
      this.drawCurrentOverlay();
    }

    isZoneEnabled() {
      return normalizeZone(this.zone).enabled;
    }

    getZonePixels() {
      const vw = this.video.videoWidth || 0;
      const vh = this.video.videoHeight || 0;
      return zoneToPixels(this.zone, vw, vh);
    }

    buildOverlayExtras(extra = {}) {
      const zone = this.getZonePixels();
      return {
        predCurve: this.predCurve,
        trail: this.trail.slice().reverse(),
        protectedZone: zone,
        intrusion: false,
        threat: 'Low',
        ...extra
      };
    }

    drawCurrentOverlay() {
      fitOverlayToVideo(this.video, this.overlay);
      drawOverlay(this.video, this.overlay, this.recordEl, this.latestDetections, this.latestExtras);
    }

    drawIdleZone() {
      if (this.isDetecting) return;
      fitOverlayToVideo(this.video, this.overlay);
      this.latestDetections = [];
      this.latestExtras = this.buildOverlayExtras();
      drawOverlay(this.video, this.overlay, this.recordEl, [], this.latestExtras);
    }

    installZoneEditor() {
      if (!this.overlay || this.overlay.__zoneEditorInstalled) return;
      this.overlay.__zoneEditorInstalled = true;
      const eventTarget = this.overlay.parentElement || this.overlay;
      eventTarget.style.touchAction = 'none';

      const startDrag = (event) => {
        const point = pointerToVideoPoint(event, this.video, this.overlay);
        const zonePx = this.getZonePixels();
        const mode = zoneHitMode(point, zonePx, point?.geometry?.scale);
        if (!point || !mode) return;

        event.preventDefault();
        eventTarget.setPointerCapture?.(event.pointerId);
        this.zoneDrag = {
          mode,
          pointerId: event.pointerId,
          startPoint: point,
          startZone: normalizeZone(this.zone)
        };
      };

      const updateDrag = (event) => {
        const point = pointerToVideoPoint(event, this.video, this.overlay);
        if (!point) return;

        if (!this.zoneDrag) {
          const mode = zoneHitMode(point, this.getZonePixels(), point.geometry.scale);
          eventTarget.style.cursor = mode ? (mode === 'move' ? 'move' : `${mode.replace('resize-', '')}-resize`) : 'default';
          return;
        }
        if (event.pointerId !== this.zoneDrag.pointerId) return;

        event.preventDefault();
        const start = this.zoneDrag.startZone;
        const dxPct = ((point.x - this.zoneDrag.startPoint.x) / point.geometry.vw) * 100;
        const dyPct = ((point.y - this.zoneDrag.startPoint.y) / point.geometry.vh) * 100;
        let x = start.x;
        let y = start.y;
        let w = start.w;
        let h = start.h;
        const minSize = 5;

        if (this.zoneDrag.mode === 'move') {
          x = clamp(start.x + dxPct, 0, 100 - start.w);
          y = clamp(start.y + dyPct, 0, 100 - start.h);
        } else {
          if (this.zoneDrag.mode.includes('e')) w = clamp(start.w + dxPct, minSize, 100 - start.x);
          if (this.zoneDrag.mode.includes('s')) h = clamp(start.h + dyPct, minSize, 100 - start.y);
          if (this.zoneDrag.mode.includes('w')) {
            x = clamp(start.x + dxPct, 0, start.x + start.w - minSize);
            w = clamp((start.x + start.w) - x, minSize, 100 - x);
          }
          if (this.zoneDrag.mode.includes('n')) {
            y = clamp(start.y + dyPct, 0, start.y + start.h - minSize);
            h = clamp((start.y + start.h) - y, minSize, 100 - y);
          }
        }

        this.zone = normalizeZone({ enabled: this.isZoneEnabled(), x, y, w, h });
        this.latestExtras = this.buildOverlayExtras({
          intrusion: !!this.latestExtras?.intrusion,
          threat: this.latestExtras?.threat || 'Low'
        });
        this.drawCurrentOverlay();
      };

      const endDrag = (event) => {
        if (!this.zoneDrag || event.pointerId !== this.zoneDrag.pointerId) return;
        event.preventDefault();
        eventTarget.releasePointerCapture?.(event.pointerId);
        this.zoneDrag = null;
        this.saveZone();
      };

      eventTarget.addEventListener('pointerdown', startDrag);
      eventTarget.addEventListener('pointermove', updateDrag);
      eventTarget.addEventListener('pointerup', endDrag);
      eventTarget.addEventListener('pointercancel', endDrag);
      this.video.addEventListener('loadedmetadata', () => this.drawIdleZone());
      this.video.addEventListener('loadeddata', () => this.drawIdleZone());
    }

    getTickInterval() {
      const fps = Math.max(1, Math.min(15, Number(fpsInput.value) || 6));
      return Math.floor(1000 / fps);
    }

    restartTickLoop() {
      if (this.timerId) clearInterval(this.timerId);
      this.timerId = setInterval(() => this.tick(), this.getTickInterval());
    }

    start() {
      this.stop();

      this.isDetecting = true;
      setStatus(`${this.name}: ${t('detecting')}`, true);

      fitOverlayToVideo(this.video, this.overlay);
      this.restartTickLoop();
      this.renderFrameId = window.requestAnimationFrame(this.renderLoop);
      window.removeEventListener('resize', this.handleResize);
      window.addEventListener('resize', this.handleResize, { passive: true });
      window.removeEventListener('client-settings-changed', this.handleSettingsChanged);
      window.addEventListener('client-settings-changed', this.handleSettingsChanged);
      reportDetectorState(this.name, true);
    }

    stop() {
      this.finalizeActiveEventTime();

      this.isDetecting = false;
      if (this.timerId) { clearInterval(this.timerId); this.timerId = null; }
      if (this.abortCtl) { this.abortCtl.abort(); this.abortCtl = null; }
      if (this.renderFrameId) { cancelAnimationFrame(this.renderFrameId); this.renderFrameId = null; }

      this.stopRecordingIfAny();

      this.inEvent = false;
      this.eventStartTs = null;
      this.pendingClipText = null;
      this.latestDetections = [];
      this.latestExtras = { predCurve: null, trail: [] };
      this.latestDetectMs = null;
      this.recentRow = null;
      this.storedLogId = null;
      this.alertedThisEvent = false;
      this.alertedIntrusionThisEvent = false;
      this.zoneEnteredAt = null;
      this.eventThreat = 'Low';
      this.eventIntrusion = false;

      this.clearMotion();
      window.removeEventListener('resize', this.handleResize);
      window.removeEventListener('client-settings-changed', this.handleSettingsChanged);

      const ctx = this.overlay.getContext('2d');
      ctx.clearRect(0, 0, this.overlay.width, this.overlay.height);
      this.drawIdleZone();
      setStatus(t('idle'), true);
      reportDetectorState(this.name, false);
    }

    finalizeActiveEventTime() {
      if (!this.inEvent || !this.eventStartTs) return;

      // The final event time is only known when detection stops or the object
      // disappears, so the row created at event start is updated here.
      const start = this.eventStartTs;
      const end = Date.now();
      const timeText = `${fmtHHMMSS(start)} - ${fmtHHMMSS(end)}`;
      const eventText = this.recentRow?.tdE?.textContent || formatEventText(this.eventId, this.eventIntrusion, this.eventThreat);

      if (this.recentRow?.tdT) this.recentRow.tdT.textContent = timeText;

      if (this.activeClipContext) {
        this.activeClipContext.timeText = timeText;
        this.activeClipContext.eventText = eventText;
      }

      if (this.storedLogId) {
        fetch(LOGS_UPDATE_ENDPOINT, {
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body: JSON.stringify({
            id: this.storedLogId,
            time: timeText,
            event: eventText,
            source: this.name,
            clip: persistentClipText(this.pendingClipText || (autoClip.checked ? '-' : t('clipOff')))
          })
        }).catch(() => {});
      }
    }

    clearMotion() {
      this.lastCenter = null;
      this.lastCenterT = null;
      this.vel = { vx: 0, vy: 0 };
      this.acc = { ax: 0, ay: 0 };
      this.predCurve = null;
      this.trail = [];
    }

    getRecordStream() {
      if (!this.recordEl || !this.recordEl.captureStream) return null;
      return this.recordEl.captureStream(30);
    }

    async createStoredLogSkeleton(timeText, eventText) {
      // Create the log early, then update the clip field after recording ends.
      try {
        const r = await fetch(LOGS_CREATE_ENDPOINT, {
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body: JSON.stringify({
            time: timeText,
            event: eventText,
            source: this.name,
            clip: persistentClipText(this.pendingClipText || (autoClip.checked ? '-' : t('clipOff')))
          })
        });
        if (!r.ok) throw new Error('bad');
        const js = await r.json();
        this.storedLogId = js.id ?? null;
      } catch (_) {
        this.storedLogId = null;
      }
    }

    async updateStoredLogClip(clipText, timeText, eventText, storedLogId = this.storedLogId) {
      if (!storedLogId) return;
      try {
        await fetch(LOGS_UPDATE_ENDPOINT, {
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body: JSON.stringify({
            id: storedLogId,
            time: timeText,
            event: eventText,
            source: this.name,
            clip: persistentClipText(clipText)
          })
        });
      } catch (_) {}
    }

    async finalizeClipStatus(clipText, clipContext = null) {
      this.pendingClipText = clipText;
      // Keep the dashboard row and the stored SQLite log in sync after the
      // MediaRecorder has either saved a clip or failed.
      const context = clipContext || this.activeClipContext || {
        recentRow: this.recentRow,
        storedLogId: this.storedLogId,
        timeText: this.recentRow?.tdT?.textContent || '',
        eventText: this.recentRow?.tdE?.textContent || ''
      };

      if (context.recentRow?.tdL) {
        context.recentRow.tdL.textContent = clipText;
        context.recentRow.tdL.style.color = String(clipText).startsWith(t('savedPrefix')) ? 'var(--text)' : 'var(--muted)';
      }

      const timeText = context.timeText || context.recentRow?.tdT?.textContent || '';
      const eventText = context.eventText || context.recentRow?.tdE?.textContent || '';
      await this.updateStoredLogClip(clipText, timeText, eventText, context.storedLogId);
      triggerNodeSync();
    }

    startRecordingForEvent() {
      if (!autoClip.checked) {
        this.pendingClipText = t('clipOff');
        this.finalizeClipStatus(this.pendingClipText);
        return;
      }

      const stream = this.getRecordStream();
      if (!stream || typeof MediaRecorder === 'undefined') {
        this.pendingClipText = 'Clip unsupported';
        this.finalizeClipStatus(this.pendingClipText);
        return;
      }

      const mimeType = pickMimeType();
      let rec;
      try {
        rec = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
      } catch (_) {
        this.pendingClipText = 'Clip recorder error';
        this.finalizeClipStatus(this.pendingClipText);
        return;
      }

      this.pendingClipText = t('saving');
      this.recChunks = [];

      const clipContext = {
        recentRow: this.recentRow,
        storedLogId: this.storedLogId,
        timeText: this.recentRow?.tdT?.textContent || '',
        eventText: this.recentRow?.tdE?.textContent || ''
      };
      this.activeClipContext = clipContext;

      // Save chunks in memory and upload one WebM file when recording stops.
      const recChunks = [];
      rec.ondataavailable = (ev) => { if (ev.data && ev.data.size > 0) recChunks.push(ev.data); };

      rec.onstop = async () => {
        const blob = new Blob(recChunks, { type: rec.mimeType || 'video/webm' });
        if (!blob.size) {
          await this.finalizeClipStatus('Save failed', clipContext);
          if (this.activeClipContext === clipContext) this.activeClipContext = null;
          return;
        }
        try {
          const res = await uploadClipToServer(blob, this.name, this.eventId);
          await this.finalizeClipStatus(`${t('savedPrefix')} ${res.filename}`, clipContext);
        } catch (_) {
          await this.finalizeClipStatus('Save failed', clipContext);
        } finally {
          if (this.activeClipContext === clipContext) this.activeClipContext = null;
          try { stream.getTracks().forEach((track) => track.stop()); } catch (_) {}
        }
      };

      rec.start(250);
      this.recorder = rec;

      if (clipMode.value === 'fixed') {
        const seconds = Math.max(3, Math.min(60, Number(clipSec.value) || 8));
        setTimeout(() => { try { if (this.recorder && this.recorder.state !== 'inactive') this.recorder.stop(); } catch(_){} }, seconds * 1000);
      } else {
        // Event mode normally stops when the target disappears, but this cap
        // prevents endless recording if the object stays on screen.
        if (this.eventCapTimer) clearTimeout(this.eventCapTimer);
        this.eventCapTimer = setTimeout(() => { try { if (this.recorder && this.recorder.state !== 'inactive') this.recorder.stop(); } catch(_){} }, this.eventMaxMs);
      }
    }

    stopRecordingIfAny() {
      if (this.eventCapTimer) { clearTimeout(this.eventCapTimer); this.eventCapTimer = null; }
      try {
        if (this.recorder && this.recorder.state !== 'inactive') {
          try { this.recorder.requestData(); } catch (_) {}
          this.recorder.stop();
        }
      } catch (_) {}
      this.recorder = null;
    }

    stopRecordingForEventEnd() {
      if (clipMode.value !== 'event') return;
      try { if (this.recorder && this.recorder.state !== 'inactive') this.recorder.stop(); } catch (_) {}
    }

    updatePredictionAndTrail(topDet) {
      const cx = (topDet.x1 + topDet.x2) / 2;
      const cy = (topDet.y1 + topDet.y2) / 2;

      const t = performance.now();
      this.trail.unshift({ x: cx, y: cy, t });
      if (this.trail.length > this.trailMax) this.trail.length = this.trailMax;
      this.trail = this.trail.filter(p => (t - p.t) <= this.trailKeepMs);

      if (!this.lastCenter || !this.lastCenterT) {
        this.lastCenter = { x: cx, y: cy };
        this.lastCenterT = t;
        this.vel = { vx: 0, vy: 0 };
        this.acc = { ax: 0, ay: 0 };
        this.predCurve = null;
        return;
      }

      const dt = (t - this.lastCenterT) / 1000.0;
      if (dt <= 0.0001) return;

      const dx = cx - this.lastCenter.x;
      const dy = cy - this.lastCenter.y;

      const instVx = dx / dt;
      const instVy = dy / dt;

      const vAlpha = 0.75;
      const prevVx = this.vel.vx;
      const prevVy = this.vel.vy;

      this.vel.vx = vAlpha * this.vel.vx + (1 - vAlpha) * instVx;
      this.vel.vy = vAlpha * this.vel.vy + (1 - vAlpha) * instVy;

      const instAx = (this.vel.vx - prevVx) / dt;
      const instAy = (this.vel.vy - prevVy) / dt;

      const aAlpha = 0.80;
      this.acc.ax = aAlpha * this.acc.ax + (1 - aAlpha) * instAx;
      this.acc.ay = aAlpha * this.acc.ay + (1 - aAlpha) * instAy;

      this.lastCenter = { x: cx, y: cy };
      this.lastCenterT = t;

      const horizon = 1.2;
      const steps = 6;
      const pts = [];
      for (let i = 1; i <= steps; i++) {
        const tt = (horizon * i) / steps;
        const px = cx + this.vel.vx * tt + 0.5 * this.acc.ax * tt * tt;
        const py = cy + this.vel.vy * tt + 0.5 * this.acc.ay * tt * tt;
        pts.push({ x: px, y: py });
      }
      this.predCurve = { start: { x: cx, y: cy }, pts };
    }

    async tick() {
      if (!this.isDetecting) return;
      if (this.video.readyState < 2) return;

      fitOverlayToVideo(this.video, this.overlay);
      const dataURL = await frameToDataUrl(this.video, 0.6);
      if (!dataURL) return;

      if (this.detectInFlight) return;
      this.detectInFlight = true;
      const abortCtl = new AbortController();
      this.abortCtl = abortCtl;

      const payload = {
        frame: dataURL,
        timestamp: new Date().toISOString(),
        frame_no: 0,
        conf: Number(confInput.value) || 0.4,
        max_dim: Number(maxDimInput.value) || 640
      };

      const t0 = performance.now();

      try {
        const resp = await fetch(DETECT_ENDPOINT, {
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body: JSON.stringify(payload),
          signal: abortCtl.signal
        });
        if (!resp.ok) return;

        const js = await resp.json();
        this.latestDetectMs = Math.round(performance.now() - t0);
        lastMs.textContent = String(this.latestDetectMs);

        let dets = js.detections || [];
        const hasDrone = (js.detected && dets.length > 0);
        const videoWidth = this.video.videoWidth || js.orig_size?.width || 0;
        const videoHeight = this.video.videoHeight || js.orig_size?.height || 0;
        const protectedZone = zoneToPixels(this.zone, videoWidth, videoHeight);
        const threatInfo = annotateThreats(dets, protectedZone, videoWidth, videoHeight, this.zoneEnteredAt, this.inEvent);
        dets = threatInfo.detections;
        this.zoneEnteredAt = threatInfo.zoneEnteredAt;

        if (hasDrone) {
          const top = dets.slice().sort((a,b) => (b.confidence||0) - (a.confidence||0))[0];
          this.updatePredictionAndTrail(top);
          this.lastSeenTs = Date.now();
        } else {
          this.clearMotion();
        }

        this.latestDetections = dets;
        this.latestExtras = this.buildOverlayExtras({
          protectedZone,
          intrusion: threatInfo.intrusion,
          threat: threatInfo.threat
        });

        // EVENT START
        if (hasDrone && !this.inEvent) {
          this.inEvent = true;
          this.eventId += 1;
          this.eventStartTs = Date.now();
          this.alertedThisEvent = false;
          this.alertedIntrusionThisEvent = false;
          this.eventThreat = threatInfo.threat;
          this.eventIntrusion = threatInfo.intrusion;

          // Alert immediately
          if (!this.alertedThisEvent) {
            const title = threatInfo.intrusion ? t('alertIntrusionTitle') : t('alertTitle');
            toast(title, `${t('alertBody')} ${this.name} - ${this.eventThreat}`);
            this.alertedThisEvent = true;
            this.alertedIntrusionThisEvent = threatInfo.intrusion;
          }

          // Create dashboard recent row immediately
          const start = this.eventStartTs;
          const timeText = `${fmtHHMMSS(start)} - ...`;
          const eventText = formatEventText(this.eventId, this.eventIntrusion, this.eventThreat, {
            zoneEnabled: this.isZoneEnabled(),
            confidence: threatInfo.maxConfidence,
            areaPct: threatInfo.maxAreaPct,
            dwellMs: threatInfo.dwellMs
          });
          this.recentRow = addRecentLogRow({
            timeText,
            event: eventText,
            source: this.name,
            clipText: autoClip.checked ? t('saving') : t('clipOff')
          });

          // create stored log skeleton on server (best-effort)
          await this.createStoredLogSkeleton(timeText, eventText);

          // start recording
          this.startRecordingForEvent();
        }

        if (hasDrone && this.inEvent) {
          const prevText = this.recentRow?.tdE?.textContent || '';
          this.eventIntrusion = this.eventIntrusion || threatInfo.intrusion;
          if (threatInfo.threat === 'High' || (threatInfo.threat === 'Medium' && this.eventThreat !== 'High')) {
            this.eventThreat = threatInfo.threat;
          }

          const nextText = formatEventText(this.eventId, this.eventIntrusion, this.eventThreat, {
            zoneEnabled: this.isZoneEnabled(),
            confidence: threatInfo.maxConfidence,
            areaPct: threatInfo.maxAreaPct,
            dwellMs: threatInfo.dwellMs
          });
          if (this.recentRow?.tdE && prevText !== nextText) {
            this.recentRow.tdE.textContent = nextText;
            this.finalizeActiveEventTime();
          }

          if (threatInfo.intrusion && !this.alertedIntrusionThisEvent) {
            toast(t('alertIntrusionTitle'), `${t('alertBody')} ${this.name} - ${this.eventThreat}`);
            this.alertedIntrusionThisEvent = true;
          }
        }

        // EVENT END
        if (!hasDrone && this.inEvent) {
          const goneFor = Date.now() - this.lastSeenTs;
          if (goneFor > this.goneDelayMs) {
            const start = this.eventStartTs || Date.now();
            const end = Date.now();
            const timeText = `${fmtHHMMSS(start)} - ${fmtHHMMSS(end)}`;

            // finalize dashboard row
            if (this.recentRow?.tdT) this.recentRow.tdT.textContent = timeText;

            // update stored log time even before clip finishes uploading
            if (this.storedLogId) {
              try {
                await fetch(LOGS_UPDATE_ENDPOINT, {
                  method:'POST',
                  headers:{'Content-Type':'application/json'},
                  body: JSON.stringify({
                    id: this.storedLogId,
                    time: timeText,
                    event: this.recentRow?.tdE?.textContent || formatEventText(this.eventId, this.eventIntrusion, this.eventThreat),
                    source: this.name,
                    clip: persistentClipText(this.pendingClipText || (autoClip.checked ? '-' : t('clipOff')))
                  })
                });
              } catch (_) {}
            }

            this.finalizeActiveEventTime();

            const clipDone = !!this.pendingClipText && this.pendingClipText !== t('saving');
            if (!autoClip.checked || clipDone) {
              triggerNodeSync();
            }

            // stop recording only for event mode
            this.stopRecordingForEventEnd();

            this.inEvent = false;
            this.eventStartTs = null;
          }
        }

      } catch (_) {
        // ignore abort/network
      } finally {
        if (this.abortCtl === abortCtl) this.abortCtl = null;
        this.detectInFlight = false;
      }
    }
  }

