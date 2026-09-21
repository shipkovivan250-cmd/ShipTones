import multiprocessing
import traceback
import tkinter as tk
from tkinter import messagebox

from config import BASE_DIR, logger

def main():
    import webapp
    webapp.main()

if __name__ == "__main__":
    multiprocessing.freeze_support()
    try:
        main()
    except Exception as e:
        logger.critical(f"Fatal crash: {traceback.format_exc()}")
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("ShipTones Crash", f"Критическая ошибка:\n{e}\n\nПодробности в shiptones.log")
            root.destroy()
        except Exception:
            pass