(() => {
  "use strict";

  let SOURCES = {};
  let settings = {};
  let currentSource = "ytmusic";
  let currentMode = "playlist";
  let downloading = false;
  let cardCount = 0;
  let appVersion = "";

  const $ = (id) => document.getElementById(id);

  // Папка назначения: при включённом экспорте показываем понятный
  // пользовательский путь (системные Загрузки), приватную папку не светим.
  let destPrivateDir = "";
  function applyDestText(privateDir) {
    if (privateDir !== undefined) destPrivateDir = privateDir;
    const input = $("target-dir-input");
    const hint = $("dir-hint");
    if (settings.export_to_downloads !== false) {
      input.value = "Загрузки/ShipTones";
      if (hint) hint.textContent =
        "Каждый скачанный трек сразу копируется в системные «Загрузки» — видно в файловом менеджере. Служебная копия остаётся во внутренней памяти (нужна для истории).";
    } else {
      input.value = destPrivateDir || "внутренняя папка приложения";
      if (hint) hint.textContent =
        "Файлы сохраняются во внутренней папке приложения — она не видна в файловом менеджере";
    }
  }

  const asyncApi = (name, ...args) => {
    if (typeof window.ShipTones === "undefined" || !window.ShipTones[name]) {
      return Promise.reject(new Error("ShipTones bridge is not ready"));
    }
    const raw = window.ShipTones[name](...args);
    try {
      return Promise.resolve(JSON.parse(raw || "{}"));
    } catch (e) {
      return Promise.resolve(raw);
    }
  };

  // ---------------- THEME ----------------
  function applyTheme(mode) {
    document.documentElement.setAttribute("data-theme", mode);
    try { localStorage.setItem("shiptones-theme", mode); } catch (e) {}
  }
  $("theme-toggle").addEventListener("click", () => {
    const cur = document.documentElement.getAttribute("data-theme");
    applyTheme(cur === "dark" ? "light" : "dark");
  });

  // ---------------- FORMAT & QUALITY LINKING ----------------
  const FORMAT_QUALITY_MAP = {
    "aac": { default: "256", options: ["128", "192", "256", "320"] },
    "mp3": { default: "320", options: ["128", "192", "256", "320"] },
    "flac": { default: "0", options: ["0"] },
    "opus": { default: "160", options: ["96", "128", "160", "192"] }
  };

  function updateQualityOptions(format) {
    const qualitySelect = $("quality-select");
    const config = FORMAT_QUALITY_MAP[format] || FORMAT_QUALITY_MAP["aac"];
    const currentValue = qualitySelect.value;
    let newValue = config.options.includes(currentValue) ? currentValue : config.default;
    qualitySelect.innerHTML = "";
    config.options.forEach(q => {
      const opt = document.createElement("option");
      opt.value = q;
      opt.textContent = q === "0" ? "Lossless" : q;
      qualitySelect.appendChild(opt);
    });
    qualitySelect.value = newValue;
  }

  $("format-select").addEventListener("change", (e) => {
    updateQualityOptions(e.target.value);
    settings.format = e.target.value;
    asyncApi("settingsSet", JSON.stringify(settings));
  });

  // ---------------- NAVIGATION ----------------
  const VIEW_META = {
    download: ["Загрузка музыки", "Вставь ссылку на трек или плейлист"],
    history: ["История загрузок", "Всё, что уже скачано этим приложением"],
    playlists: ["Плейлисты", "Сохранённые источники для повторной синхронизации"],
    settings: ["Настройки", "Поведение загрузчика и обработки файлов"],
  };
  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".nav-item").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const view = btn.dataset.view;
      document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
      $(`view-${view}`).classList.add("active");
      const [title, sub] = VIEW_META[view];
      $("view-title").textContent = title;
      $("view-subtitle").textContent = sub;
      if (view === "history") loadHistory();
      if (view === "playlists") loadPlaylists();
    });
  });

  // ---------------- TOAST ----------------
  function toast(msg, kind = "default") {
    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    el.textContent = msg;
    $("toast-stack").appendChild(el);
    setTimeout(() => el.remove(), 4000);
  }

  // ---------------- SOURCES ----------------
  function renderSources() {
    const wrap = $("source-picker");
    wrap.innerHTML = "";
    Object.entries(SOURCES).forEach(([key, src]) => {
      const btn = document.createElement("button");
      btn.className = "source-btn" + (key === currentSource ? " active" : "");
      btn.style.borderColor = key === currentSource ? src.color : "";
      if (key === currentSource) btn.style.background = src.color;
      btn.style.color = key === currentSource ? src.fg : "";
      const devTag = src.in_dev ? ' <small class="dev-tag">в разработке</small>' : "";
      btn.innerHTML = `<span class="src-row"><span class="dot" style="background:${key === currentSource ? src.fg : src.color}"></span>${src.name}</span>${devTag}`;
      btn.addEventListener("click", () => {
        currentSource = key;
        renderSources();
        if (src.in_dev) toast("Этот источник ещё в разработке", "default");
      });
      wrap.appendChild(btn);
    });
  }

  // ---------------- MODE SEGMENTED ----------------
  document.querySelectorAll("#mode-segmented button").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#mode-segmented button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      currentMode = btn.dataset.mode;
    });
  });

  async function detectAndApplySource(url) {
    if (!url) return;
    try {
      const detected = await asyncApi("detectSource", url);
      if (detected && SOURCES[detected] && detected !== currentSource) {
        currentSource = detected;
        renderSources();
      }
    } catch (e) {}
  }
  $("url-input").addEventListener("change", (e) => detectAndApplySource(e.target.value.trim()));

  // ---------------- DOWNLOAD FLOW ----------------
  function setDownloading(state) {
    downloading = state;
    $("start-btn").disabled = state;
    $("cancel-btn").disabled = !state;
  }

  $("start-btn").addEventListener("click", async () => {
    const url = $("url-input").value.trim();
    if (!url) { toast("Введите ссылку", "error"); return; }
    if (SOURCES[currentSource] && SOURCES[currentSource].in_dev) {
      toast("Этот источник ещё в разработке", "error");
      return;
    }
    const format = $("format-select").value;
    const quality = $("quality-select").value;
    settings.format = format;
    settings.quality = quality;
    settings.source = currentSource;
    await asyncApi("settingsSet", JSON.stringify(settings));

    $("track-grid").innerHTML = "";
    cardCount = 0;
    $("log-console").textContent = "";
    $("progress-fill").style.width = "0%";
    ["downloaded", "duplicates", "skipped", "failed"].forEach((k) => ($(`stat-${k}`).textContent = "0"));
    setDownloading(true);
    $("progress-text").textContent = "Получение информации…";

    try {
      const res = await asyncApi("startDownload", url, format, currentMode);
      if (res && res.error) { toast(res.error, "error"); setDownloading(false); }
    } catch (e) {
      toast("Ошибка запуска загрузки", "error");
      setDownloading(false);
    }
  });

  $("cancel-btn").addEventListener("click", async () => {
    await asyncApi("cancel");
    appendLog("⏹ Отмена…");
  });

  function appendLog(msg) {
    const el = $("log-console");
    el.textContent += (el.textContent ? "\n" : "") + msg;
    el.scrollTop = el.scrollHeight;
  }

  function addTrackCard(data) {
    if (cardCount === 0) $("track-grid").innerHTML = "";
    cardCount++;
    const src = SOURCES[data.source] || { color: "#888" };
    const card = document.createElement("div");
    card.className = "track-card";
    card.innerHTML = `<span class="dot" style="background:${src.color}"></span>
      <div class="meta"><div class="t">${escapeHtml(data.title)}</div><div class="a">${escapeHtml(data.artist)}</div></div>`;
    $("track-grid").prepend(card);
  }

  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  // ---------------- EVENTS FROM PYTHON ----------------
  window.ShipTones = window.ShipTones || {};
  window.ShipTones.onEvent = function (type, data) {
    switch (type) {
      case "log":
        appendLog(data);
        break;
      case "progress": {
        const pct = parseFloat(data.percent) || 0;
        $("progress-fill").style.width = `${Math.min(100, pct)}%`;
        $("progress-text").textContent = `${pct.toFixed(0)}%`;
        $("progress-eta").textContent = data.eta || "";
        break;
      }
      case "status":
        $("stat-downloaded").textContent = data.downloaded ?? 0;
        $("stat-duplicates").textContent = data.duplicates ?? 0;
        $("stat-skipped").textContent = data.skipped ?? 0;
        $("stat-failed").textContent = data.failed ?? 0;
        break;
      case "track_card":
        addTrackCard(data);
        break;
      case "refresh_playlists":
        if ($("view-playlists").classList.contains("active")) loadPlaylists();
        break;
      case "done": {
        setDownloading(false);
        if (data && data.canceled) {
          $("progress-text").textContent = "Отменено";
          toast("Загрузка остановлена", "default");
        } else if (data && data.ok) {
          $("progress-text").textContent = "Готово";
          toast("Загрузка завершена", "success");
        } else {
          $("progress-text").textContent = "Ошибка";
          toast((data && data.error) || "Загрузка завершена с ошибкой", "error");
        }
        break;
      }
    }
  };

  // ---------------- HISTORY ----------------
  async function loadHistory() {
    const q = $("history-search").value.trim();
    const body = $("history-body");
    body.innerHTML = `<tr><td colspan="5" class="empty-hint">Загрузка…</td></tr>`;
    let rows = [];
    try { rows = await asyncApi("history", q); } catch (e) { rows = []; }
    body.innerHTML = "";
    if (!rows.length) {
      body.innerHTML = `<tr><td colspan="5" class="empty-hint">Пока пусто</td></tr>`;
      return;
    }
    rows.forEach(([title, artist, source, date, id]) => {
      const src = SOURCES[source] || { name: source, color: "#888" };
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${escapeHtml(title)}</td><td>${escapeHtml(artist)}</td>
        <td><span class="badge" style="background:${src.color}22;color:${src.color}"><span class="dot" style="background:${src.color}"></span>${src.name}</span></td>
        <td>${escapeHtml(date)}</td>
        <td><button class="share-btn" data-id="${id}" title="Поделиться треком">↗</button></td>`;
      body.appendChild(tr);
    });
  }
  $("history-body").addEventListener("click", (e) => {
    const btn = e.target.closest(".share-btn");
    if (!btn) return;
    shareTrack(btn.dataset.id);
  });
  async function shareTrack(id) {
    const res = await asyncApi("shareTrack", parseInt(id, 10));
    if (res && res.error) toast(res.error.startsWith("share failed") ? "Не удалось открыть трек" : res.error, "error");
  }
  let searchTimer;
  $("history-search").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadHistory, 250);
  });

  // ---------------- PLAYLISTS ----------------
  async function loadPlaylists() {
    const body = $("playlists-body");
    body.innerHTML = `<tr><td colspan="4" class="empty-hint">Загрузка…</td></tr>`;
    let rows = [];
    try { rows = await asyncApi("playlists"); } catch (e) { rows = []; }
    body.innerHTML = "";
    if (!rows.length) {
      body.innerHTML = `<tr><td colspan="4" class="empty-hint">Плейлистов пока нет</td></tr>`;
      return;
    }
    rows.forEach(([id, name, url, source, target, lastSynced]) => {
      const src = SOURCES[source] || { name: source, color: "#888" };
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${escapeHtml(name)}</td>
        <td><span class="badge" style="background:${src.color}22;color:${src.color}"><span class="dot" style="background:${src.color}"></span>${src.name}</span></td>
        <td>${escapeHtml(lastSynced || "никогда")}</td>
        <td class="row-actions">
          <button class="btn btn-sm btn-outline" data-sync="${id}" data-url="${escapeHtml(url || "")}">Синхр.</button>
          <button class="btn btn-sm btn-danger" data-del="${id}">Удалить</button>
        </td>`;
      body.appendChild(tr);
    });
    body.querySelectorAll("[data-sync]").forEach((b) => {
      b.addEventListener("click", async () => {
        toast("Синхронизация запущена");
        document.querySelector('.nav-item[data-view="download"]').click();
        const url = b.dataset.url || $("url-input").value.trim();
        if (!url) { toast("Ссылка плейлиста недоступна", "error"); return; }
        $("url-input").value = url;
        setDownloading(true);
        try {
          const res = await asyncApi("startDownload", url, $("format-select").value, "playlist");
          if (res && res.error) { toast(res.error, "error"); setDownloading(false); }
        } catch (e) {
          toast("Ошибка запуска", "error"); setDownloading(false);
        }
      });
    });
    body.querySelectorAll("[data-del]").forEach((b) => {
      b.addEventListener("click", async () => {
        if (!confirm("Удалить плейлист из списка?")) return;
        await asyncApi("deletePlaylist", parseInt(b.dataset.del, 10));
        loadPlaylists();
      });
    });
  }

  // ---------------- SETTINGS ----------------
  function bindSettings() {
    $("set-smart-dedup").checked = !!settings.smart_dedup;
    $("set-auto-categorize").checked = !!settings.auto_categorize;
    $("set-extended-tags").checked = !!settings.extended_tags;
    $("set-normalize-volume").checked = !!settings.normalize_volume;
    $("set-bass-boost").checked = !!settings.bass_boost;
    $("set-export-downloads").checked = settings.export_to_downloads !== false; // по умолчанию ВКЛ
    $("set-max-workers").value = String(settings.max_workers || 4);

    const persist = () => asyncApi("settingsSet", JSON.stringify(settings));
    $("set-smart-dedup").addEventListener("change", (e) => { settings.smart_dedup = e.target.checked; persist(); });
    $("set-auto-categorize").addEventListener("change", (e) => { settings.auto_categorize = e.target.checked; persist(); });
    $("set-extended-tags").addEventListener("change", (e) => { settings.extended_tags = e.target.checked; persist(); });
    $("set-normalize-volume").addEventListener("change", (e) => { settings.normalize_volume = e.target.checked; persist(); });
    $("set-bass-boost").addEventListener("change", (e) => { settings.bass_boost = e.target.checked; persist(); });
    $("set-export-downloads").addEventListener("change", (e) => {
      settings.export_to_downloads = e.target.checked;
      persist();
      applyDestText();
    });
    $("set-max-workers").addEventListener("change", (e) => { settings.max_workers = parseInt(e.target.value, 10); persist(); });
  }

  // ---------------- INIT ----------------
  const SPLASH_MIN_MS = 1200;

  async function init() {
    const startedAt = Date.now();
    const splash = $("splash");

    try {
      const sourcesRaw = await asyncApi("getSources");
      SOURCES = sourcesRaw || {};
      const settingsRaw = await asyncApi("settingsGet");
      settings = settingsRaw || {};
      currentSource = (settings.source && SOURCES[settings.source]) ? settings.source : "ytmusic";
      renderSources();
      bindSettings();

      $("format-select").value = settings.format || "aac";
      updateQualityOptions(settings.format || "aac");
      $("quality-select").value = settings.quality || "256";

      const dir = await asyncApi("downloadDir");
      applyDestText(dir);

      const online = await asyncApi("checkInternet");
      const pill = $("net-status");
      pill.className = "status-pill " + (online ? "online" : "offline");
      pill.querySelector("span:last-child").textContent = online ? "сеть в порядке" : "нет сети";

      const ver = await asyncApi("getAppVersion");
      appVersion = ver;
      $("app-version").textContent = `ShipTones v${ver}`;
      const vSettings = $("app-version-settings");
      if (vSettings) vSettings.textContent = `ShipTones v${ver} (Android)`;
    } catch (e) {
      toast("Ошибка инициализации интерфейса", "error");
    }

    const elapsed = Date.now() - startedAt;
    setTimeout(() => {
      splash.classList.add("hidden");
      setTimeout(() => splash.remove(), 300);
    }, Math.max(0, SPLASH_MIN_MS - elapsed));
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
