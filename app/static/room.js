(function () {
  const roomId = decodeURIComponent(location.pathname.split("/").pop());
  const $ = (id) => document.getElementById(id);

  // Токен мастера мог прийти в ссылке: /r/ID#admin=TOKEN
  const hashAdmin = new URLSearchParams(location.hash.slice(1)).get("admin");
  if (hashAdmin) {
    Store.saveRoom({ id: roomId, admin: hashAdmin });
    history.replaceState(null, "", location.pathname);
  }
  const adminToken = () => (Store.room(roomId) || {}).admin || "";
  const meKey = `dh:me:${roomId}`;
  let me = Store.get(meKey, null);

  let ws = null;
  let retry = 0;
  let state = { players: [], busy: false };
  let roomName = "";
  let lastId = 0;

  // ---------- соединение ----------
  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws/${encodeURIComponent(roomId)}`);
    setConn("подключение…", false);
    ws.onopen = () => {
      retry = 0;
      setConn("онлайн", false);
      ws.send(JSON.stringify({ type: "join", player: me.player, character: me.character }));
    };
    ws.onmessage = (ev) => handle(JSON.parse(ev.data));
    ws.onclose = (ev) => {
      if (ev.code === 4404) { setConn("комната не найдена", true); return; }
      setConn("нет связи, переподключаюсь…", true);
      retry = Math.min(retry + 1, 6);
      setTimeout(connect, 500 * 2 ** retry);
    };
  }
  setInterval(() => { if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "ping" })); }, 25000);

  function send(obj) {
    if (!ws || ws.readyState !== 1) { toast("Нет связи с сервером, сообщение не отправлено."); return false; }
    ws.send(JSON.stringify(obj));
    return true;
  }

  function setConn(text, bad) {
    const el = $("conn");
    el.textContent = text;
    el.classList.toggle("bad", bad);
  }

  function handle(msg) {
    switch (msg.type) {
      case "welcome":
        setRoomName(msg.room.name);
        $("messages").innerHTML = "";
        lastId = 0;
        msg.messages.forEach(addMessage);
        if (!msg.messages.length) showEmptyHint();
        scrollDown(true);
        break;
      case "message":
        $("empty-hint")?.remove();
        addMessage(msg.message);
        scrollDown(msg.message.author === me.player);
        break;
      case "state":
        state = msg;
        renderPlayers();
        $("typing").classList.toggle("on", msg.busy);
        if (msg.busy) scrollDown(false);
        break;
      case "room":
        setRoomName(msg.room.name);
        break;
      case "notice":
        toast(msg.text, 12000);
        break;
      case "error":
        toast(msg.text);
        break;
    }
  }

  // ---------- отрисовка ----------
  function setRoomName(name) {
    roomName = name || roomName;
    $("room-title").textContent = roomName;
    document.title = `${roomName} — DungeonHub`;
    Store.saveRoom({ id: roomId, name: roomName });
  }

  function showEmptyHint() {
    const div = document.createElement("div");
    div.className = "empty-hint";
    div.id = "empty-hint";
    div.innerHTML = adminToken()
      ? "Комната готова. Откройте <b>⚙ Настройки</b>, опишите мир и стартовую ситуацию, пригласите друзей и нажмите <b>⏭ Ход мастера</b>, чтобы начать игру."
      : "Игра ещё не началась. Когда все соберутся, мастер (или любой игрок) нажмёт <b>⏭ Ход мастера</b>.";
    $("messages").appendChild(div);
  }

  function addMessage(m) {
    if (m.id && m.id <= lastId) return;
    lastId = Math.max(lastId, m.id || 0);
    const el = document.createElement("div");
    el.className = `msg ${m.kind}`;
    const time = new Date(m.created_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    if (m.kind === "master") {
      el.innerHTML = `<div class="head">Мастер · ${time}</div>` + renderMarkdown(m.content);
    } else if (m.kind === "action") {
      if (me && m.author === me.player) el.classList.add("mine");
      el.innerHTML = `<div class="head"><b>${esc(m.character || m.author)}</b> · ${esc(m.author)} · ${time}</div>` +
        `<div>${esc(m.content).replace(/\n/g, "<br>")}</div>`;
    } else if (m.kind === "roll") {
      const html = esc(m.content).replace(/= (-?\d+)/, '= <span class="total">$1</span>');
      el.innerHTML = `🎲 <b>${esc(m.character || m.author)}</b>: ${html}`;
    } else if (m.kind === "ooc") {
      el.innerHTML = `${esc(m.author)}: ${esc(m.content)}`;
    } else {
      el.textContent = m.content;
    }
    $("messages").appendChild(el);
  }

  function scrollDown(force) {
    const feed = $("feed");
    const nearBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 200;
    if (force || nearBottom) requestAnimationFrame(() => { feed.scrollTop = feed.scrollHeight; });
  }

  function renderPlayers() {
    const box = $("players");
    box.innerHTML = "";
    for (const p of state.players) {
      const div = document.createElement("div");
      div.className = "player";
      div.innerHTML = `<div class="char"><span class="dot ${p.online ? "on" : ""}"></span>${esc(p.character)}</div>` +
        `<div class="who">${esc(p.player)}${p.player === me.player ? " (вы)" : ""}</div>` +
        (p.open ? `<div class="badge-open">✎ пишет заявку…</div>` : "");
      div.onclick = () => (p.player === me.player ? openSheet() : viewSheet(p));
      box.appendChild(div);
    }
    const mine = state.players.find(p => p.player === me.player);
    $("open-flag").classList.toggle("on", !!(mine && mine.open));
  }

  // ---------- ввод ----------
  const input = $("input");
  function autosize() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight + 2, 220) + "px";
  }
  input.addEventListener("input", autosize);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); }
  });
  $("send-btn").onclick = submit;

  function submit() {
    const text = input.value.trim();
    if (!text) return;
    if (send({ type: "say", text })) {
      input.value = "";
      autosize();
    }
    input.focus();
  }

  $("dice-btn").onclick = () => {
    if (!input.value.trim()) input.value = "/roll 1d20+";
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  };
  $("turn-btn").onclick = () => {
    if (state.busy) { toast("Мастер уже пишет ответ."); return; }
    send({ type: "turn" });
  };
  $("help-btn").onclick = () => send({ type: "say", text: "/help" });
  $("menu-btn").onclick = () => $("sidebar").classList.toggle("open");

  // ---------- вход ----------
  function askJoin() {
    const dlg = $("join-dlg");
    $("join-room-name").textContent = roomName ? `«${roomName}»` : "";
    $("j-player").value = Store.get("dh:lastPlayer", "");
    dlg.addEventListener("cancel", (e) => e.preventDefault());
    $("join-form").onsubmit = (e) => {
      const player = $("j-player").value.trim();
      const character = $("j-char").value.trim();
      if (!player || !character) { e.preventDefault(); $("join-err").textContent = "Заполните оба поля"; return; }
      me = { player, character };
      Store.set(meKey, me);
      Store.set("dh:lastPlayer", player);
      connect();
    };
    dlg.showModal();
  }

  // ---------- персонаж ----------
  function openSheet() {
    const mine = state.players.find(p => p.player === me.player) || {};
    $("s-char").value = me.character;
    $("s-sheet").value = mine.sheet || "";
    $("sheet-dlg").showModal();
  }
  $("sheet-btn").onclick = openSheet;
  $("sheet-form").addEventListener("submit", (e) => {
    if (e.submitter?.value !== "save") return;
    const character = $("s-char").value.trim();
    if (character && character !== me.character) {
      me.character = character;
      Store.set(meKey, me);
      send({ type: "join", player: me.player, character });
    }
    send({ type: "sheet", sheet: $("s-sheet").value });
  });

  function viewSheet(p) {
    $("v-char").textContent = p.character;
    $("v-player").textContent = `Игрок: ${p.player}`;
    $("v-sheet").textContent = p.sheet || "Игрок пока ничего не написал о персонаже.";
    $("view-dlg").showModal();
  }

  // ---------- приглашение ----------
  $("invite-btn").onclick = () => {
    $("invite-link").value = `${location.origin}/r/${roomId}`;
    const token = adminToken();
    $("admin-link-wrap").hidden = !token;
    if (token) $("admin-link").value = `${location.origin}/r/${roomId}#admin=${token}`;
    $("invite-dlg").showModal();
  };
  document.querySelectorAll("[data-copy]").forEach(btn => {
    btn.onclick = async () => {
      const el = $(btn.dataset.copy);
      el.select();
      try { await navigator.clipboard.writeText(el.value); btn.textContent = "Скопировано"; }
      catch (e) { document.execCommand("copy"); btn.textContent = "Скопировано"; }
      setTimeout(() => (btn.textContent = "Копировать"), 1500);
    };
  });

  // ---------- настройки мастера ----------
  const fields = {
    world: "st-world", situation: "st-situation", plot: "st-plot", style: "st-style",
    length: "st-length", mode: "st-mode", rules: "st-rules", batch_seconds: "st-batch",
    open_timeout_minutes: "st-timeout", temperature: "st-temp", model: "st-model",
  };

  async function adminFetch(method, body) {
    const res = await fetch(`/api/rooms/${encodeURIComponent(roomId)}/admin`, {
      method,
      headers: { "Content-Type": "application/json", "X-Admin-Token": adminToken() },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Ошибка сервера");
    return data;
  }

  async function openSettings() {
    try {
      const data = await adminFetch("GET");
      $("st-name").value = data.name;
      for (const [key, id] of Object.entries(fields)) $(id).value = data.settings[key] ?? "";
      $("st-chronicle").value = data.chronicle || "";
      $("model-hint").textContent = `— пусто = ${data.default_model}`;
      $("rules-wrap").hidden = $("st-mode").value !== "mechanics";
      $("settings-err").textContent = "";
      $("settings-dlg").showModal();
    } catch (e) { toast(e.message); }
  }
  $("st-mode").onchange = () => { $("rules-wrap").hidden = $("st-mode").value !== "mechanics"; };
  $("settings-btn").onclick = openSettings;
  $("settings-form").addEventListener("submit", async (e) => {
    if (e.submitter?.value !== "save") return;
    e.preventDefault();
    const settings = {};
    for (const [key, id] of Object.entries(fields)) settings[key] = $(id).value;
    try {
      await adminFetch("PUT", { name: $("st-name").value, settings, chronicle: $("st-chronicle").value });
      $("settings-dlg").close();
      toast("Настройки сохранены. Мастер учтёт их со следующего ответа.", 3000);
    } catch (err) { $("settings-err").textContent = err.message; }
  });

  // ---------- старт ----------
  async function start() {
    try {
      const res = await fetch(`/api/rooms/${encodeURIComponent(roomId)}`);
      if (!res.ok) {
        $("messages").innerHTML = `<div class="empty-hint">Комната не найдена. Проверьте ссылку или <a href="/">создайте новую</a>.</div>`;
        setConn("", false);
        return;
      }
      setRoomName((await res.json()).name);
    } catch (e) { /* попробуем подключиться всё равно */ }

    $("settings-btn").hidden = !adminToken();
    if (me && me.player && me.character) connect();
    else askJoin();
    if (new URLSearchParams(location.search).get("new") && adminToken()) {
      history.replaceState(null, "", location.pathname);
    }
  }
  start();
})();
