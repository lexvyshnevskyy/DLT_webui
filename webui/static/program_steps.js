(function () {
  function rowInputs(tr) {
    return {
      stepId: tr.dataset.stepId,
      tStart: tr.querySelector('[data-field="t_start"]'),
      tStop: tr.querySelector('[data-field="t_stop"]'),
      minutes: tr.querySelector('[data-field="minutes"]'),
    };
  }

  function rowValues(parts) {
    return {
      t_start: parseFloat(parts.tStart.value),
      t_stop: parseFloat(parts.tStop.value),
      minutes: parseFloat(parts.minutes.value),
    };
  }

  function setStatus(tr, text, ok) {
    let el = tr.querySelector('.step-save-status');
    if (!el) {
      el = document.createElement('span');
      el.className = 'step-save-status';
      tr.querySelector('td:last-child')?.appendChild(el);
    }
    el.textContent = text;
    el.className = 'step-save-status ' + (ok ? 'ok' : 'err');
  }

  function bindAutosave(table, saveUrl, programId) {
    if (!table) return;
    table.querySelectorAll('tbody tr[data-step-id]').forEach(function (tr) {
      const parts = rowInputs(tr);
      if (!parts.tStart) return;
      let saveTimer = null;
      function scheduleSave() {
        clearTimeout(saveTimer);
        saveTimer = setTimeout(function () {
          if (tr.contains(document.activeElement)) return;
          if (
            parts.tStart.value === parts.tStart.dataset.original &&
            parts.tStop.value === parts.tStop.dataset.original &&
            parts.minutes.value === parts.minutes.dataset.original
          ) {
            return;
          }
          const vals = rowValues(parts);
          if ([vals.t_start, vals.t_stop, vals.minutes].some(function (v) { return Number.isNaN(v); })) {
            setStatus(tr, 'invalid', false);
            return;
          }
          setStatus(tr, 'saving…', true);
          const body = new URLSearchParams();
          if (programId) body.set('id', String(programId));
          body.set('step_id', parts.stepId);
          body.set('t_start', String(vals.t_start));
          body.set('t_stop', String(vals.t_stop));
          body.set('minutes', String(vals.minutes));
          fetch(saveUrl, { method: 'POST', body: body, credentials: 'same-origin' })
            .then(function (r) { return r.json(); })
            .then(function (data) {
              if (data.ok) {
                parts.tStart.dataset.original = parts.tStart.value;
                parts.tStop.dataset.original = parts.tStop.value;
                parts.minutes.dataset.original = parts.minutes.value;
                setStatus(tr, 'saved', true);
              } else {
                setStatus(tr, data.message || 'failed', false);
              }
            })
            .catch(function () {
              setStatus(tr, 'network error', false);
            });
        }, 200);
      }
      [parts.tStart, parts.tStop, parts.minutes].forEach(function (input) {
        input.dataset.original = input.value;
        input.addEventListener('blur', scheduleSave);
      });
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    const editTable = document.querySelector('.steps-edit-table[data-mode="edit"]');
    const newTable = document.querySelector('.steps-edit-table[data-mode="new"]');
    if (editTable) {
      bindAutosave(editTable, '/program-edit/steps/save-one', editTable.dataset.programId);
    }
    if (newTable) {
      bindAutosave(newTable, '/program-new/steps/save-one', null);
    }
  });
})();
