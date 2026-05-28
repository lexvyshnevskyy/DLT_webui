(function () {
  const PAGE_SIZE = 100;

  function initTabs() {
    const buttons = document.querySelectorAll('.tab-btn');
    buttons.forEach((btn) => {
      btn.addEventListener('click', () => {
        const targetId = btn.getAttribute('data-tab');
        document.querySelectorAll('.tab-btn').forEach((b) => {
          b.classList.remove('active');
          b.setAttribute('aria-selected', 'false');
        });
        document.querySelectorAll('.tab-panel').forEach((p) => p.classList.remove('active'));
        btn.classList.add('active');
        btn.setAttribute('aria-selected', 'true');
        const panel = document.getElementById(targetId);
        if (panel) panel.classList.add('active');
      });
    });
  }

  function initClickableRows() {
    document.querySelectorAll('tr.clickable-row[data-href]').forEach((row) => {
      row.addEventListener('click', (ev) => {
        if (ev.target.closest('.no-row-nav')) return;
        window.location.href = row.getAttribute('data-href');
      });
    });
  }

  function formatCell(value) {
    if (value === null || value === undefined) return '';
    if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(4);
    return String(value);
  }

  function initMeasurementsLazyLoad() {
    const meta = document.getElementById('run-meas-meta');
    const tbody = document.querySelector('#run-meas-table tbody');
    const hint = document.getElementById('run-meas-load-more');
    if (!meta || !tbody) return;

    const api = meta.dataset.api;
    const programId = meta.dataset.programId;
    const runId = meta.dataset.runId;
    let offset = 0;
    let total = null;
    let loading = false;
    let done = false;

    function appendRows(rows) {
      rows.forEach((row) => {
        const tr = document.createElement('tr');
        ['elapsed_s', 'freq', 'measure_ch1', 'measure_ch2', 't_ch1', 't_ch2', 't_exp'].forEach((key) => {
          const td = document.createElement('td');
          td.textContent = formatCell(row[key]);
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
    }

    async function loadMore() {
      if (loading || done) return;
      loading = true;
      try {
        const url = `${api}?run_id=${encodeURIComponent(runId)}&program_id=${encodeURIComponent(programId)}&offset=${offset}&limit=${PAGE_SIZE}`;
        const resp = await fetch(url);
        const data = await resp.json();
        if (data.result !== 'Ok') {
          if (hint) hint.textContent = data.error || 'Load failed';
          done = true;
          return;
        }
        total = data.total != null ? data.total : total;
        const rows = data.row || [];
        appendRows(rows);
        offset += rows.length;
        if (hint) {
          hint.textContent =
            total != null
              ? `Loaded ${offset} / ${total} rows — scroll down for more`
              : `Loaded ${offset} rows`;
        }
        if (rows.length < PAGE_SIZE || (total != null && offset >= total)) {
          done = true;
          if (hint) hint.textContent = total != null ? `${total} rows loaded.` : `${offset} rows loaded.`;
        }
      } catch (err) {
        if (hint) hint.textContent = 'Failed to load measurements.';
        done = true;
      } finally {
        loading = false;
      }
    }

    const onScroll = () => {
      if (done || loading) return;
      const nearBottom = window.innerHeight + window.scrollY >= document.body.offsetHeight - 200;
      if (nearBottom) loadMore();
    };

    window.addEventListener('scroll', onScroll, { passive: true });
    loadMore();
  }

  document.addEventListener('DOMContentLoaded', () => {
    initTabs();
    initClickableRows();
    initMeasurementsLazyLoad();
  });
})();
