import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime
from pathlib import Path

from config import (load_settings, save_settings, HAS_MUTAGEN, HAS_PIL, HAS_DND,
                    SOURCES, detect_source, C_BG, C_PANEL, C_CARD, C_CARD2,
                    C_BORDER, C_TEXT, C_TEXT2, C_ACCENT, logger)
from utils import find_removable_drives, check_internet, find_ffmpeg, update_ytdlp
from database import DB
from downloader import Downloader
from tooltip import Tooltip

if HAS_DND:
    import windnd

# ============================================================
# ГЛАВНОЕ ОКНО СО ВСЕМИ ФИШКАМИ
# ============================================================
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.title("ShipTones v3.0 by Ivan Shipkov")
        self.geometry("1200x750")
        self.minsize(1000, 650)
        self.configure(bg=C_BG)

        self.gui_queue = queue.Queue()
        self.downloader = None
        self.current_target_folder = None
        self.source_var = tk.StringVar(value=self.settings.get("source", "youtube"))
        self.view_mode = "download"
        self._playlists_data = []
        self.nav_buttons = {}
        self._card_count = 0  # для сетки карточек 4 в ряд

        self._build_ui()
        self._process_queue()
        self.after(500, self._initial_checks)

        # Фишка 4: Drag & Drop ссылок
        if HAS_DND:
            try:
                windnd.hook_dropfiles(self, func=self._on_drop)
            except Exception as e:
                logger.error(f"D&D init failed: {e}")

    def _on_drop(self, files):
        if files:
            text = files[0].decode('utf-8', errors='ignore') if isinstance(files[0], bytes) else files[0]
            if '://' in text:
                self.url_entry.delete(0, "end")
                self.url_entry.insert(0, text.strip())
                self._set_source(detect_source(text))
                self._log("📥 Ссылка получена через Drag & Drop")

    def _hover(self, widget, enter_bg, leave_bg):
        widget.bind("<Enter>", lambda e: widget.config(bg=enter_bg), add="+")
        widget.bind("<Leave>", lambda e: widget.config(bg=leave_bg), add="+")

    # ============================================================
    # ПОСТРОЕНИЕ ИНТЕРФЕЙСА
    # ============================================================
    def _build_ui(self):
        style = ttk.Style()
        style.theme_use('clam')

        style.configure("TFrame", background=C_BG)
        style.configure("TLabel", background=C_BG, foreground=C_TEXT, font=("Segoe UI", 10))
        style.configure("TEntry", fieldbackground=C_CARD2, foreground=C_TEXT, insertcolor=C_TEXT, borderwidth=0)
        style.configure("TCombobox", fieldbackground=C_CARD2, background=C_CARD2,
                        foreground=C_TEXT, arrowcolor=C_TEXT2, borderwidth=0)
        style.map("TCombobox", fieldbackground=[("readonly", C_CARD2)],
                  background=[("readonly", C_CARD2)])
        style.configure("TProgressbar", background=C_ACCENT, troughcolor=C_CARD2, borderwidth=0, thickness=10)
        style.configure("TRadiobutton", background=C_BG, foreground=C_TEXT, font=("Segoe UI", 10))
        style.map("TRadiobutton", background=[("active", C_BG)])
        style.configure("Treeview", background=C_CARD, foreground=C_TEXT, fieldbackground=C_CARD,
                        borderwidth=0, font=("Segoe UI", 10), rowheight=30)
        style.configure("Treeview.Heading", background=C_CARD, foreground=C_TEXT,
                        font=("Segoe UI", 10, "bold"), borderwidth=0, relief="flat")
        style.map("Treeview", background=[("selected", C_ACCENT)], foreground=[("selected", "#ffffff")])
        style.map("Treeview.Heading", background=[("active", "#222222")])
        style.configure("TScrollbar", troughcolor=C_BG, background="#2a2a2a", borderwidth=0, arrowcolor=C_TEXT2)
        style.map("TScrollbar", background=[("active", "#3a3a3a")])

        self.option_add('*TCombobox*Listbox.background', C_CARD2)
        self.option_add('*TCombobox*Listbox.foreground', C_TEXT)
        self.option_add('*TCombobox*Listbox.selectBackground', C_ACCENT)

        main_container = tk.Frame(self, bg=C_BG)
        main_container.pack(fill="both", expand=True)

        # ---------- ФИШКА 17: БОКОВАЯ ПАНЕЛЬ ----------
        self.sidebar = tk.Frame(main_container, width=220, bg=C_PANEL)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        tk.Label(self.sidebar, text="🎵  ShipTones", bg=C_PANEL, fg=C_TEXT,
                 font=("Segoe UI", 16, "bold"), anchor="w").pack(pady=20, padx=15, fill="x")
        tk.Frame(self.sidebar, bg=C_BORDER, height=1).pack(fill="x", padx=15, pady=5)

        for text, mode in [("⬇  Загрузка", "download"), ("📜  История", "history"),
                           ("💾  Плейлисты", "playlists"), ("⚙  Настройки", "settings")]:
            b = tk.Button(self.sidebar, text=text, bg=C_PANEL, fg=C_TEXT, bd=0,
                          font=("Segoe UI", 10), anchor="w", padx=18, pady=10, cursor="hand2",
                          activebackground="#1c1c1c", activeforeground=C_TEXT,
                          command=lambda m=mode: self._switch_view(m))
            b.pack(fill="x", pady=2)
            b.bind("<Enter>", lambda e, w=b, m=mode: self._nav_enter(w, m))
            b.bind("<Leave>", lambda e, w=b, m=mode: self._nav_leave(w, m))
            self.nav_buttons[mode] = b

        tk.Frame(self.sidebar, bg=C_BORDER, height=1).pack(fill="x", padx=15, pady=10)
        tk.Label(self.sidebar, text="СОХРАНЁННЫЕ", bg=C_PANEL, fg="#777777",
                 font=("Segoe UI", 9), anchor="w").pack(padx=18, fill="x")

        self.playlists_listbox = tk.Listbox(self.sidebar, bg=C_PANEL, fg=C_TEXT,
                                            selectbackground=C_ACCENT, selectforeground="#ffffff",
                                            font=("Segoe UI", 9), bd=0, highlightthickness=0,
                                            height=10, activestyle='none')
        self.playlists_listbox.pack(fill="both", expand=True, padx=10, pady=5)
        self.playlists_listbox.bind("<<ListboxSelect>>", self._on_playlist_select)

        del_btn = tk.Button(self.sidebar, text="🗑  Удалить плейлист", bg=C_PANEL, fg=C_TEXT2,
                            bd=0, font=("Segoe UI", 9), anchor="w", padx=18, pady=8,
                            cursor="hand2", activebackground="#1c1c1c", command=self._delete_playlist)
        del_btn.pack(fill="x", side="bottom", pady=5)
        self._hover(del_btn, "#1c1c1c", C_PANEL)
        self._refresh_playlists_list()

        # ---------- ЦЕНТР ----------
        self.content = tk.Frame(main_container, bg=C_BG)
        self.content.pack(side="left", fill="both", expand=True)

        # ФИШКА 11/12: ВЫБОР ИСТОЧНИКА ВВЕРХУ
        # (у Яндекса на жёлтом фоне — чёрный текст, неактивные — тёмные с белым)
        top_bar = tk.Frame(self.content, bg=C_BG)
        top_bar.pack(fill="x", padx=20, pady=(15, 10))
        tk.Label(top_bar, text="Источник:", bg=C_BG, fg=C_TEXT,
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        self.source_buttons = {}
        for key, src in SOURCES.items():
            selected = key == self.source_var.get()
            btn = tk.Button(top_bar, text=f"{src['icon']}  {src['name']}",
                            bg=src['color'] if selected else C_CARD,
                            fg=src['fg'] if selected else C_TEXT,
                            font=("Segoe UI", 10, "bold"), bd=0, relief="flat",
                            padx=18, pady=8, cursor="hand2",
                            activebackground=src['color'],
                            activeforeground=src['fg'],
                            command=lambda k=key: self._set_source(k))
            btn.pack(side="left", padx=5)
            self.source_buttons[key] = btn

        self.views = {}
        for name, builder in [("download", self._build_download_view),
                              ("history", self._build_history_view),
                              ("playlists", self._build_playlists_view),
                              ("settings", self._build_settings_view)]:
            f = tk.Frame(self.content, bg=C_BG)
            builder(f)
            self.views[name] = f

        # ФИШКА 20: СТАТУС-БАР
        self.status_bar = tk.Frame(self.content, bg=C_BG, height=30)
        self.status_bar.pack(fill="x", side="bottom", padx=20, pady=(5, 10))
        self.status_label = tk.Label(self.status_bar, text="✓ Готов к работе",
                                     bg=C_BG, fg="#888888", font=("Segoe UI", 9), anchor="w")
        self.status_label.pack(side="left")

        self._switch_view("download")

    # ---------- ВЬЮШКА: ЗАГРУЗКА ----------
    def _build_download_view(self, parent):
        tk.Label(parent, text="Загрузка музыки", bg=C_BG, fg=C_TEXT,
                 font=("Segoe UI", 20, "bold"), anchor="w").pack(anchor="w", pady=(0, 15))

        tk.Label(parent, text="📝  Ссылка на трек или плейлист:", bg=C_BG, fg=C_TEXT2,
                 font=("Segoe UI", 10), anchor="w").pack(anchor="w", pady=(5, 5))
        self.url_entry = tk.Entry(parent, font=("Segoe UI", 12), bg=C_CARD2, fg=C_TEXT,
                                  insertbackground=C_TEXT, relief="flat", bd=0,
                                  highlightthickness=1, highlightbackground=C_BORDER,
                                  highlightcolor=C_ACCENT)
        self.url_entry.pack(fill="x", pady=(0, 5), ipady=8)
        self.url_entry.bind("<KeyPress>", self._hotkeys)

        options_frame = tk.Frame(parent, bg=C_BG)
        options_frame.pack(fill="x", pady=10)

        tk.Label(options_frame, text="💾  Диск:", bg=C_BG, fg=C_TEXT, font=("Segoe UI", 10)).pack(side="left")
        self.disk_var = tk.StringVar()
        self.disk_menu = ttk.Combobox(options_frame, textvariable=self.disk_var, state="readonly", width=12)
        self.disk_menu.pack(side="left", padx=10, ipady=4)
        refresh_btn = tk.Button(options_frame, text="🔄", bg=C_CARD2, fg=C_TEXT, bd=0, cursor="hand2",
                                command=self._refresh_disks, activebackground="#2a2a2a")
        refresh_btn.pack(side="left", padx=5, ipadx=6, ipady=4)
        self._hover(refresh_btn, "#2a2a2a", C_CARD2)

        tk.Label(options_frame, text="🎵  Качество:", bg=C_BG, fg=C_TEXT, font=("Segoe UI", 10)).pack(side="left", padx=(25, 0))
        self.quality_var = tk.StringVar(value=self.settings.get("quality", "192"))
        self.quality_menu = ttk.Combobox(options_frame, textvariable=self.quality_var,
                                         values=["128", "192", "256", "320"], state="readonly", width=8)
        self.quality_menu.pack(side="left", padx=10, ipady=4)
        Tooltip(self.quality_menu, "Битрейт MP3: 192 — стандарт, 320 — максимум")

        tk.Label(options_frame, text="📺  Режим:", bg=C_BG, fg=C_TEXT, font=("Segoe UI", 10)).pack(side="left", padx=(25, 0))
        self.mode_var = tk.StringVar(value="playlist")
        tk.Radiobutton(options_frame, text="Один трек", variable=self.mode_var, value="single",
                       bg=C_BG, fg=C_TEXT, activebackground=C_BG, selectcolor=C_CARD2, bd=0,
                       highlightthickness=0, font=("Segoe UI", 10), cursor="hand2").pack(side="left", padx=5)
        tk.Radiobutton(options_frame, text="Плейлист", variable=self.mode_var, value="playlist",
                       bg=C_BG, fg=C_TEXT, activebackground=C_BG, selectcolor=C_CARD2, bd=0,
                       highlightthickness=0, font=("Segoe UI", 10), cursor="hand2").pack(side="left", padx=5)

        btn_frame = tk.Frame(parent, bg=C_BG)
        btn_frame.pack(fill="x", pady=15)
        self.download_btn = tk.Button(btn_frame, text="▶  Начать загрузку", bg=C_ACCENT, fg=C_TEXT, bd=0,
                                      font=("Segoe UI", 11, "bold"), cursor="hand2",
                                      activebackground="#2563eb", command=self._start_download)
        self.download_btn.pack(side="left", fill="x", expand=True, padx=(0, 5), ipady=10)
        self._hover(self.download_btn, "#2563eb", C_ACCENT)
        self.open_folder_btn = tk.Button(btn_frame, text="📁  Открыть папку", bg=C_CARD2, fg=C_TEXT2, bd=0,
                                         font=("Segoe UI", 10, "bold"), cursor="hand2",
                                         command=self._open_folder, state="disabled")
        self.open_folder_btn.pack(side="right", fill="x", expand=True, padx=(5, 0), ipady=10)
        self.cancel_btn = tk.Button(btn_frame, text="⏹  Стоп", bg=C_CARD2, fg=C_TEXT2, bd=0,
                                    font=("Segoe UI", 10, "bold"), cursor="hand2",
                                    command=self._cancel_download, state="disabled")
        self.cancel_btn.pack(side="right", fill="x", expand=True, padx=(5, 0), ipady=10)

        self.progress_bar = ttk.Progressbar(parent, mode="determinate")
        self.progress_bar.pack(fill="x", pady=10)
        self.progress_info = tk.Label(parent, text="", bg=C_BG, fg="#999999", font=("Segoe UI", 10), anchor="w")
        self.progress_info.pack(anchor="w")

        # ФИШКА 18: КАРТОЧКИ ТРЕКОВ (сеткой 4 в ряд)
        tk.Label(parent, text="🎧  Скачанные треки (в этой сессии):", bg=C_BG, fg=C_TEXT,
                 font=("Segoe UI", 11, "bold"), anchor="w").pack(anchor="w", pady=(15, 5))
        tracks_frame = tk.Frame(parent, bg=C_BG)
        tracks_frame.pack(fill="both", expand=True)
        self.tracks_canvas = tk.Canvas(tracks_frame, bg=C_BG, highlightthickness=0)
        t_scroll = tk.Scrollbar(tracks_frame, orient="vertical", command=self.tracks_canvas.yview,
                                bg=C_BG, troughcolor=C_BG, bd=0)
        self.tracks_inner = tk.Frame(self.tracks_canvas, bg=C_BG)
        self.tracks_inner.bind("<Configure>", lambda e: self.tracks_canvas.configure(scrollregion=self.tracks_canvas.bbox("all")))
        self.tracks_canvas.create_window((0, 0), window=self.tracks_inner, anchor="nw")
        self.tracks_canvas.configure(yscrollcommand=t_scroll.set)
        self.tracks_canvas.pack(side="left", fill="both", expand=True)
        t_scroll.pack(side="right", fill="y")
        self.tracks_canvas.bind_all("<MouseWheel>",
                                    lambda e: self.tracks_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        tk.Label(parent, text="📜  Лог", bg=C_BG, fg=C_TEXT2, font=("Segoe UI", 10, "bold"),
                 anchor="w").pack(anchor="w", pady=(10, 5))
        self.log_text = tk.Text(parent, height=5, bg="#101010", fg="#bbbbbb", font=("Consolas", 9),
                                relief="flat", bd=0, highlightthickness=1, highlightbackground=C_BORDER,
                                state="disabled")
        self.log_text.pack(fill="x")

        self._refresh_disks()
        if self.settings.get("download_disk") in self.disk_menu['values']:
            self.disk_var.set(self.settings["download_disk"])

    # ---------- ВЬЮШКА: ИСТОРИЯ (фишка 5) ----------
    def _build_history_view(self, parent):
        tk.Label(parent, text="История загрузок", bg=C_BG, fg=C_TEXT,
                 font=("Segoe UI", 20, "bold"), anchor="w").pack(anchor="w", pady=(0, 15))
        search_frame = tk.Frame(parent, bg=C_BG)
        search_frame.pack(fill="x", pady=10)
        tk.Label(search_frame, text="🔍  Поиск:", bg=C_BG, fg=C_TEXT2, font=("Segoe UI", 10)).pack(side="left")
        self.history_search_var = tk.StringVar()
        self.history_search_var.trace("w", lambda *a: self._refresh_history())
        tk.Entry(search_frame, textvariable=self.history_search_var, font=("Segoe UI", 11),
                 bg=C_CARD2, fg=C_TEXT, insertbackground=C_TEXT, bd=0, relief="flat",
                 highlightthickness=1, highlightbackground=C_BORDER,
                 highlightcolor=C_ACCENT).pack(side="left", fill="x", expand=True, padx=10, ipady=6)

        columns = ("title", "artist", "source", "date")
        self.history_tree = ttk.Treeview(parent, columns=columns, show="headings", height=22)
        for c, t, w in [(columns[0], "Название", 300), (columns[1], "Исполнитель", 200),
                        (columns[2], "Источник", 100), (columns[3], "Дата", 160)]:
            self.history_tree.heading(c, text=t)
            self.history_tree.column(c, width=w)
        h_scroll = ttk.Scrollbar(parent, orient="vertical", command=self.history_tree.yview)
        self.history_tree.configure(yscrollcommand=h_scroll.set)
        self.history_tree.pack(side="left", fill="both", expand=True)
        h_scroll.pack(side="right", fill="y")
        self._refresh_history()

    # ---------- ВЬЮШКА: ПЛЕЙЛИСТЫ (фишки 15/17) ----------
    def _build_playlists_view(self, parent):
        tk.Label(parent, text="Сохранённые плейлисты", bg=C_BG, fg=C_TEXT,
                 font=("Segoe UI", 20, "bold"), anchor="w").pack(anchor="w", pady=(0, 10))
        tk.Label(parent, text="Все плейлисты, которые вы скачивали. Можно запустить синхронизацию для любого из них.",
                 bg=C_BG, fg=C_TEXT2, font=("Segoe UI", 10), anchor="w").pack(anchor="w", pady=(0, 15))

        columns = ("name", "source", "path", "last_sync")
        self.playlists_tree = ttk.Treeview(parent, columns=columns, show="headings", height=20)
        for c, t, w in [(columns[0], "Название", 250), (columns[1], "Источник", 110),
                        (columns[2], "Папка", 300), (columns[3], "Синхронизация", 150)]:
            self.playlists_tree.heading(c, text=t)
            self.playlists_tree.column(c, width=w)
        p_scroll = ttk.Scrollbar(parent, orient="vertical", command=self.playlists_tree.yview)
        self.playlists_tree.configure(yscrollcommand=p_scroll.set)
        self.playlists_tree.pack(side="left", fill="both", expand=True)
        p_scroll.pack(side="right", fill="y")

        btn_frame = tk.Frame(parent, bg=C_BG)
        btn_frame.pack(fill="x", side="bottom", pady=10)
        sync_btn = tk.Button(btn_frame, text="🔄  Синхронизировать выбранный", bg=C_ACCENT, fg=C_TEXT,
                             bd=0, font=("Segoe UI", 10, "bold"), cursor="hand2",
                             activebackground="#2563eb", command=self._sync_selected_playlist)
        sync_btn.pack(side="left", padx=5, ipadx=10, ipady=8)
        self._hover(sync_btn, "#2563eb", C_ACCENT)
        open_btn = tk.Button(btn_frame, text="📂  Открыть папку", bg=C_CARD2, fg=C_TEXT, bd=0,
                             font=("Segoe UI", 10, "bold"), cursor="hand2",
                             activebackground="#2a2a2a", command=self._open_playlist_folder)
        open_btn.pack(side="left", padx=5, ipadx=10, ipady=8)
        self._hover(open_btn, "#2a2a2a", C_CARD2)
        self._refresh_playlists_table()

    # ---------- ВЬЮШКА: НАСТРОЙКИ (все вкл/выкл + тултипы) ----------
    def _settings_card(self, master, title):
        card = tk.Frame(master, bg=C_CARD)
        card.pack(fill="x", pady=8, padx=4)
        header = tk.Frame(card, bg=C_CARD)
        header.pack(fill="x", padx=16, pady=(14, 4))
        tk.Label(header, text=title, bg=C_CARD, fg=C_TEXT, font=("Segoe UI", 11, "bold"),
                 anchor="w").pack(side="left")
        tk.Frame(card, bg=C_BORDER, height=1).pack(fill="x", padx=16, pady=(0, 4))
        body = tk.Frame(card, bg=C_CARD)
        body.pack(fill="x", padx=16, pady=(0, 14))
        return body

    def _settings_row(self, body, row, name, tip, var):
        lbl = tk.Label(body, text=name, bg=C_CARD, fg=C_TEXT, font=("Segoe UI", 10), anchor="w")
        lbl.grid(row=row, column=0, sticky="w", padx=(0, 15), pady=7)
        cb = tk.Checkbutton(body, variable=var, bg=C_CARD, fg=C_TEXT, activebackground=C_CARD,
                            activeforeground=C_TEXT, selectcolor="#262626", bd=0,
                            highlightthickness=0, cursor="hand2")
        cb.grid(row=row, column=1, sticky="w", pady=7)
        Tooltip(lbl, tip)
        Tooltip(cb, tip)

    def _build_settings_view(self, parent):
        wrapper = tk.Frame(parent, bg=C_BG)
        wrapper.pack(fill="both", expand=True)
        tk.Label(wrapper, text="Настройки", bg=C_BG, fg=C_TEXT, font=("Segoe UI", 20, "bold"),
                 anchor="w").pack(anchor="w", pady=(0, 10))

        canvas = tk.Canvas(wrapper, bg=C_BG, highlightthickness=0)
        s_scroll = tk.Scrollbar(wrapper, orient="vertical", command=canvas.yview, bg=C_BG, troughcolor=C_BG, bd=0)
        settings_inner = tk.Frame(canvas, bg=C_BG)
        settings_inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=settings_inner, anchor="nw")
        canvas.configure(yscrollcommand=s_scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        s_scroll.pack(side="right", fill="y")

        body = self._settings_card(settings_inner, "🎵  Качество аудио")
        self.normalize_var = tk.BooleanVar(value=self.settings.get("normalize_volume", True))
        self._settings_row(body, 0, "Нормализация громкости (EBU R128):",
                           "Все треки звучат одинаково громко — в машине не придётся крутить ручку",
                           self.normalize_var)
        self.silence_var = tk.BooleanVar(value=self.settings.get("remove_silence", True))
        self._settings_row(body, 1, "Удаление тишины в начале/конце:",
                           "Убирает пустые паузы в начале и конце трека", self.silence_var)
        self.ext_tags_var = tk.BooleanVar(value=self.settings.get("extended_tags", True))
        self._settings_row(body, 2, "Расширенные ID3-теги:",
                           "Альбом, год, жанр и номер трека — плеер покажет всё", self.ext_tags_var)

        body = self._settings_card(settings_inner, "⬇  Загрузка")
        self.dedup_var = tk.BooleanVar(value=self.settings.get("smart_dedup", True))
        self._settings_row(body, 0, "Умное удаление дубликатов:",
                           "Не качает треки, которые уже есть в истории", self.dedup_var)
        self.categorize_var = tk.BooleanVar(value=self.settings.get("auto_categorize", False))
        self._settings_row(body, 1, "Авто-каталогизация:",
                           "Раскладывает музыку по папкам Исполнитель/Альбом", self.categorize_var)
        tk.Label(body, text="Количество потоков:", bg=C_CARD, fg=C_TEXT, font=("Segoe UI", 10),
                 anchor="w").grid(row=2, column=0, sticky="w", padx=(0, 15), pady=7)
        self.workers_var = tk.StringVar(value=str(self.settings.get("max_workers", 3)))
        w_cb = ttk.Combobox(body, textvariable=self.workers_var, values=["1", "2", "3", "4", "5"],
                            state="readonly", width=5)
        w_cb.grid(row=2, column=1, sticky="w", pady=7)
        Tooltip(w_cb, "Сколько треков качается одновременно")

        body = self._settings_card(settings_inner, "🔄  Обновления и синхронизация")
        self.auto_update_var = tk.BooleanVar(value=self.settings.get("auto_update_ytdlp", True))
        self._settings_row(body, 0, "Авто-обновление yt-dlp:",
                           "Ставит свежий yt-dlp, чтобы YouTube не блокировал загрузки", self.auto_update_var)
        self.sync_enabled_var = tk.BooleanVar(value=self.settings.get("sync_enabled", False))
        self._settings_row(body, 1, "Включить автосинхронизацию:",
                           "Сама докачивает новые треки из сохранённых плейлистов", self.sync_enabled_var)
        tk.Label(body, text="Диск для синхронизации:", bg=C_CARD, fg=C_TEXT, font=("Segoe UI", 10),
                 anchor="w").grid(row=2, column=0, sticky="w", padx=(0, 15), pady=7)
        self.sync_disk_var = tk.StringVar(value=self.settings.get("sync_disk", ""))
        sd_cb = ttk.Combobox(body, textvariable=self.sync_disk_var,
                             values=find_removable_drives(), state="readonly", width=14)
        sd_cb.grid(row=2, column=1, sticky="w", pady=7)
        Tooltip(sd_cb, "Куда докачивать плейлисты (обычно флешка)")
        tk.Label(body, text="Интервал (часы):", bg=C_CARD, fg=C_TEXT, font=("Segoe UI", 10),
                 anchor="w").grid(row=3, column=0, sticky="w", padx=(0, 15), pady=7)
        self.sync_interval_var = tk.StringVar(value=str(self.settings.get("sync_interval_hours", 24)))
        si_cb = ttk.Combobox(body, textvariable=self.sync_interval_var,
                             values=["6", "12", "24", "48", "168"], state="readonly", width=5)
        si_cb.grid(row=3, column=1, sticky="w", pady=7)
        Tooltip(si_cb, "Как часто проверять плейлисты на новинки")

        save_btn = tk.Button(settings_inner, text="💾  Сохранить настройки", bg=C_ACCENT, fg=C_TEXT,
                             bd=0, font=("Segoe UI", 11, "bold"), cursor="hand2",
                             activebackground="#2563eb", command=self._save_settings)
        save_btn.pack(fill="x", padx=4, pady=20, ipady=10)
        self._hover(save_btn, "#2563eb", C_ACCENT)

    # ============================================================
    # ГОРЯЧИЕ КЛАВИШИ ПО KEYCODE (любая раскладка, фикс Ctrl+X)
    # ============================================================
    def _hotkeys(self, event):
        if not event.state & 0x4:  # зажат Ctrl
            return None
        kc = event.keycode
        if kc == 65:    # A — выделить всё
            self.url_entry.select_range(0, "end")
            self.url_entry.icursor("end")
            return "break"
        if kc == 67:    # C — копировать
            self._entry_copy(self.url_entry)
            return "break"
        if kc == 88:    # X — вырезать
            self._entry_cut(self.url_entry)
            return "break"
        if kc == 86:    # V — вставить
            self._entry_paste(self.url_entry)
            self._log("📋 Ссылка вставлена")
            return "break"
        return None

    def _entry_copy(self, w):
        try:
            text = w.get("sel.first", "sel.last")
            self.clipboard_clear()
            self.clipboard_append(text)
        except Exception:
            pass

    def _entry_cut(self, w):
        try:
            first = w.index("sel.first")
            last = w.index("sel.last")
            text = w.get(first, last)
            w.delete(first, last)
            self.clipboard_clear()
            self.clipboard_append(text)
        except Exception:
            pass

    def _entry_paste(self, w):
        try:
            text = self.clipboard_get().strip()
        except Exception:
            return
        if not text:
            return
        try:
            w.delete("sel.first", "sel.last")
        except Exception:
            pass
        w.insert("insert", text)

    # ============================================================
    # НАВИГАЦИЯ / ИСТОЧНИКИ / ДАННЫЕ
    # ============================================================
    def _nav_enter(self, w, mode):
        if self.view_mode != mode:
            w.config(bg="#1c1c1c")

    def _nav_leave(self, w, mode):
        if self.view_mode != mode:
            w.config(bg=C_PANEL)

    def _switch_view(self, view_name):
        self.view_mode = view_name
        for view in self.views.values():
            view.pack_forget()
        self.views[view_name].pack(fill="both", expand=True, padx=20)
        for key, btn in self.nav_buttons.items():
            btn.config(bg=C_ACCENT if key == view_name else C_PANEL, fg=C_TEXT)
        if view_name == "history":
            self._refresh_history()
        elif view_name == "playlists":
            self._refresh_playlists_table()

    def _set_source(self, source):
        self.source_var.set(source)
        for key, btn in self.source_buttons.items():
            src = SOURCES[key]
            if key == source:
                # Активная: цветной фон + свой цвет текста (у Яндекса — чёрный)
                btn.configure(bg=src['color'], fg=src['fg'],
                              activebackground=src['color'], activeforeground=src['fg'])
            else:
                # Неактивная: тёмный фон + белый текст
                btn.configure(bg=C_CARD, fg=C_TEXT,
                              activebackground=src['color'], activeforeground=src['fg'])

    def _refresh_disks(self):
        drives = find_removable_drives()
        self.disk_menu['values'] = drives
        if drives and self.disk_var.get() not in drives:
            self.disk_var.set(drives[0])

    def _refresh_playlists_list(self):
        self.playlists_listbox.delete(0, tk.END)
        self._playlists_data = DB.get_playlists()
        for p in self._playlists_data:
            icon = SOURCES.get(p[3], {}).get('icon', '🎵')
            name = p[1][:25] + "..." if len(p[1]) > 25 else p[1]
            self.playlists_listbox.insert(tk.END, f"{icon} {name}")

    def _refresh_playlists_table(self):
        for item in self.playlists_tree.get_children():
            self.playlists_tree.delete(item)
        for p in DB.get_playlists():
            self.playlists_tree.insert("", tk.END, values=(
                p[1], SOURCES.get(p[3], {}).get('name', p[3]), p[4], p[5] or "Никогда"))

    def _refresh_history(self):
        search = self.history_search_var.get() if hasattr(self, 'history_search_var') else ""
        for item in self.history_tree.get_children():
            self.history_tree.delete(item)
        for row in DB.get_history(search):
            self.history_tree.insert("", tk.END, values=(
                row[0], row[1], SOURCES.get(row[2], {}).get('name', row[2]), row[3]))

    def _on_playlist_select(self, event):
        sel = self.playlists_listbox.curselection()
        if sel and self._playlists_data:
            p = self._playlists_data[sel[0]]
            self.url_entry.delete(0, "end")
            self.url_entry.insert(0, p[2])
            self._set_source(p[3])
            self._switch_view("download")

    def _delete_playlist(self):
        sel = self.playlists_listbox.curselection()
        if sel and self._playlists_data:
            p = self._playlists_data[sel[0]]
            if messagebox.askyesno("Удалить", f"Удалить плейлист '{p[1]}' из списка?"):
                DB.delete_playlist(p[0])
                self._refresh_playlists_list()

    def _sync_selected_playlist(self):
        sel = self.playlists_tree.selection()
        if not sel:
            self._log("⚠ Выберите плейлист для синхронизации")
            return
        name = self.playlists_tree.item(sel[0])['values'][0]
        for p in DB.get_playlists():
            if p[1] == name:
                root = str(Path(p[4]).parent) if p[4] else None
                if not root:
                    return
                self.url_entry.delete(0, "end")
                self.url_entry.insert(0, p[2])
                self._set_source(p[3])
                self._switch_view("download")
                self._launch_download(p[2], p[3], root)
                break

    def _open_playlist_folder(self):
        sel = self.playlists_tree.selection()
        if not sel:
            return
        name = self.playlists_tree.item(sel[0])['values'][0]
        for p in DB.get_playlists():
            if p[1] == name:
                if p[4] and os.path.exists(p[4]):
                    os.startfile(p[4])
                else:
                    self._log(f"⚠ Папка не найдена: {p[4]}")
                break

    def _save_settings(self):
        self.settings.update({
            "normalize_volume": self.normalize_var.get(),
            "remove_silence": self.silence_var.get(),
            "extended_tags": self.ext_tags_var.get(),
            "smart_dedup": self.dedup_var.get(),
            "auto_categorize": self.categorize_var.get(),
            "auto_update_ytdlp": self.auto_update_var.get(),
            "max_workers": int(self.workers_var.get()),
            "sync_enabled": self.sync_enabled_var.get(),
            "sync_disk": self.sync_disk_var.get(),
            "sync_interval_hours": int(self.sync_interval_var.get()),
        })
        save_settings(self.settings)
        self._log("💾 Настройки сохранены")
        messagebox.showinfo("Настройки", "Настройки сохранены успешно!")

    # ============================================================
    # ОЧЕРЕДЬ / ЛОГ / СТАТУС
    # ============================================================
    def _log(self, msg):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_progress(self, data):
        try:
            self.progress_bar["value"] = max(0, min(float(data.get("percent", "0")), 100))
        except Exception:
            self.progress_bar["value"] = 0
        self.progress_info.config(text=f"Прогресс: {data.get('eta', '')}")

    def _update_status_bar(self, data):
        elapsed = data.get("elapsed", 0)
        self.status_label.configure(
            text=f"Всего: {data.get('total', 0)}  |  Скачано: {data.get('downloaded', 0)}  |  "
                 f"Дубликаты: {data.get('duplicates', 0)}  |  Ошибки: {data.get('failed', 0)}  |  "
                 f"Время: {int(elapsed // 60)}м {int(elapsed % 60)}с")

    def _add_track_card(self, data):
        """Карточки треков — сеткой 4 в ряд (строкой, а не колонкой)"""
        cols = 4
        col = self._card_count % cols
        row = self._card_count // cols
        self._card_count += 1

        self.tracks_inner.columnconfigure(col, weight=1, uniform="cards")

        card = tk.Frame(self.tracks_inner, bg=C_CARD)
        card.grid(row=row, column=col, padx=4, pady=4, sticky="nsew")

        inner = tk.Frame(card, bg=C_CARD, padx=8, pady=6)
        inner.pack(fill="both", expand=True)

        tk.Label(inner, text="🎵", bg=C_CARD,
                 fg=SOURCES.get(data.get('source'), {}).get('color', '#888888'),
                 font=("Segoe UI", 14)).pack(side="left", padx=(0, 8))
        info = tk.Frame(inner, bg=C_CARD)
        info.pack(side="left", fill="x", expand=True)
        tk.Label(info, text=data.get('title', ''), bg=C_CARD, fg=C_TEXT,
                 font=("Segoe UI", 10, "bold"), anchor="w",
                 wraplength=150, justify="left").pack(anchor="w")
        tk.Label(info, text=data.get('artist', ''), bg=C_CARD, fg=C_TEXT2,
                 font=("Segoe UI", 8), anchor="w",
                 wraplength=150, justify="left").pack(anchor="w")

    def _process_queue(self):
        try:
            while True:
                msg = self.gui_queue.get_nowait()
                if isinstance(msg, tuple) and len(msg) == 2:
                    t, d = msg
                    if t == "log":
                        self._log(d)
                    elif t == "progress":
                        self._set_progress(d)
                    elif t == "status":
                        self._update_status_bar(d)
                    elif t == "track_card":
                        self._add_track_card(d)
                    elif t == "refresh_playlists":
                        self._refresh_playlists_list()
                    elif t == "done":
                        self._restore_ui()
        except queue.Empty:
            pass
        self.after(100, self._process_queue)

    # ============================================================
    # ПРОВЕРКИ / АВТО-ОБНОВЛЕНИЕ / АВТО-СИНХРОНИЗАЦИЯ
    # ============================================================
    def _initial_checks(self):
        self._log("🌐 Проверка системы...")
        self._log("✅ Интернет OK" if check_internet() else "⚠ Нет интернета")
        self._log("✅ FFmpeg OK" if find_ffmpeg() else "⚠ FFmpeg не найден!")
        self._log("✅ Mutagen: готов" if HAS_MUTAGEN else "⚠ pip install mutagen")
        self._log("✅ Pillow: готов" if HAS_PIL else "⚠ pip install Pillow")
        self._log("✅ Drag & Drop: готов" if HAS_DND else "💡 pip install windnd")

        # Фишка 14: авто-обновление yt-dlp в фоне
        if self.settings.get("auto_update_ytdlp"):
            threading.Thread(target=self._auto_update, daemon=True).start()

        # Фишка 15: автосинхронизация при старте
        if self.settings.get("sync_enabled"):
            threading.Thread(target=self._auto_sync, daemon=True).start()

    def _auto_update(self):
        ok, msg = update_ytdlp()
        self.gui_queue.put(("log", f"🔄 yt-dlp: {msg}" if ok else f"⚠ yt-dlp: {msg}"))

    def _auto_sync(self):
        playlists = DB.get_playlists()
        if not playlists:
            return
        disk = self.settings.get("sync_disk", "")
        self.gui_queue.put(("log", f"🔄 Автосинхронизация: плейлистов — {len(playlists)}"))
        for p in playlists:
            root = f"{disk}\\Music" if disk and os.path.exists(disk) else str(Path(p[4]).parent)
            self._launch_download(p[2], p[3], root)

    # ============================================================
    # ЗАГРУЗКА
    # ============================================================
    def _launch_download(self, url, source, target_root):
        for widget in self.tracks_inner.winfo_children():
            widget.destroy()
        self._card_count = 0
        self.download_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.open_folder_btn.configure(state="disabled")
        self.progress_bar["value"] = 0
        self.status_label.configure(text="⏳ Загрузка...")

        quality = self.quality_var.get()
        workers = self.settings.get("max_workers", 3)
        self.current_target_folder = target_root
        self.downloader = Downloader(self.gui_queue, self.settings, max_workers=workers)
        threading.Thread(target=self.downloader.download,
                         args=(url, "playlist", target_root, quality, source),
                         daemon=True).start()

    def _start_download(self):
        url = self.url_entry.get().strip()
        if not url:
            self._log("❌ Введите ссылку")
            return
        disk = self.disk_var.get()
        if not disk or "❌" in disk:
            self._log("❌ Выберите диск")
            return
        if not os.path.exists(disk):
            self._log(f"❌ Диск {disk} недоступен")
            return

        quality = self.quality_var.get()
        source = self.source_var.get()
        self.settings.update({"download_disk": disk, "quality": quality, "source": source})
        save_settings(self.settings)

        if not disk.endswith("\\"):
            disk += "\\"
        target = os.path.join(disk, "Music")

        self._log(f"\n💾 Диск: {disk} | 🎵 {quality} кбит/с | 🌐 {SOURCES[source]['name']}")
        self._launch_download(url, source, target)

    def _cancel_download(self):
        if self.downloader:
            self.downloader.stop()
            self._log("⏹ Отмена...")

    def _restore_ui(self):
        self.download_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        if self.current_target_folder and os.path.exists(self.current_target_folder):
            self.open_folder_btn.configure(state="normal")
        self._refresh_playlists_list()
        self.status_label.configure(text="✓ Готово")

    def _open_folder(self):
        if not self.current_target_folder or not os.path.exists(self.current_target_folder):
            self._log("⚠ Папка не найдена")
            return
        try:
            os.startfile(self.current_target_folder)
        except Exception as e:
            self._log(f"❌ Ошибка: {e}")