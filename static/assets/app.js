(() => {
  'use strict';

  const FACETS_URL = '/api/facets';
  const VIOLATIONS_URL = '/api/violations';

  const $ = (id) => document.getElementById(id);

  const elements = {
    statusPanel: $('appStatus'),
    statusText: $('statusText'),

    q: $('q'),
    status: $('status'),
    category: $('category'),
    city: $('city'),
    fromDate: $('fromDate'),
    toDate: $('toDate'),
    sort: $('sort'),
    pageSize: $('pageSize'),
    reset: $('reset'),

    statTotal: $('statTotal'),
    statEstablishments: $('statEstablishments'),
    statAmount: $('statAmount'),
    topCategories: $('topCategories'),

    resultsMeta: $('resultsMeta'),
    resultsBody: $('resultsBody'),
    prevPage: $('prevPage'),
    nextPage: $('nextPage'),
    pageInfo: $('pageInfo'),
  };

  function setStatus(kind, text) {
    if (!elements.statusPanel || !elements.statusText) return;

    elements.statusText.textContent = text;
    elements.statusPanel.classList.remove('w3-pale-blue', 'w3-border-blue', 'w3-pale-green', 'w3-border-green', 'w3-pale-red', 'w3-border-red', 'w3-pale-yellow', 'w3-border-yellow');
    if (kind === 'ok') {
      elements.statusPanel.classList.add('w3-pale-green', 'w3-border-green');
    } else if (kind === 'warn') {
      elements.statusPanel.classList.add('w3-pale-yellow', 'w3-border-yellow');
    } else if (kind === 'err') {
      elements.statusPanel.classList.add('w3-pale-red', 'w3-border-red');
    } else {
      elements.statusPanel.classList.add('w3-pale-blue', 'w3-border-blue');
    }
  }

  function normalize(s) {
    return String(s ?? '')
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .trim()
      .toLowerCase();
  }

  function parseMoney(value) {
    const s = String(value ?? '').trim();
    if (!s) return 0;
    // Les exemples vus dans les données ouvertes incluent souvent des espaces, $ ou des virgules.
    const cleaned = s.replace(/[^0-9,.-]/g, '').replace(',', '.');
    const n = Number(cleaned);
    return Number.isFinite(n) ? n : 0;
  }

  function parseDateLoose(value) {
    const raw = String(value ?? '').trim();
    if (!raw) return null;

    const d1 = new Date(raw);
    if (!Number.isNaN(d1.getTime())) return d1;

    // aaaa-mm-jj
    const m1 = raw.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (m1) {
      const y = Number(m1[1]);
      const m = Number(m1[2]) - 1;
      const d = Number(m1[3]);
      const d2 = new Date(Date.UTC(y, m, d));
      if (!Number.isNaN(d2.getTime())) return d2;
    }

    // jj/mm/aaaa
    const m2 = raw.match(/^(\d{2})\/(\d{2})\/(\d{4})/);
    if (m2) {
      const d = Number(m2[1]);
      const m = Number(m2[2]) - 1;
      const y = Number(m2[3]);
      const d2 = new Date(Date.UTC(y, m, d));
      if (!Number.isNaN(d2.getTime())) return d2;
    }

    return null;
  }

  function formatDate(d) {
    if (!d) return '';
    return String(d);
  }

  function formatMoney(n) {
    if (!Number.isFinite(n)) return '';
    return n.toLocaleString('fr-CA', { style: 'currency', currency: 'CAD', maximumFractionDigits: 2 });
  }

  function uniqSorted(values) {
    const arr = Array.from(new Set(values.filter(Boolean)));
    arr.sort((a, b) => a.localeCompare(b, 'fr', { sensitivity: 'base' }));
    return arr;
  }

  function optionize(select, values) {
    const keepFirst = select.querySelector('option[value=""]');
    select.textContent = '';
    if (keepFirst) select.appendChild(keepFirst);
    for (const v of values) {
      const opt = document.createElement('option');
      opt.value = v;
      opt.textContent = v;
      select.appendChild(opt);
    }
  }

  function topNCounts(items, n) {
    const m = new Map();
    for (const it of items) {
      const k = String(it ?? '').trim();
      if (!k) continue;
      m.set(k, (m.get(k) ?? 0) + 1);
    }
    return Array.from(m.entries())
      .sort((a, b) => b[1] - a[1])
      .slice(0, n);
  }

  function renderMiniBars(container, pairs) {
    container.textContent = '';
    if (!pairs.length) {
      const p = document.createElement('p');
      p.className = 'w3-small w3-opacity';
      p.textContent = 'Aucun resultat pour afficher des statistiques.';
      container.appendChild(p);
      return;
    }

    const max = Math.max(...pairs.map(([, c]) => c));
    for (const [label, count] of pairs) {
      const row = document.createElement('div');
      row.className = 'mini-bar';

      const left = document.createElement('div');
      left.className = 'mini-bar-label';
      left.title = `${label} (${count})`;
      left.textContent = `${label} (${count})`;

      const track = document.createElement('div');
      track.className = 'mini-bar-track';

      const fill = document.createElement('div');
      fill.className = 'mini-bar-fill';
      fill.style.width = `${Math.max(6, Math.round((count / max) * 100))}%`;

      track.appendChild(fill);
      row.appendChild(left);
      row.appendChild(track);
      container.appendChild(row);
    }
  }

  const state = {
    view: [],
    page: 1,
    pageSize: 25,
  };

  function readFilters() {
    return {
      q: normalize(elements.q.value),
      status: elements.status.value,
      category: elements.category.value,
      city: elements.city.value,
      from: elements.fromDate.value ? parseDateLoose(elements.fromDate.value) : null,
      to: elements.toDate.value ? parseDateLoose(elements.toDate.value) : null,
      sort: elements.sort.value,
      pageSize: Number(elements.pageSize.value) || 25,
    };
  }

  function buildQuery() {
    const f = readFilters();
    state.pageSize = f.pageSize;
    const p = new URLSearchParams();
    if (f.q) p.set('q', f.q);
    if (f.status) p.set('status', f.status);
    if (f.category) p.set('category', f.category);
    if (f.city) p.set('city', f.city);
    if (f.from) p.set('from', String(elements.fromDate.value));
    if (f.to) p.set('to', String(elements.toDate.value));
    if (f.sort) p.set('sort', f.sort);
    p.set('page', String(state.page));
    p.set('page_size', String(state.pageSize));
    return p;
  }

  function clampPage() {
    if (state.page < 1) state.page = 1;
    return state.page;
  }

  function renderFromResponse(payload) {
    const items = payload.items || [];
    const total = Number(payload.total || 0);
    const page = Number(payload.page || state.page);
    const pageSize = Number(payload.page_size || state.pageSize);
    const pages = Math.max(1, Math.ceil(total / pageSize));

    elements.resultsBody.textContent = '';

    for (const r of items) {
      const tr = document.createElement('tr');

      const tdDate = document.createElement('td');
      tdDate.textContent = r.date ? formatDate(r.date) : '';

      const tdEst = document.createElement('td');
      tdEst.textContent = r.establishment || '';

      const tdCat = document.createElement('td');
      tdCat.textContent = r.category || '';

      const tdDesc = document.createElement('td');
      tdDesc.textContent = r.description || '';

      const tdAmt = document.createElement('td');
      tdAmt.className = 'w3-right-align';
      tdAmt.textContent = formatMoney(r.amount || 0);

      const tdStatus = document.createElement('td');
      tdStatus.textContent = r.status || '';

      tr.appendChild(tdDate);
      tr.appendChild(tdEst);
      tr.appendChild(tdCat);
      tr.appendChild(tdDesc);
      tr.appendChild(tdAmt);
      tr.appendChild(tdStatus);

      elements.resultsBody.appendChild(tr);
    }

    state.page = page;
    state.pageSize = pageSize;
    elements.pageInfo.textContent = `${page} / ${pages}`;
    elements.prevPage.disabled = page <= 1;
    elements.nextPage.disabled = page >= pages;

    const shown = items.length;
    elements.resultsMeta.textContent = `${shown.toLocaleString('fr-CA')} affiches (page ${page}), ${total.toLocaleString('fr-CA')} resultat(s) apres filtres.`;

    const stats = payload.stats || {};
    elements.statTotal.textContent = Number(stats.total || total).toLocaleString('fr-CA');
    elements.statEstablishments.textContent = Number(stats.establishments || 0).toLocaleString('fr-CA');
    elements.statAmount.textContent = formatMoney(Number(stats.amount_sum || 0));

    const top = (payload.top_categories || []).map((x) => [x.label, Number(x.count || 0)]);
    renderMiniBars(elements.topCategories, top);
  }

  async function fetchAndRender() {
    clampPage();
    const qs = buildQuery();
    setStatus('info', 'Requete en cours...');
    const res = await fetch(`${VIOLATIONS_URL}?${qs.toString()}`, { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const payload = await res.json();
    renderFromResponse(payload);
    setStatus('ok', 'Pret.');
  }

  function debounce(fn, ms) {
    let t = 0;
    return (...args) => {
      window.clearTimeout(t);
      t = window.setTimeout(() => fn(...args), ms);
    };
  }

  function attachHandlers() {
    const onChange = () => {
      state.page = 1;
      fetchAndRender().catch((e) => setStatus('err', `Erreur: ${e.message || String(e)}`));
    };
    const onSearch = debounce(onChange, 220);

    elements.q.addEventListener('input', onSearch);
    elements.status.addEventListener('change', onChange);
    elements.category.addEventListener('change', onChange);
    elements.city.addEventListener('change', onChange);
    elements.fromDate.addEventListener('change', onChange);
    elements.toDate.addEventListener('change', onChange);
    elements.sort.addEventListener('change', onChange);
    elements.pageSize.addEventListener('change', onChange);

    elements.reset.addEventListener('click', () => {
      elements.q.value = '';
      elements.status.value = '';
      elements.category.value = '';
      elements.city.value = '';
      elements.fromDate.value = '';
      elements.toDate.value = '';
      elements.sort.value = 'date_desc';
      elements.pageSize.value = '25';
      state.page = 1;
      fetchAndRender().catch((e) => setStatus('err', `Erreur: ${e.message || String(e)}`));
    });

    elements.prevPage.addEventListener('click', () => {
      state.page -= 1;
      fetchAndRender().catch((e) => setStatus('err', `Erreur: ${e.message || String(e)}`));
    });
    elements.nextPage.addEventListener('click', () => {
      state.page += 1;
      fetchAndRender().catch((e) => setStatus('err', `Erreur: ${e.message || String(e)}`));
    });
  }

  async function load() {
    setStatus('info', 'Chargement des filtres...');
    const facetsRes = await fetch(FACETS_URL, { cache: 'no-store' });
    if (!facetsRes.ok) throw new Error(`HTTP ${facetsRes.status}`);
    const f = await facetsRes.json();

    optionize(elements.status, uniqSorted(f.status || []));
    optionize(elements.category, uniqSorted(f.category || []));
    optionize(elements.city, uniqSorted(f.city || []));

    state.page = 1;
    await fetchAndRender();
  }

  attachHandlers();
  load().catch((err) => {
    const message = err && err.message ? err.message : String(err);
    setStatus('err', `Impossible de charger les donnees (${message}). Verifie ta connexion et la source CSV.`);
  });
})();
