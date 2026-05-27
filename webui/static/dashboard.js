(function () {
  const root = document.getElementById('dashboard-live');
  const wsPath = (root && root.dataset.ws) || '/ws/dashboard';
  const pollUrl = (root && root.dataset.poll) || '/dashboard/snapshot';
  const pollUrlAlt = '/api/dashboard/snapshot';
  let pollTimer = null;
  let usingPoll = false;

  function fillTable(tbody, rows, cols) {
    if (!tbody) return;
    tbody.innerHTML = '';
    (rows || []).forEach(function (row) {
      const tr = document.createElement('tr');
      for (let i = 0; i < cols; i++) {
        const td = document.createElement('td');
        const val = row[i];
        if (i === 1 && tbody.id === 'dash-uart-tbody') {
          const code = document.createElement('code');
          code.textContent = val != null ? val : '';
          td.appendChild(code);
        } else {
          td.textContent = val != null ? val : '';
        }
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    });
  }

  function applySnapshot(data) {
    if (!data || data.error) return;
    const svc = data.services || {};
    const core = document.getElementById('dash-core');
    const db = document.getElementById('dash-db');
    const ready = document.getElementById('dash-ready');
    if (core) {
      core.textContent = svc.core_available ? 'OK' : 'DOWN';
      core.className = svc.core_available ? 'ok' : 'err';
    }
    if (db) {
      db.textContent = svc.database_available ? 'OK' : 'DOWN';
      db.className = svc.database_available ? 'ok' : 'err';
    }
    if (ready) ready.textContent = svc.system_ready ? 'yes' : 'no';
    const pwmRow = document.getElementById('dash-pwm-row');
    const pwm = document.getElementById('dash-pwm');
    if (pwmRow && pwm) {
      if (svc.pwm_note) {
        pwmRow.style.display = '';
        pwm.textContent = svc.pwm_note;
      } else {
        pwmRow.style.display = 'none';
      }
    }

    const host = data.host || {};
    const cpu = document.getElementById('dash-cpu');
    const load = document.getElementById('dash-load');
    const mem = document.getElementById('dash-mem');
    if (host.error) {
      if (cpu) cpu.textContent = host.error;
    } else {
      if (cpu) cpu.textContent = host.cpu_percent + '%';
      if (load) load.textContent = Array.isArray(host.load_avg) ? host.load_avg.join(', ') : String(host.load_avg);
      if (mem) {
        mem.textContent = host.memory_percent + '% (' + host.memory_used_gb + '/' + host.memory_total_gb + ' GB)';
      }
    }

    (data.units || []).forEach(function (row) {
      const unit = row[0];
      const tr = document.querySelector('#dash-services-tbody tr[data-unit="' + unit + '"]');
      if (!tr) return;
      const active = tr.querySelector('.svc-active');
      const sub = tr.querySelector('.svc-sub');
      if (active) active.textContent = row[1];
      if (sub) sub.textContent = row[2];
    });

    fillTable(document.getElementById('dash-disk-tbody'), data.disks, 4);
    fillTable(document.getElementById('dash-uart-tbody'), data.uart, 3);
    fillTable(document.getElementById('dash-network-tbody'), data.interfaces, 4);

    const log = document.getElementById('dash-log');
    if (log && data.log_text != null) log.textContent = data.log_text;
  }

  function fetchSnapshotFrom(url) {
    return fetch(url, { credentials: 'same-origin', cache: 'no-store' }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }

  function fetchSnapshot() {
    return fetchSnapshotFrom(pollUrl)
      .catch(function () {
        if (pollUrl === pollUrlAlt) throw new Error('HTTP 404');
        return fetchSnapshotFrom(pollUrlAlt);
      })
      .then(applySnapshot);
  }

  function startPolling() {
    if (pollTimer) return;
    usingPoll = true;
    console.info('dashboard: using HTTP polling', pollUrl);
    fetchSnapshot().catch(function (e) { console.warn('dashboard poll', e); });
    pollTimer = setInterval(function () {
      fetchSnapshot().catch(function (e) { console.warn('dashboard poll', e); });
    }, 1000);
  }

  function connectWebSocket() {
    if (usingPoll) return;
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = proto + '//' + location.host + wsPath;
    let ws;
    try {
      ws = new WebSocket(url);
    } catch (e) {
      console.warn('dashboard WebSocket failed, using poll', e);
      startPolling();
      return;
    }
    var opened = false;
    ws.onopen = function () {
      opened = true;
    };
    ws.onmessage = function (ev) {
      try {
        applySnapshot(JSON.parse(ev.data));
      } catch (e) {
        console.warn('dashboard ws parse', e);
      }
    };
    ws.onerror = function () {
      console.warn('dashboard WebSocket error:', url);
    };
    ws.onclose = function () {
      if (!opened && !usingPoll) {
        startPolling();
        return;
      }
      if (!usingPoll) {
        setTimeout(connectWebSocket, 2000);
      }
    };
    setTimeout(function () {
      if (!opened && ws.readyState !== WebSocket.OPEN && !usingPoll) {
        try { ws.close(); } catch (e) { /* ignore */ }
        startPolling();
      }
    }, 1500);
  }

  document.addEventListener('DOMContentLoaded', function () {
    connectWebSocket();
  });
})();
