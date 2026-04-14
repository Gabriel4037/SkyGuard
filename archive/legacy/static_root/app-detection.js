  // =======================
  // Video overlay + annotation recording
  // =======================
  function fitOverlayToVideo(videoEl, overlayEl) {
    const rect = videoEl.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    overlayEl.width = Math.max(1, Math.floor(rect.width * dpr));
    overlayEl.height = Math.max(1, Math.floor(rect.height * dpr));
    overlayEl.style.width = rect.width + 'px';
    overlayEl.style.height = rect.height + 'px';
    return { rect, dpr };
  }

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
    const resp = await fetch(CLIP_SAVE_ENDPOINT, { method: 'POST', body: fd });
    if (!resp.ok) throw new Error('upload failed');
    return await resp.json(); // {ok:true, filename:"..."}
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

        ctx.strokeStyle = 'rgba(34,197,94,0.95)';
        ctx.fillStyle = 'rgba(34,197,94,0.15)';
        ctx.fillRect(x, y, w, h);
        ctx.strokeRect(x, y, w, h);

        const tag = `${label} ${(conf*100).toFixed(0)}%`;
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

      this.isDetecting = false;
      this.inEvent = false;
      this.eventId = 0;
      this.eventStartTs = null;
      this.lastSeenTs = 0;
      this.goneDelayMs = 1500;

      this.recorder = null;
      this.recChunks = [];
      this.pendingClipText = null;

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
      this.handleResize = () => fitOverlayToVideo(this.video, this.overlay);
    }

    start() {
      this.stop();
      const fps = Math.max(1, Math.min(15, Number(fpsInput.value) || 6));
      const interval = Math.floor(1000 / fps);

      this.isDetecting = true;
      setStatus(`${this.name}: detecting`, true);

      this.timerId = setInterval(() => this.tick(), interval);
      window.removeEventListener('resize', this.handleResize);
      window.addEventListener('resize', this.handleResize, { passive: true });
    }

    stop() {
      this.isDetecting = false;
      if (this.timerId) { clearInterval(this.timerId); this.timerId = null; }
      if (this.abortCtl) { this.abortCtl.abort(); this.abortCtl = null; }

      this.stopRecordingIfAny();

      this.inEvent = false;
      this.eventStartTs = null;
      this.pendingClipText = null;
      this.recentRow = null;
      this.storedLogId = null;
      this.alertedThisEvent = false;

      this.clearMotion();
      window.removeEventListener('resize', this.handleResize);

      const ctx = this.overlay.getContext('2d');
      ctx.clearRect(0, 0, this.overlay.width, this.overlay.height);
      setStatus('idle', true);
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
      // Best-effort create; backend must exist
      try {
        const r = await fetch(LOGS_CREATE_ENDPOINT, {
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body: JSON.stringify({
            time: timeText,
            event: eventText,
            source: this.name,
            clip: 'saving...'
          })
        });
        if (!r.ok) throw new Error('bad');
        const js = await r.json();
        this.storedLogId = js.id ?? null;
      } catch (_) {
        this.storedLogId = null;
      }
    }

    async updateStoredLogClip(clipFilename, timeText, eventText) {
      if (!this.storedLogId) return;
      try {
        await fetch(LOGS_UPDATE_ENDPOINT, {
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body: JSON.stringify({
            id: this.storedLogId,
            time: timeText,
            event: eventText,
            source: this.name,
            clip: `Saved: ${clipFilename}`
          })
        });
      } catch (_) {}
    }

    startRecordingForEvent() {
      if (!autoClip.checked) {
        this.pendingClipText = 'off';
        return;
      }

      const stream = this.getRecordStream();
      if (!stream || typeof MediaRecorder === 'undefined') {
        this.pendingClipText = 'Clip unsupported';
        return;
      }

      const mimeType = pickMimeType();
      let rec;
      try {
        rec = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
      } catch (_) {
        this.pendingClipText = 'Clip recorder error';
        return;
      }

      this.pendingClipText = 'saving...';
      this.recChunks = [];

      rec.ondataavailable = (ev) => { if (ev.data && ev.data.size > 0) this.recChunks.push(ev.data); };

      rec.onstop = async () => {
        const blob = new Blob(this.recChunks, { type: rec.mimeType || 'video/webm' });
        if (!blob.size) {
          this.pendingClipText = 'Save failed';
          if (this.recentRow?.tdL) this.recentRow.tdL.textContent = this.pendingClipText;
          return;
        }
        try {
          const res = await uploadClipToServer(blob, this.name, this.eventId);
          this.pendingClipText = `Saved: ${res.filename}`;

          // update dashboard recent row
          if (this.recentRow?.tdL) {
            this.recentRow.tdL.textContent = this.pendingClipText;
            this.recentRow.tdL.style.color = 'var(--text)';
          }

          // update stored log row on server
          const timeText = this.recentRow?.tdT?.textContent || '';
          const eventText = this.recentRow?.tdE?.textContent || '';
          await this.updateStoredLogClip(res.filename, timeText, eventText);

        } catch (_) {
          this.pendingClipText = 'Save failed';
          if (this.recentRow?.tdL) this.recentRow.tdL.textContent = this.pendingClipText;
        }
      };

      rec.start(250);
      this.recorder = rec;

      if (clipMode.value === 'fixed') {
        const seconds = Math.max(3, Math.min(60, Number(clipSec.value) || 8));
        setTimeout(() => { try { if (this.recorder && this.recorder.state !== 'inactive') this.recorder.stop(); } catch(_){} }, seconds * 1000);
      } else {
        if (this.eventCapTimer) clearTimeout(this.eventCapTimer);
        this.eventCapTimer = setTimeout(() => { try { if (this.recorder && this.recorder.state !== 'inactive') this.recorder.stop(); } catch(_){} }, this.eventMaxMs);
      }
    }

    stopRecordingIfAny() {
      if (this.eventCapTimer) { clearTimeout(this.eventCapTimer); this.eventCapTimer = null; }
      try { if (this.recorder && this.recorder.state !== 'inactive') this.recorder.stop(); } catch (_) {}
      this.recorder = null;
      this.recChunks = [];
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

      const dataURL = await frameToDataUrl(this.video, 0.6);
      if (!dataURL) return;

      if (this.abortCtl) this.abortCtl.abort();
      this.abortCtl = new AbortController();

      const payload = {
        frame: dataURL,
        timestamp: new Date().toISOString(),
        frame_no: 0,
        persist: false,
        conf: Number(confInput.value) || 0.4,
        max_dim: Number(maxDimInput.value) || 640
      };

      const t0 = performance.now();

      try {
        const resp = await fetch(DETECT_ENDPOINT, {
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body: JSON.stringify(payload),
          signal: this.abortCtl.signal
        });
        if (!resp.ok) return;

        const js = await resp.json();
        lastMs.textContent = String(Math.round(performance.now() - t0));

        const dets = js.detections || [];
        const hasDrone = (js.detected && dets.length > 0);

        if (hasDrone) {
          const top = dets.slice().sort((a,b) => (b.confidence||0) - (a.confidence||0))[0];
          this.updatePredictionAndTrail(top);
          this.lastSeenTs = Date.now();
        } else {
          this.clearMotion();
        }

        const extras = { predCurve: this.predCurve, trail: this.trail.slice().reverse() };
        drawOverlay(this.video, this.overlay, this.recordEl, dets, extras);

        // EVENT START
        if (hasDrone && !this.inEvent) {
          this.inEvent = true;
          this.eventId += 1;
          this.eventStartTs = Date.now();
          this.alertedThisEvent = false;

          // Alert immediately
          if (!this.alertedThisEvent) {
            toast(t('alertTitle'), `${t('alertBody')} ${this.name}`);
            this.alertedThisEvent = true;
          }

          // Create dashboard recent row immediately
          const start = this.eventStartTs;
          const timeText = `${fmtHHMMSS(start)} – ...`;
          const eventText = `DRONE #${this.eventId}`;
          this.recentRow = addRecentLogRow({
            timeText,
            event: eventText,
            source: this.name,
            clipText: autoClip.checked ? 'saving...' : 'off'
          });

          // create stored log skeleton on server (best-effort)
          await this.createStoredLogSkeleton(timeText, eventText);

          // start recording
          this.startRecordingForEvent();
        }

        // EVENT END
        if (!hasDrone && this.inEvent) {
          const goneFor = Date.now() - this.lastSeenTs;
          if (goneFor > this.goneDelayMs) {
            const start = this.eventStartTs || Date.now();
            const end = Date.now();
            const timeText = `${fmtHHMMSS(start)} – ${fmtHHMMSS(end)}`;

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
                    event: this.recentRow?.tdE?.textContent || `DRONE #${this.eventId}`,
                    source: this.name,
                    clip: this.pendingClipText || (autoClip.checked ? 'saving...' : 'off')
                  })
                });
              } catch (_) {}
            }

            // stop recording only for event mode
            this.stopRecordingForEventEnd();

            this.inEvent = false;
            this.eventStartTs = null;
          }
        }

      } catch (_) {
        // ignore abort/network
      }
    }
  }

