(function () {
  const wsPath = window.DELATOMETRY_WS || '/ws/experiment';
  const maxPoints = 120;
  const labels = [];
  const controlData = [];
  let chart = null;

  function initChart() {
    const canvas = document.getElementById('temp-chart');
    if (!canvas || typeof Chart === 'undefined') return;
    chart = new Chart(canvas, {
      type: 'line',
      data: {
        labels,
        datasets: [{
          label: 'Control temp',
          data: controlData,
          borderColor: '#3d8bfd',
          tension: 0.15,
          pointRadius: 0,
        }],
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

  function pushTemp(value) {
    const t = new Date().toLocaleTimeString();
    labels.push(t);
    controlData.push(value);
    if (labels.length > maxPoints) {
      labels.shift();
      controlData.shift();
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

    if (typeof data.control_temp === 'number' && !Number.isNaN(data.control_temp)) {
      pushTemp(data.control_temp);
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
