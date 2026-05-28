(function () {
  const root = document.getElementById('experiment-live');
  const wsPath = (root && root.dataset.ws) || '/ws/experiment';
  const maxPoints = 120;
  const labels = [];
  const controlData = [];
  const agendaData = [];
  let chart = null;

  function initChart() {
    const canvas = document.getElementById('temp-chart');
    if (!canvas || typeof Chart === 'undefined') return;
    const agendaLabel =
      (root && root.dataset.labelAgenda) || 'Agenda (theory)';
    chart = new Chart(canvas, {
      type: 'line',
      data: {
        labels,
        datasets: [
          {
            label: 'Control temp',
            data: controlData,
            borderColor: '#3d8bfd',
            tension: 0.15,
            pointRadius: 0,
          },
          {
            label: agendaLabel,
            data: agendaData,
            borderColor: '#e55353',
            borderDash: [6, 4],
            tension: 0.15,
            pointRadius: 0,
          },
        ],
      },
      options: {
        animation: false,
        responsive: true,
        scales: {
          x: { display: false },
          y: { title: { display: true, text: 'K' } },
        },
      },
    });
  }

  function pushTemps(controlValue, agendaValue) {
    const t = new Date().toLocaleTimeString();
    labels.push(t);
    if (typeof controlValue === 'number' && !Number.isNaN(controlValue)) {
      controlData.push(controlValue);
    } else {
      controlData.push(null);
    }
    if (typeof agendaValue === 'number' && !Number.isNaN(agendaValue)) {
      agendaData.push(agendaValue);
    } else {
      agendaData.push(null);
    }
    if (labels.length > maxPoints) {
      labels.shift();
      controlData.shift();
      agendaData.shift();
    }
    if (chart) chart.update('none');
  }

  function fillTable(tbody, rows, cols) {
    tbody.innerHTML = '';
    (rows || []).forEach((row) => {
      const tr = document.createElement('tr');
      for (let i = 0; i < cols; i++) {
        const td = document.createElement('td');
        td.textContent = row[i] != null ? row[i] : '';
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    });
  }

  function pwmFromHeaterOutput(data) {
    const out = data.heater_output;
    if (out == null || Number.isNaN(Number(out))) return null;
    const pwmRange = 1000;
    const duty = Math.max(0, Math.min(pwmRange, Number(out)));
    const percent = Math.round((1000 * duty) / pwmRange) / 10;
    const ch = (label, pin) => ({
      label,
      pin,
      duty,
      percent,
      pwm_range: pwmRange,
    });
    return {
      available: true,
      pwm_range: pwmRange,
      control_enabled: Boolean(data.control_enabled),
      control_reason: (data.core_json && data.core_json.reason) || '',
      commanded_output: duty,
      channels: [ch('CH1', 18), ch('CH2', 19)],
    };
  }

  function resolvePwmStatus(data) {
    if (data && data.pwm_status) return data.pwm_status;
    if (data && data.heater_output != null) return pwmFromHeaterOutput(data);
    if (data && data.core_json && data.core_json.heater_output != null) {
      return pwmFromHeaterOutput({
        heater_output: data.core_json.heater_output,
        control_enabled: data.core_json.enabled,
        core_json: data.core_json,
      });
    }
    return null;
  }

  function formatPwmStatus(pwm) {
    if (!pwm) return 'Waiting for PWM data…';
    if (!pwm.available) {
      return pwm.message || 'PWM not available';
    }
    const lines = [];
    if (pwm.control_enabled) {
      lines.push(
        `Control: ON — PI output ${pwm.commanded_output} / ${pwm.pwm_range}` +
          (pwm.control_reason ? ` (${pwm.control_reason})` : '')
      );
    } else {
      lines.push('Control: OFF');
    }
    (pwm.channels || []).forEach((ch) => {
      lines.push(
        `${ch.label} GPIO ${ch.pin}: ${ch.duty} / ${ch.pwm_range} (${ch.percent}%)`
      );
    });
    return lines.join('\n');
  }

  function updatePwmDisplay(data) {
    const pwmEl = document.getElementById('pwm-status');
    if (!pwmEl) return;
    pwmEl.textContent = formatPwmStatus(resolvePwmStatus(data));
  }

  function applySnapshot(data) {
    if (!data) return;
    updatePwmDisplay(data);
    if (data.error) {
      const banner = document.getElementById('exp-banner');
      if (banner) banner.textContent = `Error: ${data.error}`;
      return;
    }
    const banner = document.getElementById('exp-banner');
    if (banner) banner.textContent = data.banner || '';
    const timingEl = document.getElementById('exp-timing');
    const modeEl = document.getElementById('exp-mode');
    const timing = data.timing || {};
    if (timingEl) {
      if (timing.active) {
        timingEl.textContent =
          `Elapsed ${timing.elapsed_text || '0:00'} / ${timing.total_text || '0:00'} — ` +
          `${timing.remaining_text || '0:00'} left (step ${timing.step_index || 1}/${timing.step_count || 1})`;
      } else {
        timingEl.textContent = '';
      }
    }
    if (modeEl) {
      const mode = data.experiment_mode || 'idle';
      const labels = { idle: 'Idle', program_running: 'Program running', stabilize: 'Stabilize (manual)' };
      let text = labels[mode] ? `Mode: ${labels[mode]}` : '';
      if (data.run_label) text += ` — experiment ${data.run_label}`;
      modeEl.textContent = text;
    }
    const ltm = document.getElementById('ltm-summary');
    if (ltm) ltm.textContent = data.ltm_summary || '';
    const ltmStream = document.getElementById('ltm-stream');
    if (ltmStream) ltmStream.textContent = (data.ltm_stream || []).join('\n') || '(no messages yet)';
    const e720s = document.getElementById('e720-summary');
    if (e720s) e720s.textContent = data.e720_summary || '';
    const e720Stream = document.getElementById('e720-stream');
    if (e720Stream) e720Stream.textContent = (data.e720_stream || []).join('\n') || '(no messages yet)';
    const core = document.getElementById('core-json');
    if (core) core.textContent = JSON.stringify(data.core_json || {}, null, 2);

    const measBody = document.querySelector('#meas-table tbody');
    if (measBody) fillTable(measBody, data.measurements, 5);

    const e720Body = document.querySelector('#e720-table tbody');
    if (e720Body && data.e720_row) fillTable(e720Body, [data.e720_row], 9);

    const controlTemp =
      typeof data.control_temp === 'number' && !Number.isNaN(data.control_temp)
        ? data.control_temp
        : null;
    const theoryTemp =
      typeof data.theoretical_temp === 'number' && !Number.isNaN(data.theoretical_temp)
        ? data.theoretical_temp
        : null;
    if (controlTemp !== null || theoryTemp !== null) {
      pushTemps(controlTemp, theoryTemp);
    }
  }

  function pollSnapshot() {
    fetch('/api/experiment/snapshot', { cache: 'no-store' })
      .then((r) => r.json())
      .then((data) => applySnapshot(data))
      .catch(() => {});
  }

  function connect() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(proto + '//' + location.host + wsPath);
    ws.onmessage = (ev) => {
      try {
        applySnapshot(JSON.parse(ev.data));
      } catch (e) {
        console.warn('bad ws payload', e);
      }
    };
    ws.onclose = () => {
      const banner = document.getElementById('exp-banner');
      if (banner) banner.textContent = 'WebSocket disconnected — retrying…';
      setTimeout(connect, 2000);
    };
  }

  document.addEventListener('DOMContentLoaded', () => {
    initChart();
    pollSnapshot();
    setInterval(pollSnapshot, 1000);
    connect();
  });
})();
