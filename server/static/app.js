/* RC Capture front-end: plain JS, talks to the FastAPI backend and listens to SSE. */
(() => {
  const $ = (id) => document.getElementById(id);
  const feed = $("feed"), library = $("library");
  let remotes = [];
  let allButtons = [];
  let status = {};
  let frameBlinkTimer = null;

  // ---------- helpers ---------------------------------------------------------
  async function api(path, opts = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...opts,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    if (!res.ok) {
      let msg = res.statusText;
      try { msg = (await res.json()).detail || msg; } catch (_) {}
      throw new Error(msg);
    }
    return res.status === 204 ? null : res.json();
  }
  function toast(msg, isError = false) {
    const t = $("toast");
    t.textContent = msg; t.className = "toast" + (isError ? " error" : "");
    clearTimeout(t._timer);
    t._timer = setTimeout(() => t.classList.add("hidden"), isError ? 5000 : 2500);
  }
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtTime = (iso) => iso ? new Date(iso).toLocaleTimeString() : new Date().toLocaleTimeString();

  // Pulse-train waveform: durations alternate HIGH/LOW starting with HIGH
  function waveSvg(timings, raw) {
    const total = timings.reduce((a, b) => a + b, 0) || 1;
    const W = 600, H = 40, top = 6, bottom = 34;
    let x = 0, d = `M0 ${bottom}`;
    timings.forEach((t, i) => {
      const w = (t / total) * W;
      const y = i % 2 === 0 ? top : bottom;
      d += ` V${y} H${(x + w).toFixed(2)}`;
      x += w;
    });
    d += ` V${bottom}`;
    return `<svg class="wave${raw ? " raw" : ""}" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><path d="${d}"/></svg>`;
  }

  // ---------- connection ------------------------------------------------------
  async function loadPorts() {
    const sel = $("port-select");
    const ports = await api("/api/ports");
    const cur = sel.value;
    sel.innerHTML = ports.length
      ? ports.map((p) => `<option value="${esc(p.device)}">${esc(p.device)} — ${esc(p.description)}</option>`).join("")
      : `<option value="">No serial ports found</option>`;
    if (cur && ports.some((p) => p.device === cur)) sel.value = cur;
  }
  function renderStatus(s) {
    status = s;
    const on = s.connected;
    $("link-dot").classList.toggle("on", on);
    $("btn-connect").classList.toggle("hidden", on);
    $("btn-sim").classList.toggle("hidden", on);
    $("btn-disconnect").classList.toggle("hidden", !on);
    $("port-select").disabled = on;
    const boardName = { nano: "Arduino Nano", esp32s3: "ESP32-S3" }[s.board] || s.board;
    $("st-source").textContent = on
      ? (s.source === "simulator" ? "Simulator running" : `Connected: ${s.port}${boardName ? " · " + boardName : ""}`)
      : "Not connected";
    $("st-fw").textContent = s.firmware ? `firmware ${s.firmware}` : (on && s.source === "serial" ? "waiting for firmware identification…" : "");
    $("st-frames").textContent = s.frames_seen ?? 0;
    $("st-dropped").textContent = s.frames_dropped ?? 0;
    if (document.activeElement !== $("set-repeats")) $("set-repeats").value = s.min_repeats;
    if (document.activeElement !== $("set-settle")) $("set-settle").value = s.settle_ms;
  }
  $("btn-refresh").onclick = () => loadPorts().catch((e) => toast(e.message, true));
  $("btn-connect").onclick = async () => {
    const port = $("port-select").value;
    if (!port) return toast("Plug in the Arduino and rescan ports", true);
    try { renderStatus(await api("/api/connect", { method: "POST", body: { port } })); toast(`Connected to ${port}`); }
    catch (e) { toast(e.message, true); }
  };
  $("btn-sim").onclick = async () => { renderStatus(await api("/api/simulate", { method: "POST" })); toast("Simulator started: fake presses every few seconds"); };
  $("btn-disconnect").onclick = async () => renderStatus(await api("/api/disconnect", { method: "POST" }));
  for (const id of ["set-repeats", "set-settle"]) {
    $(id).onchange = async () => {
      const body = { min_repeats: +$("set-repeats").value, settle_ms: +$("set-settle").value };
      try { renderStatus(await api("/api/settings", { method: "POST", body })); } catch (e) { toast(e.message, true); }
    };
  }

  // ---------- serial log ------------------------------------------------------
  const logEl = $("log");
  let logPaused = false, logLines = 0;
  const MAX_LOG = 1500;
  function fmtLogTime(t) {
    const d = new Date(t * 1000);
    return d.toLocaleTimeString([], { hour12: false }) + "." + String(d.getMilliseconds()).padStart(3, "0");
  }
  function appendLog(e) {
    if (e.kind === "frame" && !$("log-frames").checked) return;
    const div = document.createElement("div");
    div.className = "l-" + e.kind;
    div.innerHTML = `<span class="ts">${fmtLogTime(e.t)}</span> ${esc(e.line)}`;
    logEl.appendChild(div);
    logLines++;
    while (logEl.children.length > MAX_LOG) logEl.firstChild.remove();
    $("log-count").textContent = `(${logLines})`;
    if ($("log-scroll").checked) logEl.scrollTop = logEl.scrollHeight;
  }
  async function reloadLog() {
    logEl.innerHTML = ""; logLines = 0;
    const items = await api(`/api/log?limit=800&frames=${$("log-frames").checked}`);
    items.forEach(appendLog);
  }
  $("log-frames").onchange = () => reloadLog().catch(() => {});
  $("btn-log-pause").onclick = () => {
    logPaused = !logPaused;
    $("btn-log-pause").textContent = logPaused ? "Resume" : "Pause";
    if (!logPaused) reloadLog().catch(() => {});
  };
  $("btn-log-clear").onclick = async () => { await api("/api/log", { method: "DELETE" }); logEl.innerHTML = ""; logLines = 0; $("log-count").textContent = ""; };
  $("cmd-form").onsubmit = async (e) => {
    e.preventDefault();
    const cmd = $("cmd-input").value.trim();
    if (!cmd) return;
    try { await api("/api/command", { method: "POST", body: { command: cmd } }); $("cmd-input").value = ""; }
    catch (err) { toast(err.message, true); }
  };

  // ---------- SSE -------------------------------------------------------------
  function connectEvents() {
    const es = new EventSource("/api/events");
    es.addEventListener("status", (e) => renderStatus(JSON.parse(e.data)));
    es.addEventListener("frame", (e) => {
      const d = JSON.parse(e.data);
      $("st-frames").textContent = d.frames_seen;
      $("link-dot").classList.add("blink");
      clearTimeout(frameBlinkTimer);
      frameBlinkTimer = setTimeout(() => $("link-dot").classList.remove("blink"), 120);
    });
    es.addEventListener("capture", (e) => addCapture(JSON.parse(e.data)));
    es.addEventListener("log", (e) => { if (!logPaused) appendLog(JSON.parse(e.data)); });
    es.addEventListener("error", (e) => { if (e.data) toast(JSON.parse(e.data).message, true); });
    es.onerror = () => { es.close(); setTimeout(connectEvents, 2000); };
  }

  // ---------- captures feed ---------------------------------------------------
  function remoteOptions(selected) {
    return `<option value="">— choose remote —</option>` +
      remotes.map((r) => `<option value="${r.id}" ${r.id === selected ? "selected" : ""}>${esc(r.name)}</option>`).join("") +
      `<option value="__new">+ New remote…</option>`;
  }
  function addCapture(c) {
    $("feed-hint").classList.add("hidden");
    const raw = c.protocol === "raw";
    const card = document.createElement("div");
    card.className = "card" + (c.match ? " known" : "");
    card.dataset.id = c.id;
    const codes = raw
      ? `<dt>fingerprint</dt><dd>${esc(c.fingerprint)}</dd>
         <dt>edges</dt><dd>${c.timings.length}</dd>
         <dt>shortest pulse</dt><dd>${Math.min(...c.timings.slice(0, -1))} µs</dd>
         <dt>gap</dt><dd>${c.timings[c.timings.length - 1]} µs</dd>`
      : `<dt>code</dt><dd>${c.code} &nbsp; ${esc(c.code_hex)}</dd>
         <dt>binary</dt><dd>${esc(c.code_bin)}</dd>
         <dt>bits / pulse</dt><dd>${c.bit_length} bits, ${c.pulse_length} µs</dd>`;
    card.innerHTML = `
      <div class="card-head">
        <span class="badge ${raw ? "raw" : ""}">${raw ? "RAW timings" : esc(c.protocol)}</span>
        ${c.match ? `<span class="badge known" title="${c.match.method === "pattern" ? "recognised by its stable bits (" + esc(c.match.mask_hex) + ")" : "exact code match"}">${esc(c.match.remote_name)} / ${esc(c.match.button_name)}${c.match.method === "pattern" ? " ≈" : ""}</span>` : ""}
        <span class="muted">${c.repeats}× repeated${c.simulated ? " · simulated" : ""}</span>
        <span class="time">${fmtTime(c.captured_at)}</span>
      </div>
      ${waveSvg(c.timings, raw)}
      <dl class="codes">${codes}</dl>
      <details class="timings"><summary>timings (µs)</summary><pre>${c.timings.join(" ")}</pre></details>
      ${c.match ? "" : `
      <div class="save">
        <select class="sel-remote">${remoteOptions(lastRemoteId())}</select>
        <select class="sel-button"></select>
        <input class="btn-name" placeholder="Button name (e.g. Speed +)">
        <button class="primary do-save">Save</button>
      </div>`}`;
    feed.prepend(card);
    while (feed.children.length > 60) feed.lastChild.remove();
    if (c.match) return;

    const sel = card.querySelector(".sel-remote"), name = card.querySelector(".btn-name");
    const selBtn = card.querySelector(".sel-button");
    const fillButtons = () => {
      const rid = +sel.value || null;
      const mine = allButtons.filter((b) => b.remote_id === rid && b.protocol === c.protocol && b.bit_length === c.bit_length);
      selBtn.innerHTML = `<option value="__new">New button…</option>` +
        mine.map((b) => `<option value="${b.id}">same as: ${esc(b.name)} (${b.sample_count || 1} sample${(b.sample_count || 1) === 1 ? "" : "s"})</option>`).join("");
      selBtn.classList.toggle("hidden", mine.length === 0);
      name.classList.toggle("hidden", selBtn.value !== "__new");
    };
    selBtn.onchange = () => name.classList.toggle("hidden", selBtn.value !== "__new");
    fillButtons();
    sel.onchange = async () => {
      fillButtons();
      if (sel.value !== "__new") return;
      const n = prompt("Name for the new remote:");
      if (!n) { sel.value = ""; return; }
      try {
        const r = await api("/api/remotes", { method: "POST", body: { name: n } });
        await loadLibrary();
        document.querySelectorAll(".sel-remote").forEach((s) => { const v = s.value; s.innerHTML = remoteOptions(+v || null); });
        sel.value = r.id;
      } catch (e) { toast(e.message, true); sel.value = ""; }
    };
    const save = async () => {
      if (!sel.value || sel.value === "__new") return toast("Pick a remote first", true);
      const existing = selBtn.value !== "__new" ? +selBtn.value : null;
      if (!existing && !name.value.trim()) return toast("Give the button a name", true);
      try {
        let b;
        if (existing) {
          const body = c.id != null ? { capture_id: c.id } : { timings: c.timings };
          b = await api(`/api/buttons/${existing}/samples`, { method: "POST", body });
          const learned = b.stable_bits != null ? ` · learned ${b.stable_bits}/${b.bit_length} stable bits` : "";
          toast(`Added sample ${b.sample_count} to ${b.remote_name} / ${b.name}${learned}`);
        } else {
          const body = { remote_id: +sel.value, name: name.value };
          if (c.id != null) body.capture_id = c.id; else { body.timings = c.timings; body.repeats = c.repeats; }
          b = await api("/api/buttons", { method: "POST", body });
          toast(`Saved ${b.remote_name} / ${b.name}`);
        }
        try { localStorage.setItem("lastRemoteId", sel.value); } catch (_) {}
        card.classList.add("known");
        card.querySelector(".save").outerHTML = "";
        card.querySelector(".badge").insertAdjacentHTML("afterend", `<span class="badge known">${esc(b.remote_name)} / ${esc(b.name)}</span>`);
        loadLibrary();
      } catch (e) { toast(e.message, true); }
    };
    card.querySelector(".do-save").onclick = save;
    name.onkeydown = (e) => { if (e.key === "Enter") save(); };
  }
  function lastRemoteId() { try { return +localStorage.getItem("lastRemoteId") || null; } catch (_) { return null; } }
  $("btn-clear-feed").onclick = () => { feed.innerHTML = ""; $("feed-hint").classList.remove("hidden"); };

  // ---------- library ---------------------------------------------------------
  async function loadLibrary() {
    remotes = await api("/api/remotes");
    const buttons = await api("/api/buttons");
    allButtons = buttons;
    const open = new Set([...library.querySelectorAll("details[open]")].map((d) => d.dataset.id));
    library.innerHTML = remotes.length ? "" : `<p class="empty">No remotes yet. Add one above, then save captured presses to it.</p>`;
    for (const r of remotes) {
      const bs = buttons.filter((b) => b.remote_id === r.id);
      const det = document.createElement("details");
      det.className = "remote"; det.dataset.id = r.id;
      if (open.has(String(r.id)) || remotes.length === 1) det.open = true;
      det.innerHTML = `
        <summary>
          <span class="name">${esc(r.name)}</span>
          <span class="meta">${esc([r.brand, r.model, r.technology].filter(Boolean).join(" · "))}</span>
          <span class="count">${bs.length} button${bs.length === 1 ? "" : "s"}</span>
        </summary>
        ${bs.length ? `<table>
          <tr><th>Button</th><th>Protocol</th><th>Code</th><th>Bits</th><th>Pulse</th><th>Samples</th><th></th></tr>
          ${bs.map((b) => `<tr data-bid="${b.id}">
            <td>${esc(b.name)}</td>
            <td class="mono">${esc(b.protocol)}</td>
            <td class="mono">${b.code != null ? `${b.code} (0x${b.code.toString(16).toUpperCase()})` : esc(b.fingerprint)}</td>
            <td class="mono">${b.bit_length ?? b.timings.length + " edges"}</td>
            <td class="mono">${b.pulse_length != null ? b.pulse_length + " µs" : "—"}</td>
            <td class="mono" title="presses taught to this button; 2 or more enable pattern matching">${b.sample_count || 1}</td>
            <td class="actions">
              <button class="ghost small rename">Rename</button>
              <button class="ghost small danger del-btn">Delete</button>
            </td></tr>`).join("")}
        </table>` : `<p class="empty">No buttons saved yet.</p>`}
        <div class="foot"><button class="ghost small danger del-remote">Delete remote</button></div>`;
      det.querySelectorAll(".rename").forEach((btn) => btn.onclick = async (e) => {
        const bid = +e.target.closest("tr").dataset.bid;
        const cur = bs.find((b) => b.id === bid);
        const n = prompt("New button name:", cur.name);
        if (!n || n === cur.name) return;
        try { await api(`/api/buttons/${bid}`, { method: "PATCH", body: { name: n } }); loadLibrary(); } catch (err) { toast(err.message, true); }
      });
      det.querySelectorAll(".del-btn").forEach((btn) => btn.onclick = async (e) => {
        const bid = +e.target.closest("tr").dataset.bid;
        const cur = bs.find((b) => b.id === bid);
        if (!confirm(`Delete button "${cur.name}"?`)) return;
        await api(`/api/buttons/${bid}`, { method: "DELETE" }); loadLibrary();
      });
      det.querySelector(".del-remote").onclick = async () => {
        if (!confirm(`Delete remote "${r.name}" and its ${bs.length} button(s)?`)) return;
        await api(`/api/remotes/${r.id}`, { method: "DELETE" }); loadLibrary();
      };
      library.appendChild(det);
    }
    document.querySelectorAll(".sel-remote").forEach((s) => { const v = s.value; s.innerHTML = remoteOptions(+v || null); });
  }
  $("remote-form").onsubmit = async (e) => {
    e.preventDefault();
    try {
      await api("/api/remotes", { method: "POST", body: {
        name: $("remote-name").value, brand: $("remote-brand").value, model: $("remote-model").value } });
      $("remote-form").reset(); loadLibrary(); toast("Remote added");
    } catch (err) { toast(err.message, true); }
  };

  // ---------- boot ------------------------------------------------------------
  (async () => {
    try {
      await Promise.all([loadPorts(), loadLibrary()]);
      renderStatus(await api("/api/status"));
      // show recent captures from the DB so a page reload does not lose them
      const recent = await api("/api/captures?limit=20");
      recent.reverse().forEach((c) => addCapture({
        ...c, code_hex: c.code != null ? "0x" + c.code.toString(16).toUpperCase() : null,
        code_bin: c.code != null && c.bit_length ? c.code.toString(2).padStart(c.bit_length, "0") : null,
        match: c.button_id ? { button_id: c.button_id, button_name: c.button_name, remote_name: c.remote_name } : null,
      }));
      await reloadLog();
    } catch (e) { toast(e.message, true); }
    connectEvents();
  })();
})();
