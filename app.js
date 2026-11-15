(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const colors = { 'all-american-road': '#ad4a95', 'national-scenic-byway': '#26816a' };
  const labels = { 'all-american-road': 'All-American Road', 'national-scenic-byway': 'National Scenic Byway' };
  const regions = { conus: [[24.2, -125.8], [49.7, -66.2]], alaska: [[51, -168], [66.4, -129]] };
  const state = { search: '', state: '', allAmerican: true, nationalScenic: true, endpoints: true, region: 'conus' };
  let map, tiles, renderer, catalog, selectedRoute, selectedLine, selectionPopup;
  let endpoints = [];
  let records = [];
  let filtered = [];
  let requestSequence = 0;
  let detailController;
  let fetching = false;
  let tileErrors = 0;
  let tileSuccesses = 0;
  const detailCache = new Map();
  const escapeHTML = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const coordsToLatLng = (lines) => lines.map((line) => line.map((point) => [point[1], point[0]]));
  const safeURL = (value) => { try { const url = new URL(value, location.href); return ['https:', 'http:'].includes(url.protocol) ? url.href : ''; } catch (_) { return ''; } };
  const stateNames = (route) => Array.isArray(route.states) ? route.states : [route.states].filter(Boolean);
  const isAlaska = (route) => stateNames(route).includes('Alaska');
  const closePanel = () => { $('panel').classList.remove('is-expanded'); $('panelToggle').setAttribute('aria-expanded', 'false'); };

  function showStatus(mode, title = '', message = '') {
    $('dataStatus').hidden = mode === 'ready';
    $('dataStatus').classList.toggle('loading', mode === 'loading');
    $('dataStatus').classList.toggle('error', mode === 'error');
    $('statusTitle').textContent = title;
    $('statusMessage').textContent = message;
    $('retryData').hidden = mode !== 'error';
    document.body.dataset.state = mode;
  }

  function fitBounds(bounds) {
    if (!map) return;
    map.invalidateSize({ pan: false });
    if (matchMedia('(max-width:760px)').matches) closePanel();
    map.fitBounds(bounds, { paddingTopLeft: [30, 100], paddingBottomRight: [30, 110], animate: false, maxZoom: 13 });
  }

  function setRegion(region) {
    state.region = region;
    $('viewConus').classList.toggle('active', region === 'conus');
    $('viewAlaska').classList.toggle('active', region === 'alaska');
    $('viewConus').setAttribute('aria-pressed', String(region === 'conus'));
    $('viewAlaska').setAttribute('aria-pressed', String(region === 'alaska'));
    fitBounds(regions[region]);
  }

  function fitFiltered() {
    if (selectedRoute) { fitBounds(selectedRoute.bounds); return; }
    const candidates = state.state ? filtered : filtered.filter((route) => isAlaska(route) === (state.region === 'alaska'));
    if (!candidates.length) { fitBounds(regions[state.region]); return; }
    const bounds = L.latLngBounds([]);
    candidates.forEach((route) => bounds.extend(route.bounds));
    if (bounds.isValid()) fitBounds(bounds);
  }

  function initMap() {
    map = L.map('map', { preferCanvas: true, scrollWheelZoom: true, zoomSnap: .25, zoomDelta: .5, minZoom: 2, maxZoom: 19 }).setView([38, -97], 4);
    map.attributionControl.setPrefix(false);
    // Every route shares one canvas, preventing empty higher canvases from swallowing clicks.
    renderer = L.canvas({ padding: .35, tolerance: 5 });
    tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a>',
      maxZoom: 19, updateWhenIdle: true, keepBuffer: 2
    }).addTo(map);
    tiles.on('loading', () => { tileErrors = 0; tileSuccesses = 0; });
    tiles.on('tileerror', () => { tileErrors++; if (tileErrors >= 8 && tileSuccesses < 3) $('tileNotice').hidden = false; });
    tiles.on('tileload', () => { tileSuccesses++; if (tileSuccesses >= 3) $('tileNotice').hidden = true; });
    map.on('popupopen', () => { $('mapHint').hidden = true; });
    map.on('popupclose', () => { $('mapHint').hidden = false; });
  }

  function routePopup(route, loaded = false) {
    const source = safeURL(route.descriptionSourceUrl || route.sourceUrl);
    return `<article><p class="popup-eyebrow">AMERICA’S BYWAYS · ${escapeHTML(route.id)}</p><h2 class="popup-title">${escapeHTML(route.name)}</h2><div class="popup-quality" style="--qcolor:${colors[route.designation] || colors['national-scenic-byway']}">${escapeHTML(labels[route.designation] || route.designation)}</div><p class="popup-note">${escapeHTML(stateNames(route).join(' · '))}</p><p class="popup-note">${escapeHTML(route.description || '')}</p><p class="popup-metrics">${loaded ? '完整路线已加载' : '正在加载完整路线…'} · ${Number(route.pointCount || 0).toLocaleString()} 个来源坐标</p>${source ? `<div class="popup-sources"><a href="${escapeHTML(source)}" target="_blank" rel="noopener">FHWA 路线资料 ↗</a></div>` : ''}<p class="popup-disclaimer">颜色表示公路类别；不作为驾驶导航。</p></article>`;
  }

  function removeEndpoints() { endpoints.forEach((marker) => map.removeLayer(marker)); endpoints = []; }
  function showEndpoints(detail) {
    removeEndpoints();
    if (!state.endpoints || !detail?.endpoints) return;
    for (const point of detail.endpoints) {
      if (!Array.isArray(point.coords) || point.coords.length < 2) continue;
      const marker = L.marker([point.coords[1], point.coords[0]], {
        title: point.name || '路线起终点', alt: point.name || '路线起终点',
        icon: L.divIcon({ className: 'route-endpoint', html: '<span></span>', iconSize: [16, 16], iconAnchor: [8, 8] })
      });
      marker.bindPopup(`<article><p class="popup-eyebrow">ROUTE ENDPOINT</p><h2 class="popup-title">${escapeHTML(point.name || '路线起终点')}</h2><p class="popup-note">${escapeHTML(selectedRoute?.name || '')}</p></article>`, { maxWidth: 300 });
      marker.addTo(map); endpoints.push(marker);
    }
  }

  function clearSelection({ fit = false } = {}) {
    requestSequence++;
    detailController?.abort();
    if (selectedLine) { map.removeLayer(selectedLine); selectedLine = null; }
    removeEndpoints();
    selectedRoute = null;
    delete document.body.dataset.selectedRoute;
    delete document.body.dataset.detailState;
    $('selectedSection').hidden = true;
    map?.closePopup();
    syncLayers(); renderResults(); updateCaption();
    if (fit) fitFiltered();
  }

  function renderSelected(route) {
    $('selectedSection').hidden = false;
    $('selectedName').textContent = route.name;
    $('selectedStates').textContent = stateNames(route).join(' · ');
    $('selectedDesignation').textContent = labels[route.designation] || route.designation;
    $('selectedDesignation').style.setProperty('--route-color', colors[route.designation]);
    $('selectedNote').textContent = route.description || '';
    $('selectedMetrics').innerHTML = `<div><dt>来源坐标</dt><dd>${Number(route.pointCount || 0).toLocaleString()}</dd></div><div><dt>原始线段</dt><dd>${Number(route.segmentCount || 0).toLocaleString()}</dd></div><div><dt>几何来源</dt><dd>${escapeHTML(route.geometrySource || '已有KML')}</dd></div>`;
    const source = safeURL(route.descriptionSourceUrl || route.sourceUrl);
    $('selectedSource').hidden = !source;
    if (source) $('selectedSource').href = source;
    $('detailMessage').textContent = '正在加载完整路线…';
    $('detailMessage').classList.remove('error');
    $('retryDetail').hidden = true;
    document.body.dataset.detailState = 'loading';
  }

  async function selectRoute(route, { fit = true } = {}) {
    const sequence = ++requestSequence;
    detailController?.abort();
    detailController = new AbortController();
    if (selectedLine) { map.removeLayer(selectedLine); selectedLine = null; }
    removeEndpoints();
    selectedRoute = route;
    renderSelected(route); renderResults(); syncLayers(); updateCaption();
    if (fit) fitBounds(route.bounds);
    if (matchMedia('(max-width:760px)').matches) closePanel();
    const center = L.latLngBounds(route.bounds).getCenter();
    selectionPopup = L.popup({ maxWidth: 350, minWidth: 230, autoPanPaddingTopLeft: [18, 100], autoPanPaddingBottomRight: [18, 100] }).setLatLng(center).setContent(routePopup(route)).openOn(map);
    try {
      let detail = detailCache.get(String(route.id));
      if (!detail) {
        const url = new URL(route.detailUrl, location.href);
        if (url.origin !== location.origin) throw new Error('invalid_detail_url');
        const response = await fetch(url, { signal: detailController.signal, headers: { Accept: 'application/json' } });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        detail = await response.json();
        if (!Array.isArray(detail.lines) || !detail.lines.length || String(detail.id) !== String(route.id)) throw new Error('invalid_detail');
        detailCache.set(String(route.id), detail);
      }
      if (sequence !== requestSequence || selectedRoute?.id !== route.id) return;
      const record = records.find((item) => item.route.id === route.id);
      if (record && map.hasLayer(record.line)) map.removeLayer(record.line);
      selectedLine = L.polyline(coordsToLatLng(detail.lines), { renderer, color: colors[route.designation], weight: 4.7, opacity: .98, smoothFactor: .35, lineCap: 'round', lineJoin: 'round' }).addTo(map);
      selectedLine.bindPopup(routePopup(route, true), { maxWidth: 350, minWidth: 230, autoPanPaddingTopLeft: [18, 100], autoPanPaddingBottomRight: [18, 100] });
      selectedLine.bringToFront();
      showEndpoints(detail);
      $('detailMessage').textContent = '完整原始路线已加载';
      $('detailMessage').classList.remove('error');
      if (selectionPopup?.isOpen()) selectionPopup.setContent(routePopup(route, true));
      document.body.dataset.selectedRoute = String(route.id);
      document.body.dataset.detailState = 'ready';
    } catch (error) {
      if (error.name === 'AbortError' || sequence !== requestSequence) return;
      $('detailMessage').textContent = '详细路线暂时无法加载，当前仍显示概览。';
      $('detailMessage').classList.add('error');
      $('retryDetail').hidden = false;
      document.body.dataset.detailState = 'error';
    }
  }

  function routeMatches(route) {
    if (route.designation === 'all-american-road' && !state.allAmerican) return false;
    if (route.designation === 'national-scenic-byway' && !state.nationalScenic) return false;
    if (state.state && !stateNames(route).includes(state.state)) return false;
    const haystack = `${route.name} ${stateNames(route).join(' ')}`.normalize('NFKD').toLowerCase();
    return !state.search || haystack.includes(state.search.normalize('NFKD').toLowerCase());
  }

  function syncLayers() {
    if (!map) return;
    const visibleIds = new Set(filtered.map((route) => route.id));
    for (const record of records) {
      const visible = visibleIds.has(record.route.id) && !(selectedLine && selectedRoute?.id === record.route.id);
      if (visible && !map.hasLayer(record.line)) record.line.addTo(map);
      else if (!visible && map.hasLayer(record.line)) map.removeLayer(record.line);
      record.line.setStyle({ opacity: selectedRoute && record.route.id !== selectedRoute.id ? .45 : .86 });
    }
    selectedLine?.bringToFront();
  }

  function renderResults() {
    const fragment = document.createDocumentFragment();
    for (const route of filtered) {
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'route-result';
      button.classList.toggle('is-selected', selectedRoute?.id === route.id);
      button.setAttribute('aria-pressed', String(selectedRoute?.id === route.id));
      button.style.setProperty('--route-color', colors[route.designation]);
      button.innerHTML = `<span class="result-dot" aria-hidden="true"></span><span><strong>${escapeHTML(route.name)}</strong><small>${escapeHTML(stateNames(route).join(' · '))}</small><em>${escapeHTML(labels[route.designation])}</em></span><span class="result-arrow" aria-hidden="true">›</span>`;
      button.addEventListener('click', () => selectRoute(route));
      fragment.appendChild(button);
    }
    $('routeList').replaceChildren(fragment);
    $('emptyResults').hidden = filtered.length > 0;
    $('resultCount').textContent = `${filtered.length} / ${catalog?.routes.length || 0} 条路线`;
  }

  function updateCaption() {
    $('viewTitle').textContent = selectedRoute?.name || 'America’s Byways';
    $('viewSubtitle').textContent = selectedRoute ? '选中路线 · 完整细节按需加载' : `${filtered.length} 条路线 · 按州或名称探索`;
    $('panelToggleText').textContent = selectedRoute ? `${selectedRoute.name} · 路线与图层` : `${filtered.length} 条风景公路 · 搜索与图层`;
    document.body.dataset.visibleRoutes = String(filtered.length);
  }

  function applyFilters({ fit = false } = {}) {
    if (!catalog) return;
    filtered = catalog.routes.filter(routeMatches);
    if (selectedRoute && !filtered.some((route) => route.id === selectedRoute.id)) clearSelection();
    syncLayers(); renderResults(); updateCaption();
    if (fit) {
      if (state.state === 'Alaska') state.region = 'alaska';
      else if (state.state) state.region = 'conus';
      $('viewConus').classList.toggle('active', state.region === 'conus');
      $('viewAlaska').classList.toggle('active', state.region === 'alaska');
      $('viewConus').setAttribute('aria-pressed', String(state.region === 'conus'));
      $('viewAlaska').setAttribute('aria-pressed', String(state.region === 'alaska'));
      fitFiltered();
    }
  }

  function renderCatalog(data) {
    if (!Array.isArray(data.routes) || !data.routes.length || !Array.isArray(data.states)) throw new Error('invalid_catalog');
    catalog = data;
    records.forEach((record) => map.removeLayer(record.line)); records = [];
    $('allAmericanCount').textContent = data.meta?.allAmericanCount ?? data.routes.filter((route) => route.designation === 'all-american-road').length;
    $('nationalScenicCount').textContent = data.meta?.nationalScenicCount ?? data.routes.filter((route) => route.designation === 'national-scenic-byway').length;
    $('stateFilter').replaceChildren(new Option('所有州', ''));
    data.states.forEach((name) => $('stateFilter').add(new Option(name, name)));
    for (const route of data.routes) {
      if (!Array.isArray(route.overview) || !route.overview.length) continue;
      const line = L.polyline(coordsToLatLng(route.overview), { renderer, color: colors[route.designation] || colors['national-scenic-byway'], weight: route.designation === 'all-american-road' ? 2.8 : 2.2, opacity: .86, smoothFactor: .8, lineCap: 'round', lineJoin: 'round' });
      line.bindTooltip(escapeHTML(route.name), { sticky: true });
      line.on('click', () => selectRoute(route));
      records.push({ route, line });
    }
    applyFilters(); setRegion('conus'); requestAnimationFrame(() => fitBounds(regions.conus));
  }

  async function loadCatalog() {
    if (fetching) return;
    if (!window.L) { showStatus('error', '地图组件未能加载', '请刷新重试，或前往右上角资料下载页。'); return; }
    fetching = true;
    showStatus('loading', '正在展开美国风景公路', '载入路线索引与全国概览…');
    try {
      if (!map) initMap();
      const response = await fetch('./data/index.json?v=byway-intros-1', { headers: { Accept: 'application/json' } });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      renderCatalog(await response.json()); showStatus('ready');
    } catch (error) {
      console.error('Could not load byways:', error);
      showStatus('error', '路线资料暂时无法加载', '请检查网络后重试，或前往资料下载页。');
    } finally { fetching = false; }
  }

  $('routeSearch').addEventListener('input', () => { state.search = $('routeSearch').value.trim(); applyFilters(); });
  $('stateFilter').addEventListener('change', () => { state.state = $('stateFilter').value; applyFilters({ fit: true }); });
  for (const [id, property] of [['allAmerican', 'allAmerican'], ['nationalScenic', 'nationalScenic']]) $(id).addEventListener('change', () => { state[property] = $(id).checked; applyFilters(); });
  $('resetFilters').addEventListener('click', () => {
    state.search = ''; state.state = ''; state.allAmerican = true; state.nationalScenic = true;
    $('routeSearch').value = ''; $('stateFilter').value = ''; $('allAmerican').checked = true; $('nationalScenic').checked = true;
    clearSelection(); applyFilters(); setRegion('conus');
  });
  $('clearSelection').addEventListener('click', () => clearSelection({ fit: true }));
  $('retryDetail').addEventListener('click', () => { if (selectedRoute) selectRoute(selectedRoute, { fit: false }); });
  $('showEndpoints').addEventListener('change', () => { state.endpoints = $('showEndpoints').checked; if (selectedRoute) showEndpoints(detailCache.get(String(selectedRoute.id))); });
  $('viewConus').addEventListener('click', () => setRegion('conus'));
  $('viewAlaska').addEventListener('click', () => setRegion('alaska'));
  $('fitFiltered').addEventListener('click', fitFiltered);
  $('panelToggle').addEventListener('click', () => { const expanded = $('panel').classList.toggle('is-expanded'); $('panelToggle').setAttribute('aria-expanded', String(expanded)); });
  $('retryData').addEventListener('click', () => { if (!window.L) location.reload(); else loadCatalog(); });
  $('retryTiles').addEventListener('click', () => { tileErrors = 0; tileSuccesses = 0; $('tileNotice').hidden = true; tiles?.redraw(); });
  window.addEventListener('resize', () => map?.invalidateSize({ pan: false }));
  loadCatalog();
})();
