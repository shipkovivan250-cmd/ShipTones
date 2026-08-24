import tkinter as tk

# ============================================================
# ВСПЛЫВАЮЩАЯ ПОДСКАЗКА — белый «пузырь» с текстом (как в One UI)
# ============================================================
class Tooltip:
    def __init__(self, widget, text, delay=350):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.tipwin = None
        self._after = None
        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")
        widget.bind("<ButtonPress>", self._on_leave, add="+")

    def _on_enter(self, event=None):
        self._cancel()
        self._after = self.widget.after(self.delay, self._show)

    def _on_leave(self, event=None):
        self._cancel()

    def _cancel(self):
        if self._after:
            try:
                self.widget.after_cancel(self._after)
            except Exception:
                pass
            self._after = None
        if self.tipwin:
            self.tipwin.destroy()
            self.tipwin = None

    def _show(self):
        if self.tipwin or not self.text:
            return
        try:
            tip = tk.Toplevel(self.widget)
        except Exception:
            return
        tip.wm_overrideredirect(True)
        tip.wm_attributes("-topmost", True)

        trans = "#010101"
        tip.configure(bg=trans)
        has_transparency = False
        try:
            tip.wm_attributes("-transparentcolor", trans)
            has_transparency = True
        except Exception:
            pass

        # 1) измеряем текст
        measure = tk.Label(tip, text=self.text, font=("Segoe UI", 9),
                           justify="left", wraplength=280, padx=14, pady=12)
        measure.pack()
        tip.update_idletasks()
        w = measure.winfo_reqwidth() + 2
        h = measure.winfo_reqheight() + 2
        measure.destroy()

        # 2) позиция (не вылезая за экран)
        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        sw = tip.winfo_screenwidth()
        sh = tip.winfo_screenheight()
        if x + w > sw - 8:
            x = max(8, sw - w - 8)
        if y + h > sh - 8:
            y = self.widget.winfo_rooty() - h - 8
        tip.geometry(f"{w}x{h}+{x}+{y}")

        # 3) скруглённый пузырь
        if has_transparency:
            canvas = tk.Canvas(tip, width=w, height=h, bg=trans, highlightthickness=0)
            canvas.pack()
            r = 10
            fill = "#fdfdfd"
            canvas.create_rectangle(r, 0, w - r, h, fill=fill, outline=fill)
            canvas.create_rectangle(0, r, w, h - r, fill=fill, outline=fill)
            canvas.create_oval(0, 0, 2*r, 2*r, fill=fill, outline=fill)
            canvas.create_oval(w-2*r, 0, w, 2*r, fill=fill, outline=fill)
            canvas.create_oval(0, h-2*r, 2*r, h, fill=fill, outline=fill)
            canvas.create_oval(w-2*r, h-2*r, w, h, fill=fill, outline=fill)

        # 4) ТЕКСТ создаётся ПОСЛЕ холста + lift() — иначе пузырь его закрывает
        label = tk.Label(tip, text=self.text, font=("Segoe UI", 9),
                         fg="#6b6b6b", bg="#fdfdfd", justify="left",
                         wraplength=280, padx=14, pady=12)
        label.place(x=1, y=1)
        label.lift()

        self.tipwin = tip