(function () {
  const form = document.getElementById('program-new-form');
  if (!form) return;

  const descInput = document.getElementById('program-description');
  const summaryEl = document.getElementById('steps-validation-summary');
  const createBtn = document.getElementById('create-program-btn');
  const createHint = document.getElementById('create-hint');
  const badgeDesc = document.getElementById('badge-description');
  const badgeSteps = document.getElementById('badge-steps');
  const badgeE720 = document.getElementById('badge-e720');
  const sectionSteps = document.getElementById('section-steps');
  const sectionE720 = document.getElementById('section-e720');
  const stepsListBlock = document.getElementById('steps-list-block');
  const stepsTable = document.getElementById('steps-table');
  const addStepBtn = document.getElementById('add-step-btn');
  const addTStart = document.getElementById('add-t-start');
  const addTStop = document.getElementById('add-t-stop');
  const addMinutes = document.getElementById('add-minutes');
  const addStepHint = document.getElementById('add-step-hint');
  const msg = {
    addLocked: form.dataset.msgAddLocked || '',
    addInvalid: form.dataset.msgAddInvalid || '',
    addFail: form.dataset.msgAddFail || '',
    createHint: form.dataset.msgCreateHint || '',
    createReady: form.dataset.msgCreateReady || '',
    createTitleBlocked: form.dataset.msgCreateTitleBlocked || '',
    createTitleReady: form.dataset.msgCreateTitleReady || '',
    required: form.dataset.labelRequired || 'Required',
    incomplete: form.dataset.labelIncomplete || 'Incomplete',
    ok: form.dataset.labelOk || 'OK',
  };
  const addStepBtnDefault = addStepBtn ? addStepBtn.textContent : '';

  let validateTimer = null;

  function descriptionFilled() {
    return !!(descInput && descInput.value.trim().length > 0);
  }

  function setControlsDisabled(root, disabled) {
    if (!root) return;
    root.querySelectorAll('input, select, button, textarea').forEach(function (control) {
      if (control.id === 'create-program-btn') return;
      control.disabled = disabled;
    });
  }

  function syncSectionAccess() {
    const descOk = descriptionFilled();

    if (sectionSteps) {
      sectionSteps.classList.toggle('section-locked', !descOk);
    }
    setControlsDisabled(stepsListBlock, !descOk);

    if (addTStart) addTStart.disabled = !descOk;
    if (addTStop) addTStop.disabled = !descOk;
    if (addMinutes) addMinutes.disabled = !descOk;
    if (addStepBtn) addStepBtn.disabled = !descOk;
    if (addStepHint) {
      if (descOk) {
        addStepHint.style.display = 'none';
      } else {
        addStepHint.textContent = msg.addLocked;
        addStepHint.style.display = 'block';
      }
    }

    if (sectionE720 && !descOk) {
      sectionE720.classList.add('section-locked');
      setControlsDisabled(sectionE720, true);
    }
  }

  function setE720Locked(locked) {
    if (!sectionE720) return;
    sectionE720.classList.toggle('section-locked', locked);
    setControlsDisabled(sectionE720, locked);
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
      badgeDesc.textContent = descOk ? msg.ok : msg.required;
      badgeDesc.className = 'section-badge ' + (descOk ? 'ok' : 'pending');
    }
    if (badgeSteps) {
      badgeSteps.textContent = stepsOk ? msg.ok : msg.incomplete;
      badgeSteps.className = 'section-badge ' + (stepsOk ? 'ok' : 'pending');
    }
    if (badgeE720) {
      badgeE720.textContent = e720Ok ? msg.ok : msg.incomplete;
      badgeE720.className = 'section-badge ' + (e720Ok ? 'ok' : 'pending');
    }

    syncSectionAccess();
    if (descriptionFilled()) {
      setE720Locked(!stepsOk);
    }
  }

  function updateCreateButton(data) {
    const can = !!(data && data.can_create);
    if (createBtn) {
      createBtn.disabled = !can;
      createBtn.title = can ? msg.createTitleReady : msg.createTitleBlocked;
    }
    if (createHint) {
      createHint.textContent = can ? msg.createReady : msg.createHint;
    }
  }

  function validateRemote() {
    syncSectionAccess();
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
        syncSectionAccess();
      });
  }

  function scheduleValidate() {
    clearTimeout(validateTimer);
    validateTimer = setTimeout(validateRemote, 180);
  }

  function submitAddStep() {
    if (!descriptionFilled()) {
      if (addStepHint) addStepHint.style.display = 'block';
      return;
    }
    const tStart = parseFloat(addTStart && addTStart.value);
    const tStop = parseFloat(addTStop && addTStop.value);
    const minutes = parseFloat(addMinutes && addMinutes.value);
    if ([tStart, tStop, minutes].some(function (v) { return Number.isNaN(v); })) {
      if (addStepHint) {
        addStepHint.textContent = msg.addInvalid;
        addStepHint.style.display = 'block';
      }
      return;
    }
    if (addStepBtn) {
      addStepBtn.disabled = true;
      addStepBtn.textContent = 'Adding…';
    }
    const body = new URLSearchParams();
    body.set('t_start', String(tStart));
    body.set('t_stop', String(tStop));
    body.set('minutes', String(minutes));
    if (descInput) {
      body.set('description', descInput.value.trim());
    }
    fetch('/program-new/steps/add', {
      method: 'POST',
      body: body,
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    })
      .then(function (r) {
        if (r.redirected) {
          window.location.href = r.url;
          return;
        }
        window.location.href = '/program-new';
      })
      .catch(function () {
        if (addStepBtn) {
          addStepBtn.disabled = false;
          addStepBtn.textContent = addStepBtnDefault;
        }
        if (addStepHint) {
          addStepHint.textContent = msg.addFail;
          addStepHint.style.display = 'block';
        }
      });
  }

  form.addEventListener('input', scheduleValidate);
  form.addEventListener('change', scheduleValidate);
  if (descInput) {
    descInput.addEventListener('input', function () {
      syncSectionAccess();
      scheduleValidate();
    });
  }

  form.addEventListener('submit', function (ev) {
    const submitter = ev.submitter;
    if (submitter && submitter.getAttribute('formaction')) {
      return;
    }
    if (createBtn && createBtn.disabled) {
      ev.preventDefault();
      validateRemote();
    }
  });

  if (addStepBtn) {
    addStepBtn.addEventListener('click', submitAddStep);
  }

  document.addEventListener('program-new-steps-changed', scheduleValidate);

  syncSectionAccess();
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', scheduleValidate);
  } else {
    scheduleValidate();
  }
})();
