"""Swedish desktop application and isolated scraper worker (no web server)."""

from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

from .config import load_config
from .interface import configure, parse_locations, requires_openai


def resources() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))


def data_directory() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share"))
    legacy = base / "Kommunutredaren"
    path = legacy if legacy.is_dir() else base / "Utredningsstödet"
    path.mkdir(parents=True, exist_ok=True)
    return path


def worker_command(arguments: list[str], log: Path) -> list[str]:
    prefix = [sys.executable]
    if not getattr(sys, "frozen", False):
        prefix += ["-m", "municipal_research.desktop"]
    return prefix + ["--worker-log", str(log), "--worker"] + arguments


def effective_discovery(key: str, requested: str) -> str:
    return requested if key.strip() else "crawl"


def write_locations(path: Path, locations) -> None:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["id", "name", "kind", "domains", "seeds", "portal_hosts", "public_endpoints"])
    for item in locations:
        writer.writerow(
            [item.id, item.name, item.kind, ";".join(item.domains), ";".join(item.seeds), ";".join(item.portal_hosts), ";".join(item.public_endpoints)]
        )
    path.write_text(stream.getvalue(), encoding="utf-8")


def open_path(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


class Desktop:
    def __init__(self, root):
        import tkinter as tk
        from tkinter import ttk

        self.tk, self.ttk, self.root = tk, ttk, root
        self.process = None
        self.job = None
        self.run = None
        self.base = load_config(resources() / "examples" / "interface.yaml")
        self.locations = []
        for name in ("municipalities.csv", "regions.csv"):
            self.locations += parse_locations(
                (resources() / "examples" / name).read_text(encoding="utf-8")
            )
        root.title("Utredningsstödet")
        root.geometry("1000x800")
        root.minsize(800, 650)
        root.protocol("WM_DELETE_WINDOW", self.close)
        outer = ttk.Frame(root, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Utredningsstödet", font=("Segoe UI", 20, "bold")).pack(anchor="w")
        ttk.Label(outer, text="Sök i kommuners och regioners officiella webbplatser.").pack(
            anchor="w", pady=(0, 12)
        )
        self.tabs = ttk.Notebook(outer)
        self.tabs.pack(fill="both", expand=True)
        self.search = ttk.Frame(self.tabs, padding=12)
        self.settings = ttk.Frame(self.tabs, padding=12)
        self.results = ttk.Frame(self.tabs, padding=12)
        for frame, label in (
            (self.search, "Sökning"),
            (self.settings, "Inställningar"),
            (self.results, "Resultat och logg"),
        ):
            self.tabs.add(frame, text=label)
        self.mode = tk.StringVar(value="Demo – inga nätanrop")
        ttk.Combobox(
            self.search,
            textvariable=self.mode,
            values=["Demo – inga nätanrop", "Verklig sökning"],
            state="readonly",
        ).pack(fill="x")
        ttk.Label(
            self.search,
            text="Demo använder en fast fråga och påhittade organisationer; dina sökinställningar används vid Verklig sökning.",
            wraplength=850,
        ).pack(anchor="w", pady=6)
        ttk.Label(self.search, text="Utredningsfråga").pack(anchor="w", pady=(10, 0))
        self.question = tk.Text(self.search, height=3, wrap="word")
        self.question.insert("1.0", self.base.research.question)
        self.question.pack(fill="x")
        self.keywords = self.entry(
            self.search,
            "Sökord (kommatecken mellan orden)",
            ", ".join(self.base.discovery.keywords),
        )
        line = ttk.Frame(self.search)
        line.pack(fill="x", pady=(12, 4))
        self.scope = tk.StringVar(value="Kommuner")
        scope = ttk.Combobox(
            line,
            textvariable=self.scope,
            values=["Kommuner", "Regioner", "Kommuner och regioner"],
            state="readonly",
            width=25,
        )
        scope.pack(side="left")
        scope.bind("<<ComboboxSelected>>", lambda event: self.refresh_locations())
        self.all_locations = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            line,
            text="Alla i vald grupp",
            variable=self.all_locations,
            command=self.selection_state,
        ).pack(side="left", padx=12)
        ttk.Label(
            self.search,
            text="Markera en eller flera organisationer (Ctrl/klick). En region avser regionens egen verksamhet.",
        ).pack(anchor="w")
        self.listbox = tk.Listbox(
            self.search, selectmode="extended", exportselection=False, height=11
        )
        self.listbox.pack(fill="both", expand=True, pady=6)
        self.refresh_locations()
        self.listbox.selection_set(0)
        self.entry_hint = ttk.Label(
            self.search, text="Börja gärna med en organisation och fem dokument."
        )
        self.entry_hint.pack(anchor="w")
        self.ai = tk.StringVar(value="ollama")
        ttk.Label(self.settings, text="AI för analys").pack(anchor="w")
        ttk.Combobox(
            self.settings, textvariable=self.ai, values=["ollama", "openai"], state="readonly"
        ).pack(fill="x")
        ttk.Label(
            self.settings,
            text="Ollama och modellen måste vara installerade lokalt. Inga modeller laddas ned av appen.",
        ).pack(anchor="w")
        self.key = self.entry(self.settings, "OpenAI API-nyckel (sparas inte)", "", show="*")
        self.key.trace_add("write", lambda *args: self.update_discovery())
        self.model = self.entry(self.settings, "Lokal modell i Ollama", "llama3.1:8b")
        self.url = self.entry(self.settings, "Ollama-adress", "http://localhost:11434")
        self.discovery = tk.StringVar(value="Genomsök webbplatser")
        ttk.Label(self.settings, text="Sökmetod").pack(anchor="w", pady=(10, 0))
        self.discovery_box = ttk.Combobox(
            self.settings,
            textvariable=self.discovery,
            values=["Genomsök webbplatser", "Webbsökning", "Båda"],
            state="disabled",
        )
        self.discovery_box.pack(fill="x")
        ttk.Label(
            self.settings, text="Utan API-nyckel används Genomsök webbplatser automatiskt."
        ).pack(anchor="w")
        self.collect = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            self.settings, text="Samla bara dokument – ingen AI-analys", variable=self.collect
        ).pack(anchor="w", pady=8)
        self.documents = self.entry(self.settings, "Högsta antal hämtningsförsök per organisation", "5")
        self.calls = self.entry(self.settings, "Högsta antal AI-anrop totalt", "50")
        self.queries = self.entry(
            self.settings, "Sökfråga för webbsökning", "{location} {question}"
        )
        ttk.Button(
            self.settings, text="Läs egen organisationslista (CSV)", command=self.load_csv
        ).pack(anchor="w", pady=8)
        ttk.Label(self.settings, text="Resultat sparas här:").pack(anchor="w")
        ttk.Label(self.settings, text=str(data_directory()), wraplength=700).pack(anchor="w")
        ttk.Button(
            self.settings, text="Öppna resultatmapp", command=lambda: open_path(data_directory())
        ).pack(anchor="w", pady=8)
        ttk.Label(
            self.results,
            text="Demo visar syntetiska exempel. Verkliga fynd kräver kontroll av källorna.",
        ).pack(anchor="w")
        self.table = ttk.Treeview(
            self.results, columns=("name", "category", "review"), show="headings", height=7
        )
        for col, title in (
            ("name", "Organisation"),
            ("category", "Bedömning"),
            ("review", "Behöver granskas"),
        ):
            self.table.heading(col, text=title)
        self.table.pack(fill="x", pady=8)
        self.log = tk.Text(self.results, wrap="word", state="disabled", height=12)
        self.log.pack(fill="both", expand=True)
        self.excel = ttk.Button(
            self.results, text="Öppna Excel-resultat", command=self.open_excel, state="disabled"
        )
        self.excel.pack(anchor="w", pady=8)
        bottom = ttk.Frame(outer)
        bottom.pack(fill="x", pady=(12, 0))
        self.start_button = ttk.Button(bottom, text="Starta sökning", command=self.start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(bottom, text="Stoppa", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        self.status = tk.StringVar(
            value="Redo. Välj Demo för att prova utan nyckel eller lokal AI."
        )
        ttk.Label(bottom, textvariable=self.status, wraplength=600).pack(side="left", padx=8)
        root.after(400, self.poll)

    def entry(self, parent, label, value, **kwargs):
        self.ttk.Label(parent, text=label).pack(anchor="w", pady=(8, 0))
        variable = self.tk.StringVar(value=value)
        self.ttk.Entry(parent, textvariable=variable, **kwargs).pack(fill="x")
        return variable

    def refresh_locations(self):
        scope = self.scope.get()
        self.visible = [
            item
            for item in self.locations
            if scope == "Kommuner och regioner"
            or item.kind == ("kommun" if scope == "Kommuner" else "region")
        ]
        self.listbox.delete(0, "end")
        for item in self.visible:
            self.listbox.insert("end", f"{item.name} ({item.kind})")

    def selection_state(self):
        self.listbox.configure(state="disabled" if self.all_locations.get() else "normal")

    def update_discovery(self):
        if not self.key.get().strip():
            self.discovery.set("Genomsök webbplatser")
        self.discovery_box.configure(state="readonly" if self.key.get().strip() else "disabled")

    def load_csv(self):
        from tkinter import filedialog, messagebox

        path = filedialog.askopenfilename(filetypes=[("Organisationslista", "*.csv")])
        if path:
            try:
                locations = parse_locations(Path(path).read_text(encoding="utf-8-sig"))
                self.locations = locations
                self.refresh_locations()
            except Exception as error:
                messagebox.showerror("Kunde inte läsa listan", str(error))

    def start(self):
        from tkinter import messagebox

        if self.process is not None:
            return
        try:
            demo = self.mode.get().startswith("Demo")
            chosen = (
                self.visible
                if self.all_locations.get()
                else [self.visible[i] for i in self.listbox.curselection()]
            )
            provider = effective_discovery(
                self.key.get(),
                {"Genomsök webbplatser": "crawl", "Webbsökning": "openai", "Båda": "hybrid"}[
                    self.discovery.get()
                ],
            )
            if not demo and not chosen:
                raise ValueError("Välj minst en kommun eller region.")
            if (
                not demo
                and requires_openai(provider, self.ai.get(), self.collect.get())
                and not self.key.get().strip()
            ):
                raise ValueError(
                    "Ange en OpenAI-nyckel, välj Ollama eller välj att bara samla dokument."
                )
            config = (
                self.base
                if demo
                else configure(
                    self.base,
                    self.question.get("1.0", "end"),
                    self.keywords.get(),
                    self.queries.get(),
                    provider,
                    int(self.documents.get()),
                    int(self.calls.get()),
                    ai_provider=self.ai.get(),
                    local_model=self.model.get(),
                    local_url=self.url.get(),
                    context_tokens=32768,
                    local_timeout=600,
                )
            )
            job = data_directory() / (
                datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
            )
            job.mkdir()
            import yaml

            (job / "config.yaml").write_text(
                yaml.safe_dump(config.model_dump(mode="json"), allow_unicode=True), encoding="utf-8"
            )
            write_locations(job / "locations.csv", chosen)
            run = job / "resultat"
            if demo:
                arguments = [
                    "demo",
                    "--config",
                    str(resources() / "examples" / "language_requirements.yaml"),
                ]
            else:
                arguments = [
                    "run",
                    "--config",
                    str(job / "config.yaml"),
                    "--locations",
                    str(job / "locations.csv"),
                ]
                if self.collect.get():
                    arguments += ["--collect-only"]
            arguments += ["--run-dir", str(run), "--cache-dir", str(job / "cache")]
            env = os.environ.copy()
            env["OPENAI_API_KEY"] = self.key.get().strip()
            env["PYTHONUTF8"] = "1"
            self.process = subprocess.Popen(
                worker_command(arguments, job / "worker.log"),
                env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                cwd=resources(),
            )
            self.job, self.run = job, run
            self.stopped = False
            self.start_button.configure(state="disabled")
            self.stop_button.configure(state="normal")
            self.excel.configure(state="disabled")
            self.table.delete(*self.table.get_children())
            self.status.set(
                "Demo körs – syntetiska exempel."
                if demo
                else f"Sökning pågår för {len(chosen)} organisationer …"
            )
            self.log.configure(state="normal")
            self.log.delete("1.0", "end")
            self.log.configure(state="disabled")
            self.tabs.select(self.results)
            self.tabs.tab(self.search, state="disabled")
            self.tabs.tab(self.settings, state="disabled")
        except Exception as error:
            messagebox.showerror("Sökningen kunde inte starta", str(error))

    def poll(self):
        if self.process is not None:
            log = self.job / "worker.log"
            pipeline_log = self.run / "pipeline.log"
            if log.exists() or pipeline_log.exists():
                self.log.configure(state="normal")
                self.log.delete("1.0", "end")
                content = ""
                for path in (pipeline_log, log):
                    if path.exists():
                        content += path.read_text(encoding="utf-8", errors="replace")[-60000:]
                self.log.insert("end", content[-60000:])
                self.log.see("end")
                self.log.configure(state="disabled")
            code = self.process.poll()
            if code is not None:
                self.process = None
                self.tabs.tab(self.search, state="normal")
                self.tabs.tab(self.settings, state="normal")
                self.start_button.configure(state="normal")
                self.stop_button.configure(state="disabled")
                self.status.set(
                    "Stoppad. Delresultat finns i resultatmappen."
                    if self.stopped
                    else {
                        0: "Klar. Kontrollera fynden mot källorna.",
                        2: "Klar med luckor – granska resultat och logg.",
                    }.get(code, "Körningen misslyckades. Läs loggen.")
                )
                result = self.run / "results.json"
                if result.exists():
                    try:
                        data = json.loads(result.read_text(encoding="utf-8"))
                        rows = data if isinstance(data, list) else data.get("summary", [])
                        for row in rows:
                            self.table.insert(
                                "",
                                "end",
                                values=(
                                    row.get("municipality", ""),
                                    row.get("category", ""),
                                    "Ja" if row.get("needs_review") else "Nej",
                                ),
                            )
                    except (ValueError, AttributeError):
                        self.status.set(
                            "Körningen är avslutad. Öppna resultatmappen för att granska filerna."
                        )
                if (self.run / "results.xlsx").exists():
                    self.excel.configure(state="normal")
        self.root.after(400, self.poll)

    def stop(self):
        if self.process is not None:
            self.stopped = True
            try:
                self.process.terminate()
            except OSError:
                pass
            self.root.after(2000, lambda process=self.process: self.kill_if_running(process))
            self.status.set("Stoppar …")

    def kill_if_running(self, process):
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass

    def open_excel(self):
        if self.run:
            open_path(self.run / "results.xlsx")

    def close(self):
        from tkinter import messagebox

        if self.process is not None:
            if not messagebox.askyesno(
                "Avsluta?", "En sökning pågår. Stoppa sökningen och stäng programmet?"
            ):
                return
            try:
                self.process.kill()
                self.process.wait(timeout=5)
            except OSError:
                pass
        self.root.destroy()


def main() -> int:
    if "--worker" in sys.argv:
        index = sys.argv.index("--worker")
        log_index = sys.argv.index("--worker-log")
        with Path(sys.argv[log_index + 1]).open("w", encoding="utf-8", buffering=1) as log:
            sys.stdout = sys.stderr = log
            from .cli import main as cli_main

            return cli_main(sys.argv[index + 1 :])
    import tkinter as tk

    root = tk.Tk()
    Desktop(root)
    if "--smoke-test" in sys.argv:
        root.after(500, root.destroy)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
