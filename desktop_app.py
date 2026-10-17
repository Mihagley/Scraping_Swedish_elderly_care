"""PyInstaller entry point for the native desktop application."""

import multiprocessing
import sys
import traceback
from pathlib import Path

from municipal_research.desktop import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    try:
        code = main()
    except Exception:
        details = traceback.format_exc()
        Path("desktop-error.log").write_text(details, encoding="utf-8")
        if "--smoke-test" not in sys.argv and "--worker" not in sys.argv:
            from tkinter import messagebox

            messagebox.showerror("Utredningsstödet", details)
        code = 1
    raise SystemExit(code)
