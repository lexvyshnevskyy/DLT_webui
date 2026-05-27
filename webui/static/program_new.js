(function () {
  const form = document.getElementById('program-new-form');
  if (!form) return;

  const tMin = parseFloat(form.dataset.tMin || '40');
  const tMax = parseFloat(form.dataset.tMax || '1600');
  const descInput = document.getElementById('program-description');
  const summaryEl = document.getElementById('steps-validation-summary');
  const createBtn = document.getElementById('create-program-btn');
  const createHint = document.getElementById('create-hint');
  const badgeDesc = document.getElementById('badge-description');
  const badgeSteps = document.getElementById('badge-steps');
  const badgeE720 = document.getElementById('badge-e720');
  const sectionSteps = document.getElementById('section-steps');
  const sectionE720 = document.getElementById('section-e720');
  const sectionAddStep = document.getElementById('section-add-step');
  const stepsTable = document.getElementById('steps-table');

  let validateTimer = null;

  function setSectionLocked(el, locked) {
    if (!el) return;
    el.classList.toggle('section-locked', locked);
    el.querySelectorAll('input, select, button, textarea').forEach(function (control) {
      if (control.id === 'create-program-btn') return;
      if (control.closest('form') && control.closest('form') !== form) return;
      control.disabled = locked;
    });
  }

  function readRows() {
    const rows = [];
    if (!stepsTable) return rows;
    stepsTable.querySelectorAll('tbody tr[data-step-id]').forEach(function (tr) {
      const id = parseInt(tr.dataset.stepId, 10);
      const tStart = tr.querySelector('[data-field="t_start"]');
      const tStop = tr.querySelector('[data-field="t_stop"]');
      const minutes = tr.querySelector('[data-field="minutes"]');
      if (!tStart || !tStop || !minutes) return;
      rows.push([
        id,
        parseFloat(tStart.value),
        parseFloat(tStop.value),
        parseFloat(minutes.value),
      ]);
    });
    return rows;
  }

  function readE720() {
    const freqs = [];
    form.querySelectorAll('input[name="enabled_freqs"]:checked').forEach(function (cb) {
      freqs.push(cb.value);
    });
    return {
      sweep_mode: parseInt(form.querySelector('#sweep-mode')?.value || '0', 10),
      enabled_freqs: freqs,
      range_max: parseFloat(form.querySelector('#range-max')?.value || '0'),
    };
  }

  function applyIssueHighlights(issues) {
    if (!stepsTable) return;
    const byStep = {};
    (issues || []).forEach(function (issue) {
      if (!issue.step_id) return;
      byStep[issue.step_id] = byStep[issue.step_id] || {};
      if (issue.field) byStep[issue.step_id][issue.field] = true;
    });
    stepsTable.querySelectorAll('tbody tr[data-step-id]').forEach(function (tr) {
      const sid = parseInt(tr.dataset.stepId, 10);
      const flags = byStep[sid] || {};
      tr.classList.toggle('step-invalid', Object.keys(flags).length > 0);
      ['t_start', 't_stop', 'minutes'].forEach(function (field) {
        const input = tr.querySelector('[data-field="' + field + '"]');
        if (input) input.classList.toggle('input-invalid', !!flags[field]);
      });
    });
  }

  function renderSummary(data) {
    if (!summaryEl) return;
    const issues = (data && data.issues) || [];
    const stepIssues = issues.filter(function (i) { return i.step_id > 0 || i.code === 'no_steps'; });
    if (!stepIssues.length) {
      summaryEl.innerHTML = '';
      summaryEl.className = 'validation-summary';
      return;
    }
    summaryEl.className = 'validation-summary has-errors';
    const items = stepIssues.map(function (i) {
      return '<li class="' + (i.severity || 'error') + '">' + escapeHtml(i.message) + '</li>';
    });
    summaryEl.innerHTML = '<ul>' + items.join('') + '</ul>';
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  function updateBadges(data) {
    const descOk = !!(data && data.description_ok);
    const stepsOk = !!(data && data.steps_ok);
    const e720Ok = !!(data && data.e720_ok);

    if (badgeDesc) {
      badgeDesc.textContent = descOk ? 'OK' : 'Required';
      badgeDesc.className = 'section-badge ' + (descOk ? 'ok' : 'pending');
    }
    if (badgeSteps) {
      badgeSteps.textContent = stepsOk ? 'OK' : 'Incomplete';
      badgeSteps.className = 'section-badge ' + (stepsOk ? 'ok' : 'pending');
    }
    if (badgeE720) {
      badgeE720.textContent = e720Ok ? 'OK' : 'Incomplete';
      badgeE720.className = 'section-badge ' + (e720Ok ? 'ok' : 'pending');
    }

    const descFilled = descInput && descInput.value.trim().length > 0;
    setSectionLocked(sectionSteps, !descFilled);
    setSectionLocked(sectionAddStep, !descFilled);
    setSectionLocked(sectionE720, !descFilled || !stepsOk);
  }

  function updateCreateButton(data) {
    const can = !!(data && data.can_create);
    if (createBtn) {
      createBtn.disabled = !can;
      createBtn.title = can ? 'Save program to database' : 'Complete all sections above';
    }
    if (createHint) {
      createHint.textContent = can
        ? 'All checks passed — you can create the program.'
        : 'Complete description, valid temperature steps, and E7-20 settings.';
    }
  }

  function validateRemote() {
    const body = new FormData(form);
    return fetch('/program-new/validate', {
      method: 'POST',
      body: body,
      credentials: 'same-origin',
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        applyIssueHighlights(data.issues);
        renderSummary(data);
        updateBadges(data);
        updateCreateButton(data);
        return data;
      })
      .catch(function () {
        if (createBtn) createBtn.disabled = true;
      });
  }

  function scheduleValidate() {
    clearTimeout(validateTimer);
    validateTimer = setTimeout(validateRemote, 180);
  }

  form.addEventListener('input', scheduleValidate);
  form.addEventListener('change', scheduleValidate);
  if (descInput) {
    descInput.addEventListener('input', scheduleValidate);
  }

  form.addEventListener('submit', function (ev) {
    if (createBtn && createBtn.disabled) {
      ev.preventDefault();
      validateRemote();
    }
  });

  document.addEventListener('program-new-steps-changed', scheduleValidate);

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', scheduleValidate);
  } else {
    scheduleValidate();
  }
})();
