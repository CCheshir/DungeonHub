// Общие помощники: хранилище в браузере и экранирование.
const Store = {
  get(key, fallback) {
    try {
      const v = localStorage.getItem(key);
      return v === null ? fallback : JSON.parse(v);
    } catch (e) { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* приватный режим */ }
  },
  rooms() { return Store.get("dh:rooms", []); },
  saveRoom(room) {
    const list = Store.rooms().filter(r => r.id !== room.id);
    const prev = Store.rooms().find(r => r.id === room.id) || {};
    list.unshift({ ...prev, ...room, visited: Date.now() });
    Store.set("dh:rooms", list.slice(0, 30));
  },
  room(id) { return Store.rooms().find(r => r.id === id) || null; },
  forget(id) { Store.set("dh:rooms", Store.rooms().filter(r => r.id !== id)); },
};

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Мини-markdown: **жирный**, *курсив*, > цитата, абзацы.
function renderMarkdown(text) {
  const inline = s => esc(s)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*(?!\s)(.+?)\*(?!\*)/g, "$1<em>$2</em>");
  return String(text || "").trim().split(/\n{2,}/).map(block => {
    const lines = block.split("\n");
    if (lines.every(l => l.startsWith(">"))) {
      return "<blockquote>" + lines.map(l => inline(l.replace(/^>\s?/, ""))).join("<br>") + "</blockquote>";
    }
    return "<p>" + lines.map(inline).join("<br>") + "</p>";
  }).join("");
}

function toast(text, ms = 6000) {
  const el = document.createElement("div");
  el.className = "toast";
  el.innerHTML = renderMarkdown(text).replace(/<\/?p>/g, "");
  document.body.appendChild(el);
  setTimeout(() => el.remove(), ms);
  el.addEventListener("click", () => el.remove());
}
