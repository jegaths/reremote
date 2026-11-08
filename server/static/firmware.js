/* Firmware tab: sketch list, file editor, compile / flash with streamed output. */
(() => {
  const { api, toast, esc } = window.rc;
  const $ = (id) => document.getElementById(id);
  const editor = $("fw-editor"), gutter = $("fw-gutter"), out = $("fw-output");
  let sketches = [], boards = {}, current = null, currentFile = null, dirty = false, building = false;
  let lastPorts = [], bridge = null;
  const isOta = (p) => (p || "").startsWith("ota:");

  // ---------- sketches -------------------------------------------------------
  async function loadSketches() {
    const d = await api("/api/firmware");
    sketches = d.sketches; boards = d.boards; building = !!d.building;
    if (!d.arduino_cli) $("fw-hint").textContent = "arduino-cli not found: run 'make setup' once.";
    renderList();
    if (!current && sketches.length) selectSketch(sketches[0].name);
    updateButtons();
  }
  function renderList() {
    $("fw-list").innerHTML = sketches.map((s) => `
      <div class="fw-item ${current === s.name ? "active" : ""}" data-name="${esc(s.name)}">
        <div class="t">${esc(s.title)} <span class="muted">· ${esc(s.name)}</span></div>
        <div class="d">${esc(s.description)}</div>
        <div class="b">boards: ${s.boards.map((b) => boards[b] || b).join(", ")}${s.libraries.length ? " · libs: " + s.libraries.join(", ") : ""}</div>
      </div>`).join("") || `<p class="empty">No sketches found in firmware/.</p>`;
    $("fw-list").querySelectorAll(".fw-item").forEach((el) => el.onclick = () => selectSketch(el.dataset.name));
  }
  async function selectSketch(name) {
    if (dirty && !confirm("Discard unsaved changes?")) return;
    current = name; dirty = false; currentFile = null;
    renderList(); renderFiles(); renderPorts(); fillBoards();
    const s = sketches.find((x) => x.name === name);
    if (s && s.files.length) openFile(s.files[0]);
    updateButtons();
  }
  function renderFiles() {
    const s = sketches.find((x) => x.name === current);
    $("fw-files").innerHTML = (s ? s.files : []).map((f) =>
      `<button class="${f === currentFile ? "active" : ""} ${(s.generated || []).includes(f) ? "gen" : ""}" data-f="${esc(f)}" title="${(s.generated || []).includes(f) ? "generated before every build; edits are overwritten" : ""}">${esc(f)}</button>`).join("");
    $("fw-files").querySelectorAll("button").forEach((b) => b.onclick = () => openFile(b.dataset.f));
  }
  async function openFile(f) {
    if (dirty && !confirm("Discard unsaved changes?")) return;
    try {
      const res = await fetch(`/api/firmware/${current}/files/${encodeURIComponent(f)}`);
      if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
      editor.value = await res.text();
      currentFile = f; dirty = false;
      const s = sketches.find((x) => x.name === current);
      editor.disabled = (s.generated || []).includes(f);
      renderFiles(); renderGutter(); updateButtons();
    } catch (e) { toast(e.message, true); }
  }
  function renderGutter() {
    const n = editor.value.split("\n").length;
    gutter.textContent = Array.from({ length: n }, (_, i) => i + 1).join("\n");
    gutter.scrollTop = editor.scrollTop;
  }
  editor.addEventListener("input", () => { dirty = true; renderGutter(); updateButtons(); });
  editor.addEventListener("scroll", () => { gutter.scrollTop = editor.scrollTop; });
  editor.addEventListener("keydown", (e) => {
    if (e.key === "Tab") {
      e.preventDefault();
      const s = editor.selectionStart, t = editor.selectionEnd;
      editor.setRangeText("  ", s, t, "end"); dirty = true; renderGutter(); updateButtons();
    }
    if ((e.metaKey || e.ctrlKey) && e.key === "s") { e.preventDefault(); saveFile(); }
  });
  async function saveFile() {
    if (!current || !currentFile || !dirty) return;
    try {
      await api(`/api/firmware/${current}/files/${encodeURIComponent(currentFile)}`, { method: "PUT", body: { content: editor.value } });
      dirty = false; updateButtons(); toast(`Saved ${currentFile}`);
    } catch (e) { toast(e.message, true); }
  }
  $("fw-save").onclick = saveFile;
  $("fw-add-file").onclick = async () => {
    if (!current) return;
    const f = prompt("File name (e.g. helpers.h):");
    if (!f) return;
    try {
      const r = await api(`/api/firmware/${current}/files/${encodeURIComponent(f)}`, { method: "PUT", body: { content: "" } });
      sketches.find((x) => x.name === current).files = r.files;
      renderFiles(); openFile(f);
    } catch (e) { toast(e.message, true); }
  };
  $("fw-new").onclick = async () => {
    const name = prompt("Sketch name (letters, digits, underscore):");
    if (!name) return;
    const title = prompt("Short title:", name) || name;
    const copyFrom = confirm("Start from a copy of the RF capture sketch? (Cancel = empty sketch)") ? "rf_capture" : null;
    try {
      const s = await api("/api/firmware", { method: "POST", body: { name, title, copy_from: copyFrom } });
      await loadSketches(); selectSketch(s.name);
    } catch (e) { toast(e.message, true); }
  };

  // ---------- target ----------------------------------------------------------
  async function loadPorts() {
    const [ports, br] = await Promise.all([api("/api/ports"), api("/api/bridge/status").catch(() => null)]);
    lastPorts = ports; bridge = br;
    renderPorts();
  }
  function renderPorts() {
    const sel = $("fw-port"); const cur = sel.value;
    const s = sketches.find((x) => x.name === current);
    const opts = lastPorts.map((p) => `<option value="${esc(p.device)}" data-board="${p.board}">${esc(p.device)} — ${esc(p.description)}</option>`);
    if (s && s.ota && bridge && bridge.online && bridge.ota) {
      opts.push(`<option value="${esc(bridge.ota_port)}" data-board="${esc(bridge.board || "esp32s3")}">WiFi: ${esc(bridge.host)} — ha_bridge ${esc(bridge.version)} at ${esc(bridge.ip)}</option>`);
    }
    sel.innerHTML = opts.join("") || `<option value="">No board detected</option>`;
    if (cur && [...sel.options].some((o) => o.value === cur)) sel.value = cur;
    updateButtons();
  }
  // The board is always the user's explicit choice: never guessed, never reset by
  // the periodic port refresh. Starts empty; a build without one asks for it.
  function fillBoards() {
    const s = sketches.find((x) => x.name === current);
    const allowed = s ? s.boards : Object.keys(boards);
    const sel = $("fw-board"); const cur = sel.value;
    sel.innerHTML = `<option value="">Select board…</option>` +
      allowed.map((b) => `<option value="${b}">${esc(boards[b] || b)}</option>`).join("");
    sel.value = allowed.includes(cur) ? cur : "";
  }
  $("fw-port").onchange = updateButtons;
  $("fw-board").onchange = () => { $("fw-board").classList.remove("invalid"); updateButtons(); };
  function requireBoard() {
    const sel = $("fw-board");
    if (sel.value) return true;
    sel.classList.add("invalid");
    sel.focus();
    try { sel.showPicker(); } catch (_) { /* not supported everywhere */ }
    toast("Select your board first (Firmware tab → Board)", true);
    return false;
  }
  function updateButtons() {
    $("fw-save").disabled = !dirty;
    $("fw-save").classList.toggle("dirty", dirty);
    const s = sketches.find((x) => x.name === current);
    const port = $("fw-port").value;
    $("fw-compile").disabled = building || !s;
    $("fw-flash").disabled = building || !s || !port;
    if (building) $("fw-hint").textContent = "Build running…";
    else if (!port) $("fw-hint").textContent = "Plug the board in and it appears here. Flashing frees the serial port and reconnects it afterwards. A bridge that is online shows up as a WiFi target.";
    else if (isOta(port)) $("fw-hint").textContent = "Compiles here and pushes the binary to the bridge over WiFi; it reboots into the new firmware. config.h is regenerated first.";
    else $("fw-hint").textContent = s && s.config ? "config.h is regenerated from the Home Assistant tab before each build." : "";
  }

  // ---------- builds ----------------------------------------------------------
  async function build(action) {
    if (dirty) { await saveFile(); }
    const body = { sketch: current, board: $("fw-board").value, action, port: $("fw-port").value || null };
    try {
      out.innerHTML = ""; building = true; updateButtons();
      await api("/api/firmware/build", { method: "POST", body });
      toast(action === "flash" ? "Flashing… watch the build output" : "Compiling…");
    } catch (e) { building = false; updateButtons(); toast(e.message, true); }
  }
  $("fw-compile").onclick = () => { if (requireBoard()) build("compile"); };
  $("fw-flash").onclick = () => {
    if (!requireBoard()) return;
    const s = sketches.find((x) => x.name === current);
    const port = $("fw-port").value;
    const board = $("fw-board").value;
    let where = `${port} as ${boards[board]}`;
    if (isOta(port)) {
      where = `the bridge at ${port.slice(4)} over WiFi as ${boards[board]}`;
      // the bridge reports its own chip; a mismatch would push a binary it cannot run
      if (bridge && bridge.board && bridge.board !== board)
        where += `\n\nWarning: the bridge reports it is an ${boards[bridge.board] || bridge.board}. Flashing a build for another chip will fail.`;
    }
    if (!confirm(`Flash "${s.title}" to ${where}?`)) return;
    build("flash");
  };
  $("fw-clear-out").onclick = () => { out.innerHTML = ""; };
  window.rc.onEvent("build", (ev) => {
    if (ev.done) {
      building = false; updateButtons();
      toast(ev.ok ? "Build finished OK" : "Build failed, see output", !ev.ok);
      return;
    }
    const div = document.createElement("div");
    div.className = ev.level || (ev.line.startsWith("$ ") ? "cmd" : "");
    div.textContent = ev.line;
    out.appendChild(div);
    while (out.children.length > 800) out.firstChild.remove();
    out.scrollTop = out.scrollHeight;
    // the page may be on another tab; make sure the Firmware tab is visible when flashing starts
    if (ev.line.startsWith("$ arduino-cli upload") || ev.line.startsWith("$ ota upload")) document.querySelector('.tab[data-tab="firmware"]').click();
  });
  window.rc.onEvent("status", () => loadPorts().catch(() => {}));

  // start the bridge flash from the HA tab
  window.rc.flashSketch = async (name, port) => {
    document.querySelector('.tab[data-tab="firmware"]').click();
    if (!sketches.length) await loadSketches();
    await selectSketch(name);
    await loadPorts();
    const sel = $("fw-port");
    if (port) {
      if (![...sel.options].some((o) => o.value === port)) return toast(`${port.replace(/^ota:/, "")} is not answering; cannot update over WiFi`, true);
      sel.value = port; updateButtons();
    } else {
      const usb = lastPorts.find((p) => (p.board || "").startsWith("esp32")) || lastPorts[0];
      if (!usb) return toast("No board detected: plug the ESP32 in first", true);
      sel.value = usb.device; updateButtons();
    }
    $("fw-flash").click();
  };

  loadSketches().then(loadPorts).catch((e) => toast(e.message, true));
  setInterval(() => { if (!building) loadPorts().catch(() => {}); }, 5000);
})();
