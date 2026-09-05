(() => {
  "use strict";

  let SOURCES = {};
  let settings = {};
  let currentSource = "youtube";
  let currentMode = "playlist";
  let downloading = false;
  let cardCount = 0;
  let pendingUpdate = null;

  const $ = (id) => document.getElementById(id);

  // ---------------- THEME ----------------
  function applyTheme(mode) {
    document.documentElement.setAttribute("data-theme", mode);
    try { localStorage.setItem("shiptones-theme", mode); } catch (e) {}
  }
  $("theme-toggle").addEventListener("click", () => {
    const cur = document.documentElement.getAttribute("data-theme");
    applyTheme(cur === "dark" ? "light" : "dark");
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
      btn.innerHTML = `<span class="dot" style="background:${key === currentSource ? src.fg : src.color}"></span>${src.name}${devTag}`;
      btn.addEventListener("click", () => { currentSource = key; renderSources(); });
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

  // ---------------- DRAG & DROP URL ----------------
  const dropzone = $("dropzone");
  ["dragenter", "dragover"].forEach((evt) =>
    dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.style.borderColor = "var(--brand-400)"; })
  );
  ["dragleave", "drop"].forEach((evt) =>
    dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.style.borderColor = ""; })
  );
  dropzone.addEventListener("drop", (e) => {
    const text = e.dataTransfer.getData("text/uri-list") || e.dataTransfer.getData("text/plain");
    if (text) {
      const url = text.trim();
      $("url-input").value = url;
      detectAndApplySource(url);
    }
  });

  async function detectAndApplySource(url) {
    if (!url) return;
    try {
      const detected = await window.pywebview.api.detect_source(url);
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
    const targetDir = $("target-dir-input").value.trim();
    if (!targetDir) { toast("Выберите папку назначения", "error"); return; }

    const quality = $("quality-select").value;
    settings.download_dir = targetDir;
    settings.quality = quality;
    settings.source = currentSource;
    window.pywebview.api.save_settings(settings);

    $("track-grid").innerHTML = "";
    cardCount = 0;
    $("log-console").textContent = "";
    $("progress-fill").style.width = "0%";
    ["downloaded", "duplicates", "skipped", "failed"].forEach((k) => ($(`stat-${k}`).textContent = "0"));
    $("open-folder-btn").disabled = true;
    setDownloading(true);
    $("progress-text").textContent = "Получение информации…";

    try {
      const res = await window.pywebview.api.start_download(url, currentMode, currentSource, targetDir, quality);
      if (res && res.error) {
        toast(res.error, "error");
        setDownloading(false);
      }
    } catch (e) {
      toast("Ошибка запуска загрузки", "error");
      setDownloading(false);
    }
  });

  $("cancel-btn").addEventListener("click", async () => {
    await window.pywebview.api.cancel_download();
    appendLog("⏹ Отмена…");
  });

  $("open-folder-btn").addEventListener("click", () => window.pywebview.api.open_current_folder());

  $("browse-btn").addEventListener("click", async () => {
    const picked = await window.pywebview.api.pick_folder();
    if (picked) {
      $("target-dir-input").value = picked;
      settings.download_dir = picked;
      window.pywebview.api.save_settings(settings);
    }
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
  window.onEvent = function (type, data) {
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
      case "update_progress": {
        const btn = $("update-install-btn");
        btn.textContent = data.stage === "installing" ? "Установка…" : `Загрузка… ${data.percent}%`;
        break;
      }
      case "update_error": {
        toast(`Ошибка обновления: ${data}`, "error");
        const btn = $("update-install-btn");
        btn.disabled = false;
        btn.textContent = "Обновить";
        break;
      }
      case "done": {
        setDownloading(false);
        $("open-folder-btn").disabled = false;
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
    body.innerHTML = `<tr><td colspan="4" class="empty-hint">Загрузка…</td></tr>`;
    const rows = await window.pywebview.api.get_history(q);
    body.innerHTML = "";
    if (!rows.length) {
      body.innerHTML = `<tr><td colspan="4" class="empty-hint">Пока пусто</td></tr>`;
      return;
    }
    rows.forEach(([title, artist, source, date]) => {
      const src = SOURCES[source] || { name: source, color: "#888" };
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${escapeHtml(title)}</td><td>${escapeHtml(artist)}</td>
        <td><span class="badge" style="background:${src.color}22;color:${src.color}"><span class="dot" style="background:${src.color}"></span>${src.name}</span></td>
        <td>${escapeHtml(date)}</td>`;
      body.appendChild(tr);
    });
  }
  let searchTimer;
  $("history-search").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadHistory, 250);
  });

  // ---------------- PLAYLISTS ----------------
  async function loadPlaylists() {
    const body = $("playlists-body");
    body.innerHTML = `<tr><td colspan="5" class="empty-hint">Загрузка…</td></tr>`;
    const rows = await window.pywebview.api.get_playlists();
    body.innerHTML = "";
    if (!rows.length) {
      body.innerHTML = `<tr><td colspan="5" class="empty-hint">Плейлистов пока нет</td></tr>`;
      return;
    }
    rows.forEach(([id, name, url, source, target, lastSynced]) => {
      const src = SOURCES[source] || { name: source, color: "#888" };
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${escapeHtml(name)}</td>
        <td><span class="badge" style="background:${src.color}22;color:${src.color}"><span class="dot" style="background:${src.color}"></span>${src.name}</span></td>
        <td title="${escapeHtml(target)}">${escapeHtml(target)}</td>
        <td>${escapeHtml(lastSynced || "никогда")}</td>
        <td class="row-actions">
          <button class="btn btn-sm btn-outline" data-sync="${id}">Синхр.</button>
          <button class="btn btn-sm btn-danger" data-del="${id}">Удалить</button>
        </td>`;
      body.appendChild(tr);
    });
    body.querySelectorAll("[data-sync]").forEach((b) =>
      b.addEventListener("click", async () => {
        toast("Синхронизация запущена");
        document.querySelector('.nav-item[data-view="download"]').click();
        setDownloading(true);
        const res = await window.pywebview.api.sync_playlist(parseInt(b.dataset.sync, 10));
        if (res && res.error) { toast(res.error, "error"); setDownloading(false); }
      })
    );
    body.querySelectorAll("[data-del]").forEach((b) =>
      b.addEventListener("click", async () => {
        if (!confirm("Удалить плейлист из списка?")) return;
        await window.pywebview.api.delete_playlist(parseInt(b.dataset.del, 10));
        loadPlaylists();
      })
    );
  }

  // ---------------- SETTINGS ----------------
  function bindSettings() {
    $("set-smart-dedup").checked = !!settings.smart_dedup;
    $("set-auto-categorize").checked = !!settings.auto_categorize;
    $("set-extended-tags").checked = !!settings.extended_tags;
    $("set-auto-update").checked = !!settings.auto_update_ytdlp;
    $("set-auto-check-update").checked = settings.auto_check_update !== false;
    $("set-max-workers").value = String(settings.max_workers || 3);

    const persist = () => window.pywebview.api.save_settings(settings);
    $("set-smart-dedup").addEventListener("change", (e) => { settings.smart_dedup = e.target.checked; persist(); });
    $("set-auto-categorize").addEventListener("change", (e) => { settings.auto_categorize = e.target.checked; persist(); });
    $("set-extended-tags").addEventListener("change", (e) => { settings.extended_tags = e.target.checked; persist(); });
    $("set-auto-update").addEventListener("change", (e) => { settings.auto_update_ytdlp = e.target.checked; persist(); });
    $("set-auto-check-update").addEventListener("change", (e) => { settings.auto_check_update = e.target.checked; persist(); });
    $("set-max-workers").addEventListener("change", (e) => { settings.max_workers = parseInt(e.target.value, 10); persist(); });
  }

  $("update-ytdlp-btn").addEventListener("click", async (e) => {
    e.target.disabled = true;
    e.target.textContent = "Обновление…";
    const res = await window.pywebview.api.update_ytdlp();
    toast(res.msg, res.ok ? "success" : "error");
    e.target.disabled = false;
    e.target.textContent = "Обновить yt-dlp";
  });

  // ---------------- АВТООБНОВЛЕНИЕ ПРИЛОЖЕНИЯ ----------------
  function showUpdateBanner(info) {
    pendingUpdate = info;
    $("update-banner-text").textContent = `Доступна новая версия ShipTones ${info.version} — нажмите «Обновить», приложение перезапустится само`;
    $("update-banner").hidden = false;
  }

  $("update-install-btn").addEventListener("click", async () => {
    if (!pendingUpdate) return;
    const btn = $("update-install-btn");
    btn.disabled = true;
    btn.textContent = "Загрузка… 0%";
    try {
      const res = await window.pywebview.api.install_update(pendingUpdate.url);
      if (res && res.error) {
        toast(res.error, "error");
        btn.disabled = false;
        btn.textContent = "Обновить";
      }
    } catch (e) {
      toast("Не удалось запустить обновление", "error");
      btn.disabled = false;
      btn.textContent = "Обновить";
    }
  });

  $("update-dismiss-btn").addEventListener("click", () => { $("update-banner").hidden = true; });

  // ---------------- INIT ----------------
  const SPLASH_MIN_MS = 1500;

  async function init() {
    const startedAt = Date.now();
    const splash = $("splash");

    try {
      SOURCES = await window.pywebview.api.get_sources();
      settings = await window.pywebview.api.get_settings();
      currentSource = settings.source || "youtube";
      renderSources();
      bindSettings();

      $("quality-select").value = settings.quality || "192";
      $("target-dir-input").value = settings.download_dir || "";

      window.pywebview.api.check_internet().then((online) => {
        const pill = $("net-status");
        pill.className = "status-pill " + (online ? "online" : "offline");
        pill.querySelector("span:last-child").textContent = online ? "сеть в порядке" : "нет сети";
      });

      window.pywebview.api.get_app_version().then((v) => {
        $("app-version").textContent = `ShipTones v${v}`;
      });

      if (settings.auto_check_update !== false) {
        window.pywebview.api.check_for_update().then((info) => {
          if (info) showUpdateBanner(info);
        }).catch(() => {});
      }
    } catch (e) {
      toast("Ошибка инициализации интерфейса", "error");
    }

    const elapsed = Date.now() - startedAt;
    setTimeout(() => {
      splash.classList.add("hidden");
      setTimeout(() => splash.remove(), 300);
    }, Math.max(0, SPLASH_MIN_MS - elapsed));
  }

  window.addEventListener("pywebviewready", init);
})();
