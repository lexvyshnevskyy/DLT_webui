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

  function applySnapshot(data) {
    if (!data || data.error) return;
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
    connect();
  });
})();
