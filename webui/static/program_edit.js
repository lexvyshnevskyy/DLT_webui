(function () {
  const form = document.getElementById('program-edit-form');
  if (!form) return;

  const programId = form.dataset.programId;
  const addStepBtn = document.getElementById('add-step-btn');
  const addTStart = document.getElementById('add-t-start');
  const addTStop = document.getElementById('add-t-stop');
  const addMinutes = document.getElementById('add-minutes');
  const addStepHint = document.getElementById('add-step-hint');
  const msg = {
    addInvalid: form.dataset.msgAddInvalid || '',
    addFail: form.dataset.msgAddFail || '',
  };
  const addStepBtnDefault = addStepBtn ? addStepBtn.textContent : '';

  function submitAddStep() {
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
    }
    const body = new URLSearchParams();
    body.set('id', String(programId));
    body.set('t_start', String(tStart));
    body.set('t_stop', String(tStop));
    body.set('minutes', String(minutes));
    fetch('/program-edit/steps/add', {
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
        window.location.href = '/program-edit?id=' + encodeURIComponent(programId);
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

  if (addStepBtn) {
    addStepBtn.addEventListener('click', submitAddStep);
  }
})();
