/* Home Assistant tab: connection settings and button -> service mapping. */
(() => {
  const { api, toast, esc } = window.rc;
  const $ = (id) => document.getElementById(id);
  let entities = [], catalog = {}, actions = {}, buttons = [];

  async function loadSettings() {
    const s = await api("/api/ha/settings");
    $("ha-ssid").value = s.wifi_ssid || "";
    $("ha-url").value = s.ha_url || "";
    $("ha-pin").value = s.rx_pin || "4";
    $("ha-wpass").placeholder = s.wifi_pass_set ? "(saved, leave blank to keep)" : "WiFi password";
    $("ha-token").placeholder = s.ha_token_set ? "(saved, leave blank to keep)" : "paste the long-lived token";
    $("ha-status").textContent = s.ha_url ? "" : "not configured";
  }
  $("ha-form").onsubmit = async (e) => {
    e.preventDefault();
    try {
      await api("/api/ha/settings", { method: "PUT", body: {
        wifi_ssid: $("ha-ssid").value, wifi_pass: $("ha-wpass").value, ha_url: $("ha-url").value.trim(),
        ha_token: $("ha-token").value.trim(), rx_pin: $("ha-pin").value.trim() || "4" } });
      $("ha-wpass").value = ""; $("ha-token").value = "";
      await loadSettings(); toast("Settings saved");
    } catch (err) { toast(err.message, true); }
  };
  $("ha-test").onclick = async () => {
    $("ha-status").textContent = "testing…";
    try {
      const r = await api("/api/ha/test", { method: "POST" });
      $("ha-status").textContent = `✓ ${r.location || "Home Assistant"} ${r.version || ""}`;
      toast(`Home Assistant answered: ${r.message}`);
    } catch (err) { $("ha-status").textContent = "✗ " + err.message; toast(err.message, true); }
  };
  async function loadEntities(silent) {
    try {
      const r = await api("/api/ha/entities");
      entities = r.entities; catalog = r.actions;
      if (!silent) toast(`Loaded ${entities.length} entities`);
      renderActions();
    } catch (err) { if (!silent) toast(err.message, true); }
  }
  $("ha-load-entities").onclick = () => loadEntities(false);

  async function loadActions() {
    const r = await api("/api/ha/actions");
    actions = Object.fromEntries(Object.entries(r.actions).map(([k, v]) => [+k, v]));
    catalog = r.catalog;
    buttons = await api("/api/buttons");
    renderActions();
  }

  function entityOptions(selected) {
    const groups = {};
    for (const e of entities) (groups[e.domain] ||= []).push(e);
    let html = `<option value="">— entity —</option>`;
    for (const [dom, list] of Object.entries(groups)) {
      html += `<optgroup label="${esc(dom)}">` + list.map((e) =>
        `<option value="${esc(e.entity_id)}" ${e.entity_id === selected ? "selected" : ""}>${esc(e.name)} (${esc(e.entity_id)})</option>`).join("") + `</optgroup>`;
    }
    if (selected && !entities.some((e) => e.entity_id === selected)) {
      html += `<option value="${esc(selected)}" selected>${esc(selected)}</option>`;
    }
    return html;
  }
  function actionOptions(domain, selected) {
    const list = catalog[domain] || [];
    return `<option value="">— action —</option>` + list.map((a, i) =>
      `<option value="${i}" ${selected === i ? "selected" : ""}>${esc(a.label)}</option>`).join("");
  }
  function findActionIndex(domain, act) {
    const list = catalog[domain] || [];
    const field = act.data && Object.keys(act.data).find((k) => k !== "entity_id");
    let i = list.findIndex((a) => a.service === act.service && (a.field || null) === (field || null));
    if (i < 0) i = list.findIndex((a) => a.service === act.service);
    return i;
  }

  function renderActions() {
    const el = $("ha-actions");
    if (!buttons.length) { el.innerHTML = `<p class="empty">No buttons in the library yet. Capture and save them on the Capture tab first.</p>`; return; }
    if (!entities.length) {
      el.innerHTML = `<p class="hint">Press <b>Load entities</b> above (after saving the URL and token) to pick fans, lights and switches from your Home Assistant.</p>`;
      if (!Object.keys(actions).length) return;
    }
    const rows = buttons.map((b) => {
      const act = actions[b.id];
      const domain = act ? act.domain : "";
      const idx = act ? findActionIndex(domain, act) : -1;
      const field = idx >= 0 && catalog[domain] && catalog[domain][idx].field;
      const val = act && field && act.data ? act.data[field] : "";
      return `<tr data-bid="${b.id}">
        <td><b>${esc(b.remote_name)}</b> / ${esc(b.name)}<div class="st">${esc(b.protocol)} ${b.code != null ? "0x" + b.code.toString(16).toUpperCase() : ""}</div></td>
        <td><select class="ent">${entityOptions(act ? act.entity_id : "")}</select></td>
        <td><select class="act" ${domain ? "" : "disabled"}>${actionOptions(domain, idx)}</select></td>
        <td><input class="pct ${field ? "" : "hidden"}" type="number" min="0" max="100" value="${esc(val)}" placeholder="%"></td>
        <td class="actions">
          <button class="ghost small run" ${act ? "" : "disabled"} title="call the service now from this computer">Run</button>
          <button class="ghost small danger clear" ${act ? "" : "disabled"}>Clear</button>
        </td>
        <td class="st"></td>
      </tr>`;
    }).join("");
    el.innerHTML = `<table class="ha-table">
      <tr><th>Button</th><th>Entity</th><th>Action</th><th></th><th></th><th></th></tr>${rows}</table>`;

    el.querySelectorAll("tr[data-bid]").forEach((tr) => {
      const bid = +tr.dataset.bid, ent = tr.querySelector(".ent"), act = tr.querySelector(".act"), pct = tr.querySelector(".pct"), st = tr.querySelector("td.st");
      const domainOf = (eid) => (eid || "").split(".")[0];
      const save = async () => {
        const eid = ent.value, domain = domainOf(eid), i = act.value === "" ? -1 : +act.value;
        if (!eid || i < 0) return;
        const def = catalog[domain][i];
        const data = {};
        if (def.field) {
          if (pct.value === "") { st.textContent = "enter a %"; st.className = "st err"; return; }
          data[def.field] = +pct.value;
        }
        try {
          await api(`/api/ha/actions/${bid}`, { method: "PUT", body: { domain, service: def.service, entity_id: eid, data } });
          actions[bid] = { domain, service: def.service, entity_id: eid, data };
          st.textContent = "saved"; st.className = "st ok";
          tr.querySelector(".run").disabled = false; tr.querySelector(".clear").disabled = false;
        } catch (err) { st.textContent = err.message; st.className = "st err"; }
      };
      ent.onchange = () => {
        const d = domainOf(ent.value);
        act.disabled = !d; act.innerHTML = actionOptions(d, -1); pct.classList.add("hidden");
        if ((catalog[d] || []).length === 1) { act.value = "0"; save(); }
      };
      act.onchange = () => {
        const d = domainOf(ent.value), def = act.value !== "" && catalog[d][+act.value];
        pct.classList.toggle("hidden", !(def && def.field));
        if (def && !def.field) save();
      };
      pct.onchange = save;
      pct.onkeydown = (e) => { if (e.key === "Enter") save(); };
      tr.querySelector(".run").onclick = async () => {
        st.textContent = "calling…"; st.className = "st";
        try {
          const r = await api(`/api/ha/actions/${bid}/run`, { method: "POST" });
          st.textContent = `✓ ${r.called.replace("/api/services/", "")}`; st.className = "st ok";
        } catch (err) { st.textContent = "✗ " + err.message; st.className = "st err"; }
      };
      tr.querySelector(".clear").onclick = async () => {
        await api(`/api/ha/actions/${bid}`, { method: "DELETE" });
        delete actions[bid]; renderActions();
      };
    });
  }

  $("ha-view-config").onclick = async () => {
    const res = await fetch("/api/ha/config.h");
    const text = await res.text();
    const w = window.open("", "_blank");
    w.document.write(`<pre style="font:12px/1.5 Menlo,monospace;background:#0b0e12;color:#e6e9ee;padding:16px;white-space:pre-wrap">${esc(text)}</pre>`);
    w.document.title = "config.h";
  };
  $("ha-flash").onclick = async () => {
    const s = await api("/api/ha/settings");
    if (!s.wifi_ssid || !s.ha_url || !s.ha_token_set) return toast("Save WiFi SSID, Home Assistant URL and token first", true);
    if (!Object.keys(actions).length && !confirm("No button actions mapped yet. Flash anyway?")) return;
    window.rc.flashSketch("ha_bridge");
  };

  window.rc.onEvent("tab", (tab) => { if (tab === "ha") loadActions().then(() => entities.length || loadEntities(true)).catch(() => {}); });
  loadSettings().then(loadActions).then(() => loadEntities(true)).catch(() => {});
})();
