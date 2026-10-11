(async function () {
  const err = document.getElementById("err");

  try {
    const cfg = await fetch("/api/config").then(r => r.json());
    if (cfg.needs_password) document.getElementById("pw-wrap").hidden = false;
  } catch (e) { /* не критично */ }

  // Список комнат
  const list = document.getElementById("rooms");
  const rooms = Store.rooms();
  document.getElementById("no-rooms").hidden = rooms.length > 0;
  for (const r of rooms) {
    const li = document.createElement("li");
    li.innerHTML = `<a href="/r/${encodeURIComponent(r.id)}">${esc(r.name || r.id)}</a>` +
      (r.admin ? `<span class="tag">мастер</span>` : "") +
      `<button class="btn ghost" title="Убрать из списка">✕</button>`;
    li.querySelector("button").onclick = () => {
      if (r.admin && !confirm("Вы создатель этой комнаты. Если убрать её из списка, права мастера на этом устройстве пропадут (если не сохранили ссылку мастера). Убрать?")) return;
      Store.forget(r.id); li.remove();
    };
    list.appendChild(li);
  }

  document.getElementById("create").onsubmit = async (e) => {
    e.preventDefault();
    err.textContent = "";
    const res = await fetch("/api/rooms", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: document.getElementById("name").value,
        password: document.getElementById("pw").value,
      }),
    });
    const data = await res.json();
    if (!res.ok) { err.textContent = data.error || "Не удалось создать комнату"; return; }
    Store.saveRoom({ id: data.id, name: data.name, admin: data.admin_token });
    location.href = `/r/${encodeURIComponent(data.id)}?new=1`;
  };

  document.getElementById("join").onsubmit = (e) => {
    e.preventDefault();
    const v = document.getElementById("link").value.trim();
    const m = v.match(/\/r\/([\w-]+)/) || v.match(/^([\w-]+)$/);
    if (m) location.href = `/r/${m[1]}` + (v.includes("#") ? v.slice(v.indexOf("#")) : "");
  };
})();
