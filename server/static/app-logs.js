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

  function openEdit(log) {
    editingLog = log;
    editEvent.value = log.event || '';
    editTime.value = log.time || '';
    editSource.value = log.source || '';
    editClip.value = log.clip || '';
    editHint.textContent = `ID: ${log.id}`;
    editBackdrop.classList.add('show');
    editBackdrop.setAttribute('aria-hidden','false');
  }
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
      id: editingLog.id,
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
      toast(t('edit'), 'Saved');
      closeEdit();
      await loadStoredLogs();
    } catch (e) {
      toast('Error', e.error || e.message || 'Update failed');
    }
  });

  btnDeleteLog.addEventListener('click', async () => {
    if (!isAdmin || !editingLog) return;
    try {
      await apiFetch(LOGS_DELETE_ENDPOINT, {
        method:'POST',
        body: { id: editingLog.id }
      });
      toast(t('delete'), 'Deleted');
      closeEdit();
      await loadStoredLogs();
    } catch (e) {
      toast('Error', e.error || e.message || 'Delete failed');
    }
  });

  async function loadStoredLogs() {
    storedLogBody.innerHTML = '';
    try {
      const data = await apiFetch(LOGS_LIST_ENDPOINT, { method:'GET' });
      for (const log of (data || [])) {
        const tr = document.createElement('tr');

        const tdId = document.createElement('td'); tdId.textContent = log.id ?? '-';
        const tdTime = document.createElement('td'); tdTime.textContent = log.time ?? '-';
        const tdEvent = document.createElement('td'); tdEvent.textContent = log.event ?? '-';
        const tdSource = document.createElement('td'); tdSource.textContent = log.source ?? '-';

        const tdClip = document.createElement('td');
        tdClip.textContent = log.clip ?? '-';
        tdClip.style.color = (log.clip && String(log.clip).startsWith('Saved:')) ? 'var(--text)' : 'var(--muted)';

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
      td.textContent = e.error || e.message || 'Unable to load detector-node logs.';
      tr.appendChild(td);
      storedLogBody.appendChild(tr);
    }
  }

