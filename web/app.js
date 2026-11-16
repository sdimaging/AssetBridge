"use strict";
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const h = (tag, attrs = {}, ...kids) => {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (v !== false && v != null) el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids) if (kid != null) el.append(kid);
    return el;
  };
  const enc = encodeURIComponent;

  const TYPES = {
    "3d": { label: "3D Assets", icon: "ic-cube", fit: true },
    plant: { label: "Plants", icon: "ic-leaf", fit: true },
    surface: { label: "Surfaces", icon: "ic-surface", fit: false },
    atlas: { label: "Atlases", icon: "ic-atlas", fit: true },
    decal: { label: "Decals", icon: "ic-decal", fit: true },
    hdri: { label: "HDRIs", icon: "ic-sun", fit: false },
    splat: { label: "Splats", icon: "ic-splat", fit: false },
  };
  const MAP_ORDER = ["albedo", "normal", "roughness", "gloss", "metalness", "specular", "ao", "displacement", "bump", "opacity", "translucency", "cavity", "emission"];
  const SWATCHES = ["#1f1f1f", "#5e5e5e", "#a8a8a8", "#ececec", "#4a3a2a", "#7d5f42", "#b89a6e",
                    "#6e2a1f", "#a84e2c", "#d39b3c", "#e3cf8a", "#3f5a2c", "#7f9a52", "#2c4f63"];
  const PAGE = 240;
  const APPNAME = (window.BRAND && window.BRAND.name) || "the asset browser";
  const ACCENTS = [["gold", "#d6aa4f", "Graphite · Gold"], ["violet", "#a98cfa", "Graphite · Violet"],
                   ["ember", "#ec5f57", "Graphite · Ember"], ["teal", "#3fc2b1", "Graphite · Teal"]];

  const store = {
    get(k, d) { try { const v = localStorage.getItem("bridge2." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem("bridge2." + k, JSON.stringify(v)); } catch {} },
  };

  // one-time option resets when a default changes meaning (v2: splat real scale now bakes a copy, default off)
  if (store.get("opts_v", 1) < 2) {
    const o = store.get("opts", {});
    delete o.real_scale; delete o.fast_viewport;
    store.set("opts", o); store.set("opts_v", 2);
  }

  const S = {
    view: "all", type: null, library: null, q: "", sort: store.get("sort", "name"), color: null,
    items: [], total: 0, loading: false, done: false, gen: 0,
    sel: null, selAsset: null, facets: null,
    opts: Object.assign({ resolution: "4K", renderer: "octane", displacement: true, ao: true,
                          apply_to_selection: true, use_native: true, disp_exr: false,
                          replace_environment: true, splat_mode: "both", real_scale: false, fast_viewport: false,
                          instance: true },
                        store.get("opts", {})),
    local: { lod: null, variations: null },
    c4dOnline: false,
    lastTarget: store.get("target", "c4d"),
    heroMode: store.get("hero", "image"),
    label: null, filters: new Set(), multi: new Set(), allLabels: [], health: null, healthTitle: null,
    viewer: null, files: null, lb: null,
  };

  async function api(path, body) {
    const r = await fetch(path, body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    return r.json();
  }


  function toast(msg, err = false) {
    const t = h("div", { class: "toast" + (err ? " err" : ""), text: msg });
    $("#toasts").append(t);
    setTimeout(() => t.remove(), err ? 7000 : 3500);
  }

  // ------------------------------------------------------------- sidebar
  async function loadFacets() {
    const f = await api("/api/facets");
    S.facets = f;
    $("#count-all").textContent = f.total || "";
    $("#count-fav").textContent = f.favorites || "";
    $("#count-recent").textContent = f.recent || "";
    const tl = $("#type-list");
    tl.replaceChildren(...f.types.map(t => {
      const info = TYPES[t.type] || { label: t.type, icon: "ic-cube" };
      return h("button", { class: "nav-item" + (S.type === t.type ? " active" : ""), "data-type": t.type,
                           onclick: () => setFilter({ type: S.type === t.type ? null : t.type }) },
        h("i", { class: "ic " + info.icon }), h("span", { text: info.label }), h("em", { text: t.n }));
    }));
    const libs = await api("/api/libraries");
    const counts = Object.fromEntries(f.libraries.map(l => [l.library, l.n]));
    $("#lib-list").replaceChildren(...libs.map(l => h("div", {
        role: "button", tabindex: "0", class: "nav-item" + (S.library === l.name ? " active" : ""), title: l.path,
        onclick: () => setFilter({ library: S.library === l.name ? null : l.name }) },
      h("i", { class: "ic ic-lib" }), h("span", { text: l.name }),
      h("button", { class: "rm", title: "Remove library (files are not touched)", text: "×", "aria-label": "Remove library",
                    onclick: e => { e.stopPropagation(); removeLibrary(l.name); } }),
      h("em", { text: counts[l.name] ?? "–" }))));
    $$("#sidebar [data-view]").forEach(b => b.classList.toggle("active", b.dataset.view === S.view && !S.type && !S.library && !S.label));
    // labels + saved filters
    S.allLabels = f.labels || [];
    $("#sec-labels").hidden = !S.allLabels.length;
    $("#label-list").replaceChildren(...S.allLabels.map(l => h("button", {
        class: "nav-item" + (S.label === l.label ? " active" : ""), onclick: () => setFilter({ label: S.label === l.label ? null : l.label }) },
      h("i", { class: "ic ic-tag" }), h("span", { text: l.label }), h("em", { text: l.n }))));
    $("#label-options").replaceChildren(...S.allLabels.map(l => h("option", { value: l.label })));
    const saved = f.saved_filters || [];
    $("#sec-saved").hidden = !saved.length;
    $("#saved-list").replaceChildren(...saved.map(sf => h("div", { role: "button", tabindex: "0", class: "nav-item",
        title: describeQuery(sf.query), onclick: () => applySaved(sf.query) },
      h("i", { class: "ic ic-bookmark" }), h("span", { text: sf.name }),
      h("button", { class: "rm", text: "×", title: "Delete saved filter", "aria-label": "Delete saved filter",
        onclick: async (e) => { e.stopPropagation(); await api("/api/saved-filter", { name: sf.name, query: null }); loadFacets(); } }))));
  }

  // ------------------------------------------------------------- filters / saved views
  const FILTER_GROUPS = [
    ["Maps", [["has:normal", "Normal"], ["has:displacement", "Displacement"], ["has:opacity", "Opacity"], ["has:translucency", "Translucency"], ["has:metalness", "Metalness"], ["has:ao", "AO"]]],
    ["Geometry", [["has:proxy", "Has proxy"], ["native", "C4D scene"], ["blender", "Works in Blender"]]],
    ["Min resolution", [["res>=2K", "2K"], ["res>=4K", "4K"], ["res>=8K", "8K"], ["res>=16K", "16K"]]],
    ["Format", [["fmt:fbx", "FBX"], ["fmt:glb", "GLB"], ["fmt:obj", "OBJ"], ["fmt:abc", "ABC"], ["fmt:usd", "USD"], ["fmt:ply", "PLY"], ["fmt:spz", "SPZ"]]],
    ["Housekeeping", [["no:preview", "No preview"], ["conformed", (window.BRAND && window.BRAND.format_name) || "Asset Format"]]],
    ["Availability", [["local", "On disk"], ["remote", "On Fab (not downloaded)"]]],
  ];
  const FILTER_LABEL = Object.fromEntries(FILTER_GROUPS.flatMap(([g, items]) => items.map(([k, l]) => [k, g === "Min resolution" ? "≥ " + l : l])));
  const RES_KEYS = FILTER_GROUPS[2][1].map(([k]) => k);

  // smart tags: one vocabulary across every library (server/tagging.py), counted for the current library / type
  const TAG_FACETS = [["kind", "Kind"], ["material", "Material"], ["setting", "Setting"], ["condition", "Condition"], ["size", "Size"]];
  const SIZE_ORDER = ["tiny", "small", "medium", "large", "huge"];
  S.tagFacets = {};
  async function loadTagFacets() {
    const p = new URLSearchParams();
    if (S.library) p.set("library", S.library);
    if (S.type) p.set("type", S.type);
    const r = await api("/api/tags?" + p);
    S.tagFacets = r.facets || {};
    if (!$("#filter-pop").hidden) renderFilterPop();
  }
  function tagGroups() {
    return TAG_FACETS.filter(([f]) => (S.tagFacets[f] || []).length).map(([f, title]) => {
      let tags = S.tagFacets[f].slice(0, 18);
      if (f === "size") tags = tags.slice().sort((a, b) => SIZE_ORDER.indexOf(a[0]) - SIZE_ORDER.indexOf(b[0]));
      return h("div", { class: "fgroup" }, h("h4", { text: title }),
        h("div", { class: "opts-row" }, ...tags.map(([t, n]) => h("button", { class: S.filters.has("tag:" + t) ? "on" : "",
          onclick: () => toggleFilter("tag:" + t) }, t, h("em", { class: "n", text: n })))));
    });
  }

  function renderFilterPop() {
    const pop = $("#filter-pop");
    pop.replaceChildren(...tagGroups(), ...FILTER_GROUPS.map(([g, items]) => h("div", { class: "fgroup" }, h("h4", { text: g }),
      h("div", { class: "opts-row" }, ...items.map(([k, l]) => h("button", { class: S.filters.has(k) ? "on" : "", text: l,
        onclick: () => toggleFilter(k) }))))),
      h("div", { class: "pop-actions" },
        h("button", { class: "mini", text: "Clear filters", onclick: () => { S.filters.clear(); afterFilters(); } }),
        h("button", { class: "mini", text: "Save this view…", onclick: saveView })));
    $("#filter-count").textContent = S.filters.size || "";
  }
  function toggleFilter(k) {
    if (S.filters.has(k)) S.filters.delete(k);
    else { if (RES_KEYS.includes(k)) RES_KEYS.forEach(r => S.filters.delete(r)); S.filters.add(k); }
    afterFilters();
  }
  function afterFilters() { renderFilterPop(); reload(); }

  function currentQuery() {
    return { q: S.q, type: S.type, library: S.library, label: S.label, view: S.view, color: S.color, sort: S.sort, filters: [...S.filters] };
  }
  function describeQuery(qr) {
    const bits = [];
    if (qr.q) bits.push("“" + qr.q + "”");
    if (qr.type) bits.push((TYPES[qr.type] || {}).label || qr.type);
    if (qr.library) bits.push(qr.library);
    if (qr.label) bits.push("label " + qr.label);
    (qr.filters || []).forEach(f => bits.push(FILTER_LABEL[f] || (f.startsWith("tag:") ? "#" + f.slice(4) : f)));
    if (qr.view === "fav") bits.push("favorites");
    return bits.join(" · ") || "everything";
  }
  async function saveView() {
    const name = window.prompt ? window.prompt("Name this view", describeQuery(currentQuery())) : null;
    if (!name) return;
    const r = await api("/api/saved-filter", { name, query: currentQuery() });
    if (!r.ok) return toast(r.error, true);
    toast("Saved “" + name + "”"); loadFacets();
  }
  function applySaved(qr) {
    Object.assign(S, { q: qr.q || "", type: qr.type || null, library: qr.library || null, label: qr.label || null,
                       view: qr.view || "all", color: qr.color || null, sort: qr.sort || S.sort, filters: new Set(qr.filters || []) });
    $("#q").value = S.q; $("#sort").value = S.sort;
    $("#color-swatch").style.background = S.color || "";
    renderFilterPop(); loadFacets(); reload();
  }

  // ------------------------------------------------------------- labels + multi-select
  function renderLabels(a) {
    const box = $("#d-labels");
    const input = h("input", { list: "label-options", placeholder: "+ label", "aria-label": "Add a label",
      onkeydown: async (e) => {
        if (e.key !== "Enter" || !e.target.value.trim()) return;
        e.preventDefault(); e.stopPropagation();
        await setLabel([a.id], e.target.value.trim(), true);
      } });
    box.replaceChildren(...(a.labels || []).map(l => h("span", { class: "lchip" }, l,
      h("button", { text: "×", title: "Remove label", "aria-label": "Remove label " + l, onclick: () => setLabel([a.id], l, false) }))), input);
  }
  async function setLabel(ids, label, on) {
    await api("/api/label", { ids, label, on });
    if (S.selAsset && ids.includes(S.selAsset.id)) {
      const set = new Set(S.selAsset.labels || []);
      on ? set.add(label) : set.delete(label);
      S.selAsset.labels = [...set].sort();
      renderLabels(S.selAsset);
      setTimeout(() => { const i = $("#d-labels input"); i && i.focus(); }, 0);
    }
    loadFacets();
    if (S.label === label && !on) reload();
  }
  function togglePick(id, el) {
    S.multi.has(id) ? S.multi.delete(id) : S.multi.add(id);
    el.classList.toggle("picked", S.multi.has(id));
    renderMultibar();
  }
  function renderMultibar() {
    $("#multibar").hidden = !S.multi.size;
    $("#multi-count").textContent = S.multi.size + " selected";
  }

  function setFilter(patch) {
    Object.assign(S, patch);
    if ("view" in patch) { S.type = null; S.library = null; S.label = null; S.health = null; }
    const showHealth = S.view === "health";
    $("#health").hidden = !showHealth;
    $("#grid-wrap").hidden = showHealth;
    $("#crumbs").hidden = showHealth;
    loadFacets();
    if ("library" in patch || "type" in patch || "view" in patch) loadTagFacets();
    if (showHealth) { renderHealth(); return; }
    reload();
  }

  // ------------------------------------------------------------- library health
  let healthTimer = null;
  async function renderHealth() {
    const r = await api("/api/health");
    const st = r.state || {};
    const issues = r.issues || [];
    const errs = issues.filter(i => i.severity === "error").reduce((n, i) => n + i.count, 0);
    const warns = issues.filter(i => i.severity === "warn").reduce((n, i) => n + i.count, 0);
    $("#count-health").textContent = (errs + warns) || "";
    if (S.view !== "health") return;
    const when = r.generated_at ? new Date(r.generated_at * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : null;
    const head = h("div", { class: "h-head" },
      h("div", {}, h("h2", { text: "Library health" }),
        h("p", { text: st.running ? `Checking ${(st.done || 0).toLocaleString()} of ${(st.total || 0).toLocaleString() || "…"} assets…`
                                  : r.error ? `The last check failed: ${r.error}`
                                  : when ? `Checked ${(r.assets || 0).toLocaleString()} assets at ${when} in ${r.seconds}s` : "Not checked yet" })),
      h("div", { class: "h-sum" },
        st.running ? h("div", { class: "h-progress" }, h("i", { style: `width:${st.total ? (100 * st.done / st.total) | 0 : 5}%` })) : null,
        h("span", {}, h("b", { text: errs }), " to fix"), h("span", {}, h("b", { text: warns }), " to review"),
        h("button", { class: "ghost", text: st.running ? "Checking…" : "Check again", disabled: st.running,
          onclick: async () => { await api("/api/health/run", {}); renderHealth(); } })));
    const cards = issues.map(i => h("div", { class: "h-card", "data-sev": i.severity },
      h("div", { class: "t" }, h("h3", { text: i.title }), i.count ? h("span", { class: "n", text: i.count.toLocaleString() }) : null),
      h("p", { text: i.detail }),
      h("div", { class: "acts" },
        i.count ? h("button", { class: "ghost", text: `Show ${i.count === 1 ? "asset" : "assets"}`,
          onclick: () => { Object.assign(S, { view: "all", type: null, library: null, label: null, health: i.code, healthTitle: i.title });
                           $("#health").hidden = true; $("#grid-wrap").hidden = false; $("#crumbs").hidden = false; loadFacets(); reload(); } }) : null,
        i.action ? h("button", { class: "ghost", text: i.action.label,
          onclick: async () => { const x = await api("/api/health/action", { do: i.action.do }); toast(x.message || x.error || "Done", !x.ok); renderHealth(); } }) : null)));
    $("#health").replaceChildren(head, issues.length ? h("div", { class: "h-list" }, ...cards)
      : h("div", { class: "h-empty", text: st.running ? "Working…" : "No issues found." }));
    clearTimeout(healthTimer);
    if (st.running) healthTimer = setTimeout(renderHealth, 1500);
  }

  // ------------------------------------------------------------- grid
  function queryString(offset) {
    const p = new URLSearchParams({ offset, limit: PAGE, sort: S.sort });
    if (S.q) p.set("q", S.q);
    if (S.type) p.set("type", S.type);
    if (S.library) p.set("library", S.library);
    if (S.view === "fav") p.set("fav", "1");
    if (S.view === "recent") p.set("recent", "1");
    if (S.color) p.set("color", S.color);
    if (S.label) p.set("label", S.label);
    if (S.health) p.set("health", S.health);
    if (S.filters.size) p.set("f", [...S.filters].join(","));
    return p.toString();
  }

  function reload() {
    S.gen++;
    S.items = [];
    S.done = false;
    S.loading = false;  // a request from the old generation may still be in flight; it can no longer clear this flag
    $("#grid").replaceChildren();
    $("#grid-wrap").scrollTop = 0;
    renderCrumbs();
    loadMore();
  }

  async function loadMore() {
    if (S.loading || S.done) return;
    S.loading = true;
    const gen = S.gen;
    try {
      const r = await api("/api/assets?" + queryString(S.items.length));
      if (gen !== S.gen) return;
      S.total = r.total;
      const start = S.items.length;
      S.items.push(...r.items);
      S.done = S.items.length >= r.total || r.items.length === 0;
      const frag = document.createDocumentFragment();
      r.items.forEach((a, i) => frag.append(card(a, start + i)));
      $("#grid").append(frag);
      $("#result-count").textContent = S.total.toLocaleString() + (S.total === 1 ? " asset" : " assets");
      $("#empty").hidden = S.total > 0;
      if (!S.total) $("#empty-msg").textContent = S.q ? `Nothing matches “${S.q}”.` :
        S.view === "fav" ? "Star assets to collect them here." :
        S.view === "recent" ? "Assets you send to Cinema 4D show up here." : "Add a library folder to get started.";
    } catch (e) {
      if (gen === S.gen) toast("Couldn't load assets: " + e, true);
      return;
    } finally {
      if (gen === S.gen) S.loading = false;
    }
    // keep filling until the sentinel is off-screen
    requestAnimationFrame(() => { if (!S.done && sentinelVisible()) loadMore(); });
  }

  function sentinelVisible() {
    const r = $("#sentinel").getBoundingClientRect();
    return r.top < window.innerHeight + 600;
  }

  function card(a, idx) {
    const info = TYPES[a.type] || {};
    const img = h("img", { loading: "lazy", decoding: "async", alt: "", src: "/thumb/" + enc(a.id) + (a.tv ? "?v=" + a.tv : "") });
    img.onload = () => img.classList.add("loaded");
    const fav = h("button", { class: "favbtn" + (a.fav ? " on" : ""), title: "Favorite",
      onclick: e => { e.stopPropagation(); toggleFav(a, fav); } }, h("i", { class: "ic ic-star" }));
    let send = h("div", { class: "sendgroup" },
      h("button", { class: "sendbtn", title: "Send to Cinema 4D",
        onclick: e => { e.stopPropagation(); sendAsset(a.id, el, "c4d"); } }, h("i", { class: "ic ic-send" }), "C4D"),
      a.blender_ok === false ? null :
      h("button", { class: "sendbtn alt", title: "Send to Blender",
        onclick: e => { e.stopPropagation(); sendAsset(a.id, el, "blender"); } }, h("i", { class: "ic ic-send" }), "Blender"));
    if (a.remote) send = h("div", { class: "sendgroup" },
      h("button", { class: "sendbtn", title: "Download from Fab (" + tierLabel(S.opts.fab_tier) + ") - double-click downloads and sends",
        onclick: e => { e.stopPropagation(); fabPull(a.id, null, el); } }, h("i", { class: "ic ic-cloud" }), "Get"));
    const el = h("div", { class: "card" + (info.fit ? " fit" : "") + (a.remote ? " remote" : "") + (S.sel === a.id ? " sel" : "") + (S.multi.has(a.id) ? " picked" : ""),
                          tabindex: "0", role: "option", "data-id": a.id, "data-idx": idx,
                          onclick: (e) => (e.ctrlKey || e.metaKey) ? togglePick(a.id, el) : select(a.id),
                          ondblclick: () => sendAsset(a.id, el, a.blender_ok === false ? "c4d" : S.lastTarget) },
      h("div", { class: "thumb" }, img,
        h("span", { class: "badge", text: ({ "3d": "3D", hdri: "HDRI" })[a.type] || a.type[0].toUpperCase() + a.type.slice(1) }),
        a.resolutions && a.resolutions.length ? h("span", { class: "res", text: a.resolutions.join(" · ") }) : null,
        a.remote ? h("span", { class: "cloud", title: "On Fab - not downloaded yet" }, h("i", { class: "ic ic-cloud" }), "Fab") : null,
        fav, send),
      h("div", { class: "info" },
        h("div", { class: "name", text: a.name, title: a.name }),
        h("div", { class: "sub", text: (S.library ? "" : a.library + " · ") + (a.subtitle || "") })));
    if (a.color) el.append(h("div", { class: "colorbar", style: "background:" + a.color }));
    return el;
  }

  function renderCrumbs() {
    const chips = [];
    const chip = (label, clear, sw) => h("span", { class: "fchip" },
      sw ? h("span", { class: "sw", style: "background:" + sw }) : null, label,
      h("button", { title: "Clear", text: "×", onclick: clear }));
    if (S.type) chips.push(chip((TYPES[S.type] || {}).label || S.type, () => setFilter({ type: null })));
    if (S.library) chips.push(chip(S.library, () => setFilter({ library: null })));
    if (S.color) chips.push(chip("Color", () => setColor(null), S.color));
    if (S.label) chips.push(chip("Label: " + S.label, () => setFilter({ label: null })));
    if (S.health) chips.push(chip("Health: " + S.healthTitle, () => { S.health = null; reload(); renderCrumbs(); }));
    S.filters.forEach(f => chips.push(chip(FILTER_LABEL[f] || (f.startsWith("tag:") ? "#" + f.slice(4) : f), () => { S.filters.delete(f); afterFilters(); })));
    if (chips.length || S.q) chips.push(h("button", { class: "save-view", text: "Save view…", onclick: saveView }));
    $("#active-filters").replaceChildren(...chips);
  }

  // ------------------------------------------------------------- detail
  async function select(id) {
    S.sel = id;
    $$(".card.sel").forEach(c => c.classList.remove("sel"));
    const c = $(`.card[data-id="${CSS.escape(id)}"]`);
    if (c) c.classList.add("sel");
    const a = await api("/api/asset/" + enc(id));
    if (S.sel !== id || a.error) return;
    S.selAsset = a;
    S.files = null;
    S.local = { lod: a.lods && a.lods.length ? (a.lods.includes("LOD0") ? "LOD0" : a.lods[0]) : null,
                variations: a.variations && a.variations.length ? [a.variations[0]] : null,
                layers: a.layers && a.layers.length ? a.layers.slice() : null, view: 0 };
    renderDetail();
  }

  function renderDetail() {
    const a = S.selAsset;
    const d = $("#detail");
    if (!a) { d.hidden = true; return; }
    d.hidden = false;
    const info = TYPES[a.type] || {};
    $("#d-hero").className = "hero" + (info.fit ? " fit" : "");
    const img = $("#d-img");
    const fresh = img.dataset.id !== a.id;
    if (fresh || img.dataset.tv !== String(a.tv || "")) {  // new asset, or its preview was re-rendered/saved over
      img.src = "/preview/" + enc(a.id) + (a.tv ? "?v=" + a.tv : "");
      img.dataset.id = a.id; img.dataset.tv = String(a.tv || "");
      if (fresh) setHero(S.heroMode);
    }
    $("#t-3d").hidden = !!a.remote;  // nothing to load until it's pulled
    if (a.remote && S.heroMode === "3d") { $("#d-img").hidden = false; $("#d-viewer").hidden = true; $("#d-vbar").hidden = true; }
    $("#d-name").textContent = a.name;
    $("#d-fav").classList.toggle("on", !!a.favorite);
    const meta = [a.library, info.label || a.type];
    if (a.size) meta.push(a.size);
    if (a.format && a.type !== "splat") meta.push(a.native && S.opts.use_native ? "C4D scene" : a.format.toUpperCase());
    if (a.source_id) meta.push(a.source_id);
    $("#d-meta").replaceChildren(...meta.map(m => h("span", { text: m })));
    const types = (a.map_types || []).slice().sort((x, y) => MAP_ORDER.indexOf(x) - MAP_ORDER.indexOf(y));
    $("#d-maps").replaceChildren(...types.map(t => h("button", { class: "chip map", text: t, title: "View the " + t + " map",
      onclick: () => openMap(t) })),
      ...(a.meshes && a.meshes.length ? [h("span", { class: "chip k", text: `${a.meshes.length} mesh file${a.meshes.length > 1 ? "s" : ""}` })] : []),
      ...(a.native ? [h("span", { class: "chip k", text: "C4D scene" })] : []),
      ...(a.parts && a.parts.length ? [h("span", { class: "chip k", text: `${a.parts.length} material parts`, title: a.parts.join(", ") })] : []));

    const isHdri = a.type === "hdri";
    const isSplat = a.type === "splat";
    const hasMaps = types.length > 0 && !isHdri;
    if (isSplat) {
      const chips = (a.splat_formats || []).map(f => h("span", { class: "chip k", text: f }));
      if ((a.layers || []).length > 1) chips.push(h("span", { class: "chip k", text: a.layers.join(" + "),
        title: "Imports as " + a.layers.length + " splats together, one per layer" }));
      if (a.points) chips.push(h("span", { class: "chip", text: (a.points / 1e6).toFixed(a.points >= 1e6 ? 1 : 2) + "M splats" }));
      if (a.sh_degree != null) chips.push(h("span", { class: "chip", text: "SH" + a.sh_degree }));
      if ((a.meshes || []).length) chips.push(h("span", { class: "chip", text: "proxy " + a.meshes.map(m => m.format.toUpperCase()).join("/") }));
      $("#d-maps").replaceChildren(...chips);
    }
    const usingNative = a.native && S.opts.use_native;
    const hasProxy = (a.meshes || []).length > 0;
    seg("#o-splatmode", isSplat ? (hasProxy ? ["both", "splat", "proxy"] : ["splat"]) : [],
        [hasProxy ? S.opts.splat_mode : "splat"], v => { S.opts.splat_mode = v; saveOpts(); renderDetail(); },
        { both: "Splat + proxy", splat: "Splat only", proxy: "Proxy only" });
    seg("#o-layers", isSplat && (a.layers || []).length > 1 && S.opts.splat_mode !== "proxy" ? a.layers : [], S.local.layers || [], v => {
      const set = new Set(S.local.layers || []);
      set.has(v) ? set.delete(v) : set.add(v);
      S.local.layers = set.size ? a.layers.filter(x => set.has(x)) : [v];  // at least one layer
      renderDetail();
    });
    renderViews(a);
    seg("#o-res", a.resolutions || [], [pickRes(a)], v => { S.opts.resolution = v; saveOpts(); renderDetail(); });
    seg("#o-lod", (a.lods || []).length > 1 && !usingNative ? a.lods : [], [S.local.lod], v => { S.local.lod = v; renderDetail(); });
    const vars = (a.variations || []).length > 1 ? [...a.variations, "All"] : [];
    const curVars = S.local.variations && S.local.variations.length === a.variations?.length && vars.length ? ["All", ...S.local.variations] : (S.local.variations || []);
    seg("#o-var", vars, curVars, v => {
      if (v === "All") S.local.variations = curVars.includes("All") ? [a.variations[0]] : a.variations.slice();
      else {
        const set = new Set(S.local.variations || []);
        set.has(v) ? set.delete(v) : set.add(v);
        S.local.variations = set.size ? a.variations.filter(x => set.has(x)) : [v];
      }
      renderDetail();
    });
    seg("#o-renderer", (hasMaps || isHdri) && !usingNative ? ["octane", "standard"] : [], [S.opts.renderer],
        v => { S.opts.renderer = v; saveOpts(); renderDetail(); },
        isHdri ? { octane: "Octane HDRI", standard: "C4D Sky" } : { octane: "Octane Universal", standard: "C4D Standard" });
    $("#o-renderer label").textContent = isHdri ? "C4D environment" : "C4D material";
    $("#o-res").hidden = !(a.resolutions || []).length || usingNative;

    const show = (sel, on) => { $(sel).hidden = !on; };
    show("#o-native", !!a.native);
    show("#o-env", isHdri);
    show("#o-realscale", isSplat && !!a.real_size_m);
    show("#o-instance", ["3d", "plant", "splat"].includes(a.type));
    show("#o-fastvp", isSplat && S.opts.splat_mode !== "proxy");
    show("#o-disp", hasMaps && types.includes("displacement") && !(a.meshes || []).length && !usingNative && S.opts.renderer === "octane");
    show("#o-exr", !$("#o-disp").hidden && S.opts.displacement);
    show("#o-ao", hasMaps && types.includes("ao") && !types.includes("metalness") && !usingNative && S.opts.renderer === "octane");
    show("#o-sel", hasMaps && !(a.meshes || []).length && !a.native);
    sendLabels(false);
    $$("#d-opts input[type=checkbox]").forEach(cb => { cb.checked = !!S.opts[cb.dataset.key]; });

    $("#d-result").hidden = true;
    $("#d-opts").hidden = !!a.remote;
    renderFab(a);
    renderLabels(a);
    const smart = a.smart_tags || {};  // clickable smart tags first (filter), then the vendor's own words (search)
    $("#d-tags").replaceChildren(
      ...TAG_FACETS.flatMap(([f]) => (smart[f] || []).map(t => h("button", { class: "smart" + (S.filters.has("tag:" + t) ? " on" : ""),
        text: t, title: "Filter: " + f + " = " + t, onclick: () => toggleFilter("tag:" + t) }))),
      ...(a.tags || []).filter(t => !/^\d+$|[\\/]/.test(t)).slice(0, 24).map(t =>
        h("button", { text: t, title: "Search for " + t, onclick: () => { $("#q").value = t; S.q = t; reload(); } })));
  }

  // ------------------------------------------------------------- hero: image / 3D
  // assets with several preview images (e.g. combined, alternate, one per layer) get view buttons
  function renderViews(a) {
    const bar = $("#d-views");
    const pv = (a.previews || []).map(p => p.split(/[\\/]/).pop().replace(/\.[^.]+$/, ""));
    if (pv.length < 2 || S.heroMode === "3d") { bar.hidden = true; return; }
    let pre = pv[0];
    for (const n of pv) while (pre && !n.startsWith(pre)) pre = pre.slice(0, -1);
    pre = pre.replace(/[^_ -]*$/, "");
    const label = n => (n.slice(pre.length).replace(/^thumb_/i, "").replace(/_/g, " ").replace(/\balt\b/i, "2") || "1")
      .replace(/^./, c => c.toUpperCase());
    bar.replaceChildren(...pv.map((n, i) => h("button", { text: label(n), title: n, class: i === (S.local.view || 0) ? "on" : "",
      onclick: () => { S.local.view = i; $("#d-img").src = "/preview/" + enc(a.id) + (i ? "?i=" + i : ""); renderViews(a); } })));
    bar.hidden = false;
  }

  let heroTimer = null;
  function setHero(mode) {
    S.heroMode = mode; store.set("hero", mode);
    $("#t-image").classList.toggle("on", mode === "image");
    $("#t-3d").classList.toggle("on", mode === "3d");
    $("#d-img").hidden = mode === "3d";
    $("#d-viewer").hidden = mode !== "3d";
    $("#d-vbar").hidden = true;
    if (S.selAsset) renderViews(S.selAsset);
    if (mode === "3d" && S.selAsset) { clearTimeout(heroTimer); heroTimer = setTimeout(() => load3D(S.selAsset), 220); }
    else { $("#d-vstatus").hidden = true; }
  }

  async function filesFor(a) {
    if (S.files && S.files.id === a.id) return S.files;
    S.files = await api("/api/files/" + enc(a.id));
    return S.files;
  }

  async function load3D(a) {
    const status = (msg, err) => {
      const el = $("#d-vstatus");
      el.hidden = !msg; el.textContent = msg || ""; el.classList.toggle("err", !!err);
    };
    status("Loading 3D…");
    try {
      if (!S.viewer) {
        const mod = await import("/web/viewer.js?v=" + (window.APP_BUILD || Date.now()));
        S.viewer = new mod.Viewer($("#d-viewer"));
      }
      const files = await filesFor(a);
      if (!S.selAsset || S.selAsset.id !== a.id || S.heroMode !== "3d") return;
      const chans = await S.viewer.show(a, files, status);
      if (!S.selAsset || S.selAsset.id !== a.id) return;
      renderVbar(a, chans);
    } catch (e) { status("3D preview failed: " + (e.message || e), true); }
  }

  function renderVbar(a, chans) {
    const bar = $("#d-vbar");
    const items = [];
    if (chans.length > 1) chans.forEach(c => items.push(h("button", { text: c === "lit" ? "Lit" : c, class: c === "lit" ? "on" : "",
      onclick: (e) => { S.viewer.setChannel(c); $$("#d-vbar button[data-ch]").forEach(b => b.classList.toggle("on", b === e.target)); },
      "data-ch": c })));
    items.push(h("span", { class: "sp" }));
    if (a.type === "splat" && (a.meshes || []).length) items.push(h("button", { text: "Proxy", title: "Overlay the proxy mesh",
      onclick: (e) => { const on = !e.target.classList.contains("on"); e.target.classList.toggle("on", on); S.viewer.toggleProxy(on); } }));
    items.push(h("button", { text: "Spin", class: "on", title: "Auto-rotate",
      onclick: (e) => { const on = !e.target.classList.contains("on"); e.target.classList.toggle("on", on); S.viewer.controls.autoRotate = on; } }));
    bar.replaceChildren(...items);
    bar.hidden = false;
  }

  // ------------------------------------------------------------- map lightbox
  async function openMap(type) {
    const a = S.selAsset; if (!a) return;
    const files = await filesFor(a);
    const maps = files.files.filter(f => f.role === "map" || f.role === "part_map");
    // one entry per map type (+ part), best resolution, OpenGL normal over DirectX
    const seen = new Map();
    for (const f of maps.sort((x, y) => (parseInt(y.res) || 0) - (parseInt(x.res) || 0))) {
      const key = (f.part ? f.part + " · " : "") + f.kind;
      if (f.variant === "DX" && seen.has(key)) continue;
      if (!seen.has(key)) seen.set(key, f);
    }
    S.lb = { list: [...seen.entries()], i: Math.max(0, [...seen.keys()].findIndex(k => k === type || k.endsWith("· " + type))) };
    showLb();
  }
  function showLb() {
    const { list, i } = S.lb; if (!list.length) return;
    const [key, f] = list[i];
    $("#lb-img").src = f.url + "?s=2048";
    $("#lb-label").textContent = `${key} · ${f.res || ""} · ${f.name}  (${i + 1}/${list.length})`;
    $("#lightbox").hidden = false;
  }
  function stepLb(d) { if (!S.lb) return; S.lb.i = (S.lb.i + d + S.lb.list.length) % S.lb.list.length; showLb(); }

  function pickRes(a) {
    const rs = a.resolutions || [];
    if (!rs.length) return null;
    if (rs.includes(S.opts.resolution)) return S.opts.resolution;
    const want = parseInt(S.opts.resolution) || 4;
    const below = rs.filter(r => parseInt(r) <= want);
    return below.length ? below[below.length - 1] : rs[0];
  }

  function seg(sel, values, current, onPick, labels = {}) {
    const wrap = $(sel);
    wrap.hidden = !values.length;
    $(".seg", wrap).replaceChildren(...values.map(v => h("button", {
      class: current.includes(v) ? "on" : "", text: labels[v] || v, onclick: () => onPick(v) })));
  }

  function saveOpts() { store.set("opts", S.opts); }

  // ------------------------------------------------------------- Fab: on Fab until pulled (server/fab.py)
  const TIER_NAMES = { low: "Low", mid: "Mid", high: "High", raw: "Raw", file: "File" };
  const tierLabel = t => TIER_NAMES[t || "high"] || t;
  const mb = n => n == null ? "" : n >= 1e9 ? (n / 1e9).toFixed(1) + " GB" : Math.max(1, Math.round(n / 1e6)) + " MB";
  S.fabFiles = {};     // asset id -> /api/fab/files reply
  S.pulling = new Map(); // asset id -> job id (one pull per asset)

  async function renderFab(a) {
    const box = $("#d-fab");
    box.hidden = !a.fab;
    if (!a.fab) return;
    $("#d-fab-link").href = a.fab.url;
    const state = $("#d-fab-state");
    state.textContent = a.remote ? "On Fab · not downloaded" : "On disk · " + tierLabel(a.fab.tier) + (a.fab.bytes ? " · " + mb(a.fab.bytes) : "");
    $("#d-fab-evict").hidden = !!a.remote;
    $("#d-fab-note").textContent = a.remote ? "Cinema 4D / Blender sends download it first, at the quality picked here." : "";
    const paint = (info) => {
      if (!S.selAsset || S.selAsset.id !== a.id) return;
      const tiers = info && info.ok ? Object.keys(info.tiers).sort((x, y) => (info.tiers[x].size || 0) - (info.tiers[y].size || 0)) : [];
      if (!S.opts.fab_tier && info && info.default) S.opts.fab_tier = info.default;
      const cur = tiers.includes(S.opts.fab_tier) ? S.opts.fab_tier : (tiers.includes("high") ? "high" : tiers[tiers.length - 1]);
      seg("#o-fabtier", tiers, [cur], v => { S.opts.fab_tier = v; saveOpts(); renderFab(S.selAsset); },
          Object.fromEntries(tiers.map(t => [t, (info.tiers[t].label || tierLabel(t)) + " · " + mb(info.tiers[t].size)])));
      const get = $("#d-fab-get");
      const busy = S.pulling.has(a.id);
      const same = !a.remote && cur === a.fab.tier;
      get.hidden = same && !busy;
      get.disabled = busy;
      $("span", get).textContent = busy ? "Downloading…" : (a.remote ? "Download " : "Re-download as ") +
        (tiers.length ? info.tiers[cur].label || tierLabel(cur) : tierLabel(cur)) +
        (tiers.length ? " (" + mb(info.tiers[cur].size) + ")" : "");
      if (info && !info.ok) $("#d-fab-note").textContent = info.error;
      const copy = info && info.ok && (info.local_copies || [])[0];
      if (copy && a.remote) $("#d-fab-note").replaceChildren("Already on disk in " + copy.library +
        (copy.resolutions.length ? " (" + copy.resolutions.join(", ") + ")" : "") + " · ",
        h("button", { class: "link", text: "Open that copy", onclick: () => select(copy.id) }));
    };
    paint(S.fabFiles[a.id]);
    if (!S.fabFiles[a.id]) {
      try { S.fabFiles[a.id] = await api("/api/fab/files?id=" + enc(a.id)); } catch (e) { S.fabFiles[a.id] = { ok: false, error: String(e) }; }
      paint(S.fabFiles[a.id]);
    }
    if (S.pulling.has(a.id)) fabWatch(a.id, S.pulling.get(a.id));
  }

  async function fabPull(id, tier, cardEl) {
    if (S.pulling.has(id)) return;
    const r = await api("/api/fab/pull", { id, tier: tier || S.opts.fab_tier || null });
    if (!r.ok) return toast(r.error || "Couldn't start the download", true);
    S.pulling.set(id, r.job);
    const el = cardEl || $(`.card[data-id="${CSS.escape(id)}"]`);
    el && el.classList.add("sending");
    if (S.selAsset && S.selAsset.id === id) renderFab(S.selAsset);
    fabWatch(id, r.job);
  }

  // follow one pull to its end: the server holds each request until the job moves on or a second passes (no sleeps)
  const fabWatching = new Set();
  async function fabWatch(id, jobId) {
    if (fabWatching.has(jobId)) return;
    fabWatching.add(jobId);
    let j;
    try {
      for (;;) {
        j = await api("/api/jobs/" + enc(jobId) + "?wait=1");
        if (S.selAsset && S.selAsset.id === id) {
          const p = $("#d-fab-prog");
          p.hidden = false;
          p.classList.toggle("indet", !(j.total > 0));
          $(".bar i", p).style.width = j.total > 0 ? Math.min(100, 100 * j.done / j.total) + "%" : "";
          $("span", p).textContent = j.step || j.state;
        }
        if (!["queued", "running"].includes(j.state)) break;
      }
    } catch (e) { j = { state: "failed", error: String(e) }; }
    fabWatching.delete(jobId);
    S.pulling.delete(id);
    const el = $(`.card[data-id="${CSS.escape(id)}"]`);
    el && el.classList.remove("sending");
    if (S.selAsset && S.selAsset.id === id) $("#d-fab-prog").hidden = true;
    if (j.state !== "succeeded") return toast("Download failed: " + (j.error || j.state), true);
    toast("Downloaded " + (j.message || ""));
    fabLanded(id, el);
  }

  // a pull finished: the card becomes a normal local asset, and the open detail panel reloads it
  async function fabLanded(id, el) {
    delete S.fabFiles[id];
    const a = await api("/api/asset/" + enc(id));
    if (a.error) return;
    el = el || $(`.card[data-id="${CSS.escape(id)}"]`);
    if (el && el.isConnected) {
      const item = { id: a.id, library: a.library, type: a.type, name: a.name, color: a.color, fav: !!a.favorite,
        resolutions: a.resolutions || [], has_thumb: !!a.preview, format: a.format, importable: a.importable,
        subtitle: a.subtitle, blender_ok: a.blender_ok, remote: !!a.remote, tv: a.tv };
      el.replaceWith(card(item, +el.dataset.idx));
    }
    if (S.selAsset && S.selAsset.id === id) select(id);
  }

  async function fabEvict(id) {
    const a = S.selAsset;
    if (!a || a.id !== id) return;
    if (!confirm("Delete the downloaded files for “" + a.name + "”" + (a.fab.bytes ? " (" + mb(a.fab.bytes) + ")" : "") +
                 "? It stays in AssetBridge and can be downloaded again.")) return;
    const r = await api("/api/fab/evict", { id });
    if (!r.ok) return toast(r.error || "Couldn't free the space", true);
    toast(r.message);
    fabLanded(id);
  }

  // ------------------------------------------------------------- actions
  // Which sends are in flight ("<id>|<target>"): the buttons are derived from it, so re-rendering the panel
  // mid-send can't reset them, and the same send can't be started twice from the card, keys or buttons.
  S.sending = new Set();
  function sendLabels() {
    const id = S.selAsset && S.selAsset.id;
    const busyTarget = S.sending.has(id + "|c4d") ? "c4d" : S.sending.has(id + "|blender") ? "blender" : false;
    const hdri = S.selAsset && S.selAsset.type === "hdri";
    $("span", $("#d-send")).textContent = busyTarget === "c4d" ? "Sending…" : hdri ? "C4D environment" : "Cinema 4D";
    $("span", $("#d-send-b")).textContent = busyTarget === "blender" ? "Sending…" : hdri ? "Blender world" : "Blender";
    const noBlender = S.selAsset && S.selAsset.blender_ok === false;
    $("#d-send").disabled = busyTarget === "c4d";
    $("#d-send-b").disabled = busyTarget === "blender" || noBlender;
    $("#d-send-b").title = noBlender ? "This asset only ships a .c4d scene — no mesh Blender can open"
                                     : "Send to Blender (Shift+Enter)";
  }

  async function sendAsset(id, cardEl, target = "c4d") {
    const flight = id + "|" + target;
    if (S.sending.has(flight)) return;  // already on its way
    S.sending.add(flight);
    const el = cardEl || $(`.card[data-id="${CSS.escape(id)}"]`);
    const app = target === "blender" ? "Blender" : "Cinema 4D";
    S.lastTarget = target;
    store.set("target", target);
    const fromDetail = S.selAsset && S.selAsset.id === id;
    const options = Object.assign({}, S.opts);
    const remote = el ? el.classList.contains("remote") : !!(S.selAsset && S.selAsset.id === id && S.selAsset.remote);
    if (remote) toast("Downloading from Fab (" + tierLabel(S.opts.fab_tier) + "), then sending to " + app + "…");
    if (fromDetail) {
      options.resolution = pickRes(S.selAsset) || S.opts.resolution;
      if (S.local.lod) options.lod = S.local.lod;
      if (S.local.variations) options.variations = S.local.variations;
      if (S.local.layers) options.splat_layers = S.local.layers;
    }
    el && el.classList.remove("done", "failed");
    el && el.classList.add("sending");
    if (fromDetail) sendLabels();
    let r;
    try {
      r = await api("/api/send", { id, options, target });
    } catch (e) {
      r = { ok: false, error: String(e) };
    } finally {
      S.sending.delete(flight);
    }
    el && el.classList.remove("sending");
    sendLabels();
    if (r.ok) {
      el && (el.classList.add("done"), setTimeout(() => el.classList.remove("done"), 1500));
      toast(r.message || "Sent to " + app);
      if (remote) fabLanded(id, el);
    } else {
      el && el.classList.add("failed");
      toast(r.error || "Send failed", true);
    }
    if (fromDetail && S.selAsset && S.selAsset.id === id) {
      const box = $("#d-result");
      box.hidden = false;
      box.className = "d-result " + (r.ok ? "ok" : "err");
      box.textContent = r.ok ? `${r.message} · ${r.elapsed}s` : r.error + (r.missing ? "\n" + r.missing.join("\n") : "");
    }
    if (r.ok) api("/api/facets").then(f => { $("#count-recent").textContent = f.recent || ""; });
  }

  async function toggleFav(a, btn) {
    const on = !(btn ? btn.classList.contains("on") : a.favorite);
    await api("/api/favorite", { id: a.id, on });
    if (btn) btn.classList.toggle("on", on);
    const c = $(`.card[data-id="${CSS.escape(a.id)}"] .favbtn`);
    if (c) c.classList.toggle("on", on);
    if (S.selAsset && S.selAsset.id === a.id) { S.selAsset.favorite = on; $("#d-fav").classList.toggle("on", on); }
    const f = await api("/api/facets");
    $("#count-fav").textContent = f.favorites || "";
  }

  function setColor(c) {
    S.color = c;
    $("#color-swatch").style.background = c || "";
    $("#color-pop").hidden = true;
    reload();
  }

  async function removeLibrary(name) {
    if (!confirm(`Remove “${name}” from ${APPNAME}? Files on disk are not touched.`)) return;
    await api("/api/libraries", { action: "remove", name });
    if (S.library === name) S.library = null;
    loadFacets(); reload();
  }

  // ------------------------------------------------------------- status
  // Responses are applied newest-first only (the slow ?c4d=1 poll can land after a newer quick one), and an
  // unreachable server is shown as such instead of leaving the last state on screen.
  let lastScanRunning = false, lastScanTick = 0, statusSeq = 0, statusApplied = 0;
  function jobText(j) {
    const pct = j.total ? ` ${j.done}/${j.total}` : "";
    return `${j.label}${j.step ? " · " + j.step : ""}${pct}`;
  }
  async function pollStatus(withC4D) {
    const seq = ++statusSeq;
    let st;
    const box = $("#scan-status");
    try { st = await api("/api/status" + (withC4D ? "?c4d=1" : "")); } catch {
      if (seq < statusApplied) return;
      statusApplied = seq;
      box.classList.add("on");
      $(".txt", box).textContent = "AssetBridge server not responding…";
      box.title = "The local server isn't answering; it may be restarting";
      return;
    }
    if (seq < statusApplied) return;  // an older, slower response
    statusApplied = seq;
    const sc = st.scan;
    const others = (st.jobs || []).filter(j => j.kind !== "scan" && j.kind !== "send");
    box.classList.toggle("on", sc.running || others.length > 0);
    box.title = (st.jobs || []).map(jobText).join("\n");
    if (sc.running) {
      $(".txt", box).textContent = `Indexing ${sc.library || ""} · ${sc.seen.toLocaleString()}`;
      if (Date.now() - lastScanTick > 6000 && sc.changed) { lastScanTick = Date.now(); loadFacets(); if (S.items.length < PAGE) reload(); }
    } else if (others.length) {
      $(".txt", box).textContent = jobText(others[0]) + (others.length > 1 ? ` (+${others.length - 1})` : "");
    }
    if (lastScanRunning && !sc.running) {
      loadFacets(); reload();
      if (sc.errors && sc.errors.length) toast(sc.errors.join(" · "), true);
    }
    lastScanRunning = sc.running;
    if (st.blender) {
      const b = $("#blender-status");
      b.className = "status " + (st.blender.online ? "on" : "off");
      $(".txt", b).textContent = st.blender.online
        ? `Blender ${String(st.blender.blender_version || "").split(" ")[0]}` : "Blender not connected";
      b.title = st.blender.online ? "Add-on " + st.blender.receiver + " · " + (st.blender.file || "")
                                  : `Open Blender with the ${APPNAME} add-on enabled`;
    }
    if (st.c4d) {
      const c = $("#c4d-status");
      S.c4dOnline = st.c4d.online;
      c.className = "status " + (st.c4d.online ? "on" : "off");
      $(".txt", c).textContent = st.c4d.online
        ? `Cinema 4D ${String(st.c4d.c4d_version || "").slice(0, 4)}${st.c4d.octane ? " · Octane" : ""}${(st.c4d.others || []).length ? ` (+${st.c4d.others.join(", ")} open)` : ""}`
        : "Cinema 4D not connected";
      c.title = st.c4d.online ? "Receiver " + st.c4d.receiver : `Open Cinema 4D with the ${APPNAME} plugin installed`;
    }
  }

  // ------------------------------------------------------------- wiring
  function renderThemes() {
    const cur = document.documentElement.dataset.accent || (window.BRAND && window.BRAND.accent) || "teal";
    $("#theme-swatches").replaceChildren(...ACCENTS.map(([k, c, label]) => h("button", {
      class: k === cur ? "on" : "", title: label, "aria-label": label, style: "--c:" + c,
      onclick: () => { document.documentElement.dataset.accent = k; store.set("accent", k); renderThemes(); } })));
  }

  function wire() {
    renderThemes();
    renderFilterPop();
    $("#btn-filters").addEventListener("click", e => { e.stopPropagation(); $("#filter-pop").hidden = !$("#filter-pop").hidden; $("#color-pop").hidden = true; if (!$("#filter-pop").hidden) loadTagFacets(); });
    $("#filter-pop").addEventListener("click", e => e.stopPropagation());
    $("#filter-pop").addEventListener("click", e => e.stopPropagation());
    document.addEventListener("click", () => { $("#filter-pop").hidden = true; });
    $("#multi-apply").addEventListener("click", async () => {
      const l = $("#multi-label").value.trim(); if (!l) return $("#multi-label").focus();
      await setLabel([...S.multi], l, true);
      toast(`Labelled ${S.multi.size} asset${S.multi.size > 1 ? "s" : ""} “${l}”`);
      $("#multi-label").value = "";
    });
    $("#multi-label").addEventListener("keydown", e => { if (e.key === "Enter") $("#multi-apply").click(); e.stopPropagation(); });
    $("#multi-clear").addEventListener("click", () => { S.multi.clear(); $$(".card.picked").forEach(c => c.classList.remove("picked")); renderMultibar(); });
    let t;
    $("#q").addEventListener("input", e => {
      clearTimeout(t);
      t = setTimeout(() => { S.q = e.target.value.trim(); reload(); }, 160);
    });
    $("#sort").value = S.sort;
    $("#sort").addEventListener("change", e => { S.sort = e.target.value; store.set("sort", S.sort); reload(); });
    const size = store.get("size", 180);
    $("#size").value = size;
    document.documentElement.style.setProperty("--card-size", size + "px");
    $("#size").addEventListener("input", e => {
      document.documentElement.style.setProperty("--card-size", e.target.value + "px");
      store.set("size", +e.target.value);
    });
    $$("#sidebar [data-view]").forEach(b => b.addEventListener("click", () => setFilter({ view: b.dataset.view })));
    $("#btn-rescan").addEventListener("click", async () => {
      const r = await api("/api/rescan", {});
      toast(r.ok ? "Rescanning libraries…" : "A scan is already running");
      setTimeout(() => pollStatus(false), 300);
    });

    // color
    $("#swatches").replaceChildren(...SWATCHES.map(c => h("button", { style: "background:" + c, title: c, onclick: () => setColor(c) })));
    $("#btn-color").addEventListener("click", e => { e.stopPropagation(); $("#color-pop").hidden = !$("#color-pop").hidden; });
    $("#color-pop").addEventListener("click", e => e.stopPropagation());
    $("#color-custom").addEventListener("change", e => setColor(e.target.value));
    $("#color-clear").addEventListener("click", () => setColor(null));
    document.addEventListener("click", () => { $("#color-pop").hidden = true; });

    // detail
    $("#t-image").addEventListener("click", () => setHero("image"));
    $("#t-3d").addEventListener("click", () => setHero("3d"));
    $("#lb-close").addEventListener("click", () => { $("#lightbox").hidden = true; });
    $("#lb-prev").addEventListener("click", () => stepLb(-1));
    $("#lb-next").addEventListener("click", () => stepLb(1));
    $("#lightbox").addEventListener("click", (e) => { if (e.target.id === "lightbox") $("#lightbox").hidden = true; });
    $("#detail-close").addEventListener("click", closeDetail);
    $("#d-send").addEventListener("click", () => S.selAsset && sendAsset(S.selAsset.id, null, "c4d"));
    $("#d-send-b").addEventListener("click", () => S.selAsset && sendAsset(S.selAsset.id, null, "blender"));
    $("#d-fav").addEventListener("click", () => S.selAsset && toggleFav(S.selAsset, null));
    $("#d-reveal").addEventListener("click", () => S.selAsset && api("/api/reveal", { id: S.selAsset.id }));
    $("#d-fab-get").addEventListener("click", () => S.selAsset && fabPull(S.selAsset.id, S.opts.fab_tier));
    $("#d-fab-evict").addEventListener("click", () => S.selAsset && fabEvict(S.selAsset.id));
    $("#d-remove").addEventListener("click", () => S.selAsset && askRemove([S.selAsset.id]));
    $("#multi-remove").addEventListener("click", () => askRemove([...S.multi]));
    $("#del-cancel").addEventListener("click", () => $("#del-dialog").close());
    $("#btn-removed").addEventListener("click", async () => { await loadRemoved(); $("#removed-dialog").showModal(); });
    $("#removed-close").addEventListener("click", () => $("#removed-dialog").close());
    $("#removed-all").addEventListener("click", async () => { const r = await api("/api/removed"); restore((r.items || []).map(i => i.id)); });
    $("#d-copy").addEventListener("click", async () => {
      if (!S.selAsset) return;
      try { await navigator.clipboard.writeText(S.selAsset.path); toast("Path copied"); } catch { toast(S.selAsset.path); }
    });
    $$("#d-opts input[type=checkbox]").forEach(cb => cb.addEventListener("change", () => {
      S.opts[cb.dataset.key] = cb.checked; saveOpts(); renderDetail();
    }));

    // library dialog
    const dlg = $("#lib-dialog");
    $("#btn-add-lib").addEventListener("click", () => { $("#lib-form").reset(); $("#lib-err").hidden = true; dlg.showModal(); });
    $("#lib-cancel").addEventListener("click", () => dlg.close());
    $("#btn-browse").addEventListener("click", async () => {
      const r = await api("/api/pickfolder", {});
      if (r.path) {
        const els = $("#lib-form").elements;
        els.libpath.value = r.path;
        if (!els.libname.value) els.libname.value = r.path.split(/[\\/]/).filter(Boolean).pop();
      }
    });
    $("#lib-form").addEventListener("submit", async e => {
      e.preventDefault();
      const f = e.target.elements;
      const r = await api("/api/libraries", { action: "add", name: f.libname.value, path: f.libpath.value, kind: f.libkind.value });
      if (!r.ok) { $("#lib-err").textContent = r.error; $("#lib-err").hidden = false; return; }
      dlg.close();
      toast(`Added ${f.libname.value} — indexing…`);
      loadFacets();
      setTimeout(() => pollStatus(false), 300);
    });

    // infinite scroll
    new IntersectionObserver(es => { if (es.some(e => e.isIntersecting)) loadMore(); },
      { root: $("#grid-wrap"), rootMargin: "600px" }).observe($("#sentinel"));

    // keyboard
    document.addEventListener("keydown", e => {
      const typing = e.target.matches("input, select, textarea");
      if (e.key === "/" && !typing) { e.preventDefault(); $("#q").focus(); $("#q").select(); return; }
      if (!$("#lightbox").hidden) {
        if (e.key === "Escape") $("#lightbox").hidden = true;
        else if (e.key === "ArrowLeft") stepLb(-1);
        else if (e.key === "ArrowRight") stepLb(1);
        e.preventDefault(); return;
      }
      if (e.key === "Escape") {
        if (typing) { e.target.blur(); return; }
        closeDetail(); return;
      }
      if (typing || $("#lib-dialog").open || $("#del-dialog").open || $("#removed-dialog").open) return;
      if (e.key === "Delete" && (S.multi.size || S.selAsset)) { e.preventDefault(); askRemove(S.multi.size ? [...S.multi] : [S.selAsset.id]); return; }
      if (e.key === "Enter" && S.selAsset) {
        e.preventDefault();
        if (e.shiftKey && S.selAsset.blender_ok === false) { toast("No mesh Blender can open for this asset", true); return; }
        sendAsset(S.selAsset.id, null, e.shiftKey ? "blender" : "c4d");
        return;
      }
      if ((e.key === "f" || e.key === "F") && S.selAsset) { toggleFav(S.selAsset, null); return; }
      const dirs = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: "down", ArrowUp: "up" };
      if (e.key in dirs && S.items.length) {
        e.preventDefault();
        const cols = getComputedStyle($("#grid")).gridTemplateColumns.split(" ").length;
        const cur = S.items.findIndex(a => a.id === S.sel);
        let step = dirs[e.key];
        step = step === "down" ? cols : step === "up" ? -cols : step;
        const next = Math.max(0, Math.min(S.items.length - 1, cur < 0 ? 0 : cur + step));
        const a = S.items[next];
        select(a.id);
        const el = $(`.card[data-id="${CSS.escape(a.id)}"]`);
        el && el.scrollIntoView({ block: "nearest" });
      }
    });
  }

  // ------------------------------------------------------------- remove from the library (files are kept)
  function askRemove(ids) {
    if (!ids.length) return;
    const one = ids.length === 1;
    const a = one ? (S.items.find(x => x.id === ids[0]) || S.selAsset) : null;
    $("#del-title").textContent = one ? `Delete “${(a && a.name) || "this asset"}” from ${APPNAME}?` : `Delete ${ids.length} assets from ${APPNAME}?`;
    $("#del-text").textContent = "Are you sure you want to delete " + (one ? "this asset" : "these assets") + " from the library?";
    const dlg = $("#del-dialog");
    $("#del-ok").onclick = async () => {
      dlg.close();
      const r = await api("/api/remove", { ids });
      if (!r.ok) return toast(r.error || "Could not remove", true);
      ids.forEach(i => S.multi.delete(i));
      renderMultibar();
      if (S.sel && ids.includes(S.sel)) closeDetail();
      toast(`Removed ${r.removed} asset${r.removed === 1 ? "" : "s"} from ${APPNAME} (files kept)`);
      loadFacets(); reload(); loadRemoved();
    };
    dlg.showModal();
    $("#del-cancel").focus();
  }

  async function loadRemoved() {
    const r = await api("/api/removed");
    const items = r.items || [];
    $("#btn-removed").hidden = !items.length;
    $("#count-removed").textContent = items.length || "";
    $("#removed-list").replaceChildren(...items.map(i => h("div", { class: "rrow" },
      h("span", { text: i.name, title: i.path }), h("em", { text: i.library }),
      h("button", { class: "ghost", type: "button", text: "Restore", onclick: () => restore([i.id]) }))));
    return items;
  }
  async function restore(ids) {
    const r = await api("/api/restore", { ids });
    toast(r.ok ? `Restoring ${ids.length} — rescanning ${(r.rescan || []).join(", ")}` : (r.error || "Could not restore"), !r.ok);
    const left = await loadRemoved();
    if (!left.length) $("#removed-dialog").close();
    setTimeout(() => { loadFacets(); reload(); }, 2500);
  }

  function closeDetail() {
    S.sel = null; S.selAsset = null;
    $$(".card.sel").forEach(c => c.classList.remove("sel"));
    $("#detail").hidden = true;
  }

  wire();
  loadFacets();
  loadRemoved();
  reload();
  setTimeout(renderHealth, 1500);  // fills the sidebar badge; the check runs in the background
  // deep link: ?select=<asset id>&hero=3d (used by the app and the DCC plugins)
  const qp = new URLSearchParams(location.search);
  if (qp.get("hero")) S.heroMode = qp.get("hero") === "3d" ? "3d" : "image";
  if (qp.get("debug")) { window.APP_DEBUG = true; }
  if (qp.get("select")) select(qp.get("select"));
  pollStatus(true);
  setInterval(() => pollStatus(false), 2000);
  setInterval(() => pollStatus(true), 6000);
})();
