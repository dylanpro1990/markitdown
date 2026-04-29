from __future__ import annotations

import os
import queue
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

if __package__ in (None, ""):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from markitdown import MarkItDown
else:
    from . import MarkItDown

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    DND_AVAILABLE = True
except Exception:
    DND_FILES = None
    TkinterDnD = None
    DND_AVAILABLE = False


SUPPORTED_EXTENSIONS = (
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
    ".xls",
    ".html",
    ".htm",
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".xml",
    ".epub",
    ".zip",
    ".msg",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".bmp",
    ".webp",
    ".mp3",
    ".wav",
    ".m4a",
    ".ipynb",
)


@dataclass
class InputItem:
    source: str
    display_name: str
    is_url: bool


@dataclass
class ConversionResult:
    source: str
    display_name: str
    output: Path
    success: bool
    message: str


def _split_drop_files(raw: str) -> list[Path]:
    raw = raw.strip()
    if not raw:
        return []

    paths: list[Path] = []
    current: list[str] = []
    in_braces = False
    for char in raw:
        if char == "{":
            in_braces = True
            continue
        if char == "}":
            in_braces = False
            continue
        if char == " " and not in_braces:
            if current:
                paths.append(Path("".join(current)))
                current = []
            continue
        current.append(char)

    if current:
        paths.append(Path("".join(current)))
    return paths


def _youtube_output_name(url: str) -> str:
    parsed = urlparse(url)
    video_id = parse_qs(parsed.query).get("v", [""])[0]
    if not video_id:
        video_id = parsed.path.rstrip("/").split("/")[-1]
    video_id = re.sub(r"[^A-Za-z0-9_-]+", "_", video_id or "youtube")
    return f"youtube_{video_id}.md"


class MarkItDownDesktopApp:
    def __init__(self) -> None:
        root_class = TkinterDnD.Tk if DND_AVAILABLE else tk.Tk
        self.root = root_class()
        self.root.title("MarkItDown Portable")
        self.root.geometry("880x640")
        self.root.minsize(760, 540)

        self.converter = MarkItDown()
        self.items: list[InputItem] = []
        self.output_dir = tk.StringVar(value=str(Path.cwd() / "converted_markdown"))
        self.youtube_url = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready")
        self.progress_var = tk.DoubleVar(value=0.0)
        self.result_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.is_converting = False

        self._build_ui()
        self.root.after(150, self._poll_queue)

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=18)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(3, weight=1)
        container.rowconfigure(6, weight=1)

        title = ttk.Label(
            container, text="MarkItDown Portable", font=("Segoe UI", 24, "bold")
        )
        title.grid(row=0, column=0, sticky="w")

        intro = (
            "Drop files here, choose them manually, or add a YouTube watch URL. "
            "Converted Markdown files are saved to the output folder."
        )
        if DND_AVAILABLE:
            intro += " Drag and drop is enabled."
        else:
            intro += " Drag and drop is unavailable, but file picking still works."
        ttk.Label(container, text=intro).grid(row=1, column=0, sticky="w", pady=(6, 14))

        drop_frame = ttk.LabelFrame(container, text="Files", padding=12)
        drop_frame.grid(row=2, column=0, sticky="nsew")
        drop_frame.columnconfigure(0, weight=1)
        drop_frame.rowconfigure(1, weight=1)

        self.drop_label = tk.Label(
            drop_frame,
            text="Drop files here\nor use the buttons below",
            bg="#f3f6fb",
            fg="#24324a",
            relief="groove",
            bd=2,
            font=("Segoe UI", 14),
            height=5,
        )
        self.drop_label.grid(row=0, column=0, sticky="ew")
        if DND_AVAILABLE:
            self.drop_label.drop_target_register(DND_FILES)
            self.drop_label.dnd_bind("<<Drop>>", self._handle_drop)

        file_btns = ttk.Frame(drop_frame)
        file_btns.grid(row=1, column=0, sticky="ew", pady=(12, 8))
        ttk.Button(file_btns, text="Choose Files", command=self._choose_files).pack(
            side="left"
        )
        ttk.Button(file_btns, text="Add YouTube URL", command=self._add_youtube_url).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(file_btns, text="Clear List", command=self._clear_files).pack(
            side="left", padx=(8, 0)
        )

        url_row = ttk.Frame(drop_frame)
        url_row.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        url_row.columnconfigure(0, weight=1)
        ttk.Entry(url_row, textvariable=self.youtube_url).grid(
            row=0, column=0, sticky="ew"
        )
        ttk.Button(url_row, text="Paste URL and Add", command=self._add_youtube_url).grid(
            row=0, column=1, padx=(8, 0)
        )

        self.file_list = tk.Listbox(drop_frame, height=8, font=("Consolas", 10))
        self.file_list.grid(row=3, column=0, sticky="nsew")

        output_frame = ttk.LabelFrame(container, text="Output", padding=12)
        output_frame.grid(row=4, column=0, sticky="ew", pady=(14, 0))
        output_frame.columnconfigure(0, weight=1)

        ttk.Entry(output_frame, textvariable=self.output_dir).grid(
            row=0, column=0, sticky="ew"
        )
        ttk.Button(
            output_frame,
            text="Choose Output Folder",
            command=self._choose_output_dir,
        ).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(
            output_frame,
            text="Open Output Folder",
            command=self._open_output_dir,
        ).grid(row=0, column=2, padx=(8, 0))

        action_frame = ttk.Frame(container)
        action_frame.grid(row=5, column=0, sticky="ew", pady=(14, 8))
        action_frame.columnconfigure(0, weight=1)

        self.convert_button = ttk.Button(
            action_frame,
            text="Convert to Markdown",
            command=self._start_conversion,
        )
        self.convert_button.grid(row=0, column=0, sticky="ew")

        self.progress_bar = ttk.Progressbar(
            action_frame,
            variable=self.progress_var,
            maximum=100,
            mode="determinate",
        )
        self.progress_bar.grid(row=1, column=0, sticky="ew", pady=(10, 0))

        log_frame = ttk.LabelFrame(container, text="Log", padding=12)
        log_frame.grid(row=6, column=0, sticky="nsew", pady=(10, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log_text = tk.Text(log_frame, height=12, wrap="word", font=("Consolas", 10))
        self.log_text.grid(row=0, column=0, sticky="nsew")
        self.log_text.configure(state="disabled")

        status_bar = ttk.Label(container, textvariable=self.status_var, anchor="w")
        status_bar.grid(row=7, column=0, sticky="ew", pady=(10, 0))

    def _append_log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _add_files(self, incoming: list[Path]) -> None:
        existing_files = {
            item.source for item in self.items if not item.is_url and Path(item.source).exists()
        }
        added = 0
        for path in incoming:
            if not path.exists() or not path.is_file():
                continue
            if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                self._append_log(f"Skipped unsupported file: {path}")
                continue
            resolved = str(path.resolve())
            if resolved in existing_files:
                continue
            existing_files.add(resolved)
            self.items.append(
                InputItem(source=resolved, display_name=path.name, is_url=False)
            )
            self.file_list.insert("end", f"FILE  {path}")
            added += 1

        if added:
            self.status_var.set(f"Added {added} file(s)")
        elif incoming:
            self.status_var.set("No new files were added")

    def _choose_files(self) -> None:
        filenames = filedialog.askopenfilenames(title="Choose files to convert")
        self._add_files([Path(name) for name in filenames])

    def _add_youtube_url(self) -> None:
        raw_url = self.youtube_url.get().strip()
        if not raw_url:
            messagebox.showwarning("No URL", "Paste a YouTube watch URL first.")
            return
        if not raw_url.startswith("https://www.youtube.com/watch?"):
            messagebox.showwarning(
                "Unsupported URL",
                "Please use a full YouTube watch URL like https://www.youtube.com/watch?v=...",
            )
            return
        existing_urls = {item.source for item in self.items if item.is_url}
        if raw_url in existing_urls:
            self.status_var.set("This YouTube URL is already in the list")
            return

        self.items.append(
            InputItem(source=raw_url, display_name=raw_url, is_url=True)
        )
        self.file_list.insert("end", f"YOUTUBE  {raw_url}")
        self.youtube_url.set("")
        self.status_var.set("Added 1 YouTube URL")

    def _handle_drop(self, event: object) -> None:
        data = getattr(event, "data", "")
        self._add_files(_split_drop_files(data))

    def _clear_files(self) -> None:
        if self.is_converting:
            return
        self.items.clear()
        self.file_list.delete(0, "end")
        self.progress_var.set(0)
        self.status_var.set("File list cleared")

    def _choose_output_dir(self) -> None:
        directory = filedialog.askdirectory(
            title="Choose output folder for Markdown files"
        )
        if directory:
            self.output_dir.set(directory)

    def _open_output_dir(self) -> None:
        output_path = Path(self.output_dir.get())
        output_path.mkdir(parents=True, exist_ok=True)
        os.startfile(str(output_path))

    def _set_busy(self, busy: bool) -> None:
        self.is_converting = busy
        state = "disabled" if busy else "normal"
        self.convert_button.configure(state=state)

    def _start_conversion(self) -> None:
        if self.is_converting:
            return
        if not self.items:
            messagebox.showwarning("No Inputs", "Please add files or a YouTube URL before converting.")
            return

        output_path = Path(self.output_dir.get()).expanduser()
        output_path.mkdir(parents=True, exist_ok=True)

        self.progress_var.set(0)
        self._set_busy(True)
        self.status_var.set("Converting...")
        self._append_log("=" * 60)
        self._append_log(f"Starting conversion for {len(self.items)} item(s)")

        worker = threading.Thread(
            target=self._convert_worker,
            args=(list(self.items), output_path, self.result_queue.put),
            daemon=True,
        )
        worker.start()

    def _convert_worker(
        self,
        items: list[InputItem],
        output_dir: Path,
        emit: Callable[[tuple[str, object]], None],
    ) -> None:
        results: list[ConversionResult] = []

        for index, item in enumerate(items, start=1):
            try:
                result = self.converter.convert(item.source)
                if item.is_url:
                    output_path = output_dir / _youtube_output_name(item.source)
                else:
                    output_path = output_dir / f"{Path(item.source).stem}.md"
                output_path.write_text(result.markdown, encoding="utf-8")
                conversion = ConversionResult(
                    source=item.source,
                    display_name=item.display_name,
                    output=output_path,
                    success=True,
                    message=f"[{index}/{len(items)}] Success: {item.display_name} -> {output_path.name}",
                )
            except Exception as exc:
                failed_output = (
                    output_dir / _youtube_output_name(item.source)
                    if item.is_url
                    else output_dir / f"{Path(item.source).stem}.md"
                )
                conversion = ConversionResult(
                    source=item.source,
                    display_name=item.display_name,
                    output=failed_output,
                    success=False,
                    message=f"[{index}/{len(items)}] Failed: {item.display_name} -> {exc}",
                )

            results.append(conversion)
            emit(("progress", (index, len(items), conversion)))

        emit(("done", results))

    def _poll_queue(self) -> None:
        try:
            while True:
                event_type, payload = self.result_queue.get_nowait()
                if event_type == "progress":
                    index, total, conversion = payload
                    self.progress_var.set(index / total * 100)
                    self.status_var.set(
                        f"Processing {conversion.display_name} ({index}/{total})"
                    )
                    self._append_log(conversion.message)
                elif event_type == "done":
                    self._finish_conversion(payload)
        except queue.Empty:
            pass
        finally:
            self.root.after(150, self._poll_queue)

    def _finish_conversion(self, results: list[ConversionResult]) -> None:
        self._set_busy(False)
        success_count = sum(1 for item in results if item.success)
        failure_count = len(results) - success_count
        self.status_var.set(f"Done: {success_count} succeeded, {failure_count} failed")
        self._append_log(f"Finished. Success: {success_count}, Failed: {failure_count}.")

        if failure_count:
            messagebox.showwarning(
                "Conversion Finished",
                f"Success: {success_count}\nFailed: {failure_count}\nSee the log for details.",
            )
        else:
            messagebox.showinfo(
                "Conversion Finished",
                f"All files converted successfully.\nOutput folder: {self.output_dir.get()}",
            )

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    app = MarkItDownDesktopApp()
    app.run()


if __name__ == "__main__":
    main()
