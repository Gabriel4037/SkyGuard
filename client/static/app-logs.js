  // Stored log editing for the client node. Logs are local first, then synced
  // to the central server by the background sync process.
  // Edit modal
  const editBackdrop = document.getElementById('editBackdrop');
  const btnCloseEdit = document.getElementById('btnCloseEdit');
  const btnSaveEdit = document.getElementById('btnSaveEdit');
  const btnDeleteLog = document.getElementById('btnDeleteLog');
  const editEvent = document.getElementById('editEvent');
  const editTime = document.getElementById('editTime');
  const editSource = document.getElementById('editSource');
  const editClip = document.getElementById('editClip');
  const editHint = document.getElementById('editHint');

  let editingLog = null;
  const apiFetch = (window.authApi && window.authApi.apiFetch) || window.apiFetch;

  // Clean clip text before showing it in the edit modal/table.
  function cleanClipText(value) {
    const text = String(value || '').trim();
    if (!text || text === t('saving') || text.toLowerCase() === 'saving...') return '-';
    return text;
  }

  // Open the edit modal and copy one log row into form fields.
  function openEdit(log) {
    editingLog = log;
    editEvent.value = log.event || '';
    editTime.value = log.time || '';
    editSource.value = log.source || '';
    editClip.value = log.clip || '';
    editHint.textContent = log.central_log_id ? `Central ID: ${log.central_log_id} | Local ID: ${log.local_id}` : `Local ID: ${log.local_id || log.id}`;
    editBackdrop.classList.add('show');
    editBackdrop.setAttribute('aria-hidden','false');
  }

  // Close the edit modal and forget the selected log.
  function closeEdit() {
    editBackdrop.classList.remove('show');
    editBackdrop.setAttribute('aria-hidden','true');
    editingLog = null;
  }
  btnCloseEdit.addEventListener('click', closeEdit);
  editBackdrop.addEventListener('click', (e) => { if (e.target === editBackdrop) closeEdit(); });

  btnSaveEdit.addEventListener('click', async () => {
    if (!isAdmin || !editingLog) return;
    const payload = {
      id: editingLog.local_id || editingLog.id,
      event: editEvent.value.trim(),
      time: editTime.value.trim(),
      source: editSource.value.trim(),
      clip: editClip.value.trim()
    };
    try {
      await apiFetch(LOGS_UPDATE_ENDPOINT, {
        method:'POST',
        body: payload
      });
      toast(t('edit'), t('saved'));
      closeEdit();
      await loadStoredLogs();
    } catch (e) {
      toast(t('error'), e.error || e.message || t('updateFailed'));
    }
  });

  btnDeleteLog.addEventListener('click', async () => {
    if (!isAdmin || !editingLog) return;
    try {
      await apiFetch(LOGS_DELETE_ENDPOINT, {
        method:'POST',
        body: { id: editingLog.local_id || editingLog.id }
      });
      toast(t('delete'), t('deleted'));
      closeEdit();
      await loadStoredLogs();
    } catch (e) {
      toast(t('error'), e.error || e.message || t('deleteFailed'));
    }
  });

  async function loadStoredLogs() {
    storedLogBody.innerHTML = '';
    try {
      const data = await apiFetch(LOGS_LIST_ENDPOINT, { method:'GET' });
      for (const log of (data || [])) {
        const tr = document.createElement('tr');

        const tdId = document.createElement('td'); tdId.textContent = log.id ?? '-';
        if (log.local_id && log.central_log_id) tdId.title = `Local ID: ${log.local_id}`;
        const tdTime = document.createElement('td'); tdTime.textContent = log.time ?? '-';
        const tdEvent = document.createElement('td'); tdEvent.textContent = log.event ?? '-';
        const tdSource = document.createElement('td'); tdSource.textContent = log.source ?? '-';

        const tdClip = document.createElement('td');
        const clipText = cleanClipText(log.clip);
        tdClip.textContent = clipText;
        tdClip.style.color = (clipText && String(clipText).startsWith(t('savedPrefix'))) ? 'var(--text)' : 'var(--muted)';

        tr.appendChild(tdId);
        tr.appendChild(tdTime);
        tr.appendChild(tdEvent);
        tr.appendChild(tdSource);
        tr.appendChild(tdClip);
        storedLogBody.appendChild(tr);
      }
    } catch (e) {
      // Show hint row
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.colSpan = 5;
      td.style.color = 'var(--muted)';
      td.textContent = e.error || e.message || t('loadNodeLogsFailed');
      tr.appendChild(td);
      storedLogBody.appendChild(tr);
    }
  }

