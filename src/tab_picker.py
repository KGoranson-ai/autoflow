"""
TabPicker — Drag-and-drop UI for assigning browser tabs to form sessions.

Layout:
  Left panel:  Live list of open browser tabs (title + URL).
               "Refresh Tabs" button re-reads tabs without closing panel.

  Right panel: Drop zones — one per CSV batch / form slot.
               User drags a tab card from the left onto a drop zone.
               Each drop zone shows the assigned tab's title once filled.

  Bottom bar:  "Run All" button (disabled until at least one assignment made).
               Triggers SessionManager.start_all() and opens the progress panel.

Built entirely in Tkinter (consistent with the rest of the app).
Drag-and-drop is implemented manually (tkinter.dnd is unreliable across
platforms) using mouse button press/motion/release bindings.
"""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------
_CLR_TAB_CARD_BG    = "#2C2C2E"
_CLR_TAB_CARD_FG    = "#FFFFFF"
_CLR_TAB_CARD_HOVER = "#3A3A3C"
_CLR_DROP_EMPTY     = "#1C1C1E"
_CLR_DROP_ACTIVE    = "#0A84FF"   # blue highlight when dragging over
_CLR_DROP_FILLED    = "#30D158"   # green when a tab is assigned
_CLR_DROP_BORDER    = "#48484A"
_CLR_BG             = "#1C1C1E"
_CLR_BTN_RUN        = "#0A84FF"
_CLR_BTN_DISABLED   = "#48484A"
_CLR_TEXT_MUTED     = "#8E8E93"
_FONT_TITLE         = ("Helvetica", 13, "bold")
_FONT_BODY          = ("Helvetica", 11)
_FONT_SMALL         = ("Helvetica", 9)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _truncate(text: str, max_len: int = 40) -> str:
    return text if len(text) <= max_len else text[:max_len - 1] + "…"


# ---------------------------------------------------------------------------
# TabCard — a draggable widget representing one open browser tab
# ---------------------------------------------------------------------------

class TabCard(tk.Frame):
    """A draggable card showing a browser tab's title and URL."""

    def __init__(self, parent, tab_info: Dict[str, Any], **kwargs):
        super().__init__(
            parent,
            bg=_CLR_TAB_CARD_BG,
            relief="flat",
            bd=0,
            cursor="hand2",
            **kwargs,
        )
        self.tab_info = tab_info   # {"index": int, "title": str, "url": str}
        self._drag_data: Dict[str, Any] = {}

        title = _truncate(tab_info.get("title", f"Tab {tab_info['index']}"), 36)
        url   = _truncate(tab_info.get("url", ""), 44)

        tk.Label(
            self, text=title, bg=_CLR_TAB_CARD_BG, fg=_CLR_TAB_CARD_FG,
            font=_FONT_BODY, anchor="w",
        ).pack(fill="x", padx=8, pady=(6, 0))
        tk.Label(
            self, text=url, bg=_CLR_TAB_CARD_BG, fg=_CLR_TEXT_MUTED,
            font=_FONT_SMALL, anchor="w",
        ).pack(fill="x", padx=8, pady=(0, 6))

        # Bind drag events on the frame and both labels
        for widget in [self] + list(self.winfo_children()):
            widget.bind("<ButtonPress-1>",   self._on_press)
            widget.bind("<B1-Motion>",       self._on_motion)
            widget.bind("<ButtonRelease-1>", self._on_release)
            widget.bind("<Enter>",           lambda e: self.config(bg=_CLR_TAB_CARD_HOVER))
            widget.bind("<Leave>",           lambda e: self.config(bg=_CLR_TAB_CARD_BG))

    # -- Drag implementation ------------------------------------------------

    def _on_press(self, event):
        self._drag_data = {"x": event.x_root, "y": event.y_root, "dragging": False}
        # Store reference on root so DropZone can read it
        self.winfo_toplevel()._drag_source = self

    def _on_motion(self, event):
        dx = abs(event.x_root - self._drag_data["x"])
        dy = abs(event.y_root - self._drag_data["y"])
        if dx > 4 or dy > 4:
            self._drag_data["dragging"] = True
            self._highlight_drop_zones(event.x_root, event.y_root)

    def _on_release(self, event):
        if self._drag_data.get("dragging"):
            self._drop(event.x_root, event.y_root)
        self._clear_drop_highlights()
        self.winfo_toplevel()._drag_source = None

    def _highlight_drop_zones(self, x_root: int, y_root: int):
        root = self.winfo_toplevel()
        for zone in getattr(root, "_drop_zones", []):
            zone.highlight_if_hovered(x_root, y_root)

    def _clear_drop_highlights(self):
        root = self.winfo_toplevel()
        for zone in getattr(root, "_drop_zones", []):
            zone.clear_highlight()

    def _drop(self, x_root: int, y_root: int):
        root = self.winfo_toplevel()
        for zone in getattr(root, "_drop_zones", []):
            if zone.contains(x_root, y_root):
                zone.accept_drop(self.tab_info)
                return


# ---------------------------------------------------------------------------
# DropZone — a target area that accepts a TabCard drop
# ---------------------------------------------------------------------------

class DropZone(tk.Frame):
    """A slot that accepts a dragged tab card and holds the assignment."""

    def __init__(self, parent, slot_index: int, on_assign: Callable, **kwargs):
        super().__init__(
            parent,
            bg=_CLR_DROP_EMPTY,
            relief="solid",
            bd=1,
            highlightbackground=_CLR_DROP_BORDER,
            highlightthickness=1,
            **kwargs,
        )
        self.slot_index = slot_index
        self._on_assign = on_assign
        self.assigned_tab: Optional[Dict[str, Any]] = None

        self._label = tk.Label(
            self,
            text=f"Form {slot_index}\nDrag a tab here",
            bg=_CLR_DROP_EMPTY,
            fg=_CLR_TEXT_MUTED,
            font=_FONT_BODY,
            justify="center",
        )
        self._label.pack(expand=True, fill="both", padx=8, pady=12)

        # Register with root so TabCard can find all zones
        root = self.winfo_toplevel()
        if not hasattr(root, "_drop_zones"):
            root._drop_zones = []
        root._drop_zones.append(self)

        # Allow clicking an assigned zone to clear it
        self.bind("<Double-Button-1>", self._on_double_click)
        self._label.bind("<Double-Button-1>", self._on_double_click)

    def accept_drop(self, tab_info: Dict[str, Any]) -> None:
        self.assigned_tab = tab_info
        title = _truncate(tab_info.get("title", f"Tab {tab_info['index']}"), 30)
        self._label.config(
            text=f"Form {self.slot_index}\n✓ {title}",
            bg=_CLR_DROP_FILLED,
            fg="#FFFFFF",
        )
        self.config(bg=_CLR_DROP_FILLED, highlightbackground=_CLR_DROP_FILLED)
        self._on_assign(self.slot_index, tab_info)
        logger.debug("DropZone %d: assigned tab %d", self.slot_index, tab_info["index"])

    def clear(self) -> None:
        self.assigned_tab = None
        self._label.config(
            text=f"Form {self.slot_index}\nDrag a tab here",
            bg=_CLR_DROP_EMPTY,
            fg=_CLR_TEXT_MUTED,
        )
        self.config(bg=_CLR_DROP_EMPTY, highlightbackground=_CLR_DROP_BORDER)
        self._on_assign(self.slot_index, None)

    def highlight_if_hovered(self, x_root: int, y_root: int) -> None:
        if self.contains(x_root, y_root):
            self.config(highlightbackground=_CLR_DROP_ACTIVE, highlightthickness=2)
        else:
            self.clear_highlight()

    def clear_highlight(self) -> None:
        color = _CLR_DROP_FILLED if self.assigned_tab else _CLR_DROP_BORDER
        self.config(highlightbackground=color, highlightthickness=1)

    def contains(self, x_root: int, y_root: int) -> bool:
        try:
            x = self.winfo_rootx()
            y = self.winfo_rooty()
            w = self.winfo_width()
            h = self.winfo_height()
            return x <= x_root <= x + w and y <= y_root <= y + h
        except Exception:
            return False

    def _on_double_click(self, event):
        if self.assigned_tab:
            self.clear()


# ---------------------------------------------------------------------------
# ProgressPanel — shown after "Run All" is clicked
# ---------------------------------------------------------------------------

class ProgressPanel(tk.Toplevel):
    """Shows live per-session status after sessions are started."""

    def __init__(self, parent, session_manager, num_sessions: int):
        super().__init__(parent)
        self.title("Multi-Form Progress")
        self.geometry("480x60")
        self.resizable(True, True)
        self.configure(bg=_CLR_BG)
        self._manager = session_manager
        self._rows: Dict[int, Dict[str, tk.Widget]] = {}

        header = tk.Frame(self, bg=_CLR_BG)
        header.pack(fill="x", padx=12, pady=(10, 4))
        for col, text, width in [
            (0, "Session", 6), (1, "Tab", 4), (2, "Progress", 16), (3, "Status", 18)
        ]:
            tk.Label(
                header, text=text, bg=_CLR_BG, fg=_CLR_TEXT_MUTED,
                font=_FONT_SMALL, width=width, anchor="w",
            ).grid(row=0, column=col, padx=4)

        self._body = tk.Frame(self, bg=_CLR_BG)
        self._body.pack(fill="both", expand=True, padx=12)

        for i in range(num_sessions):
            self._add_row(i + 1)

        # Resize window to fit rows
        self.geometry(f"480x{60 + num_sessions * 32}")

        btn_frame = tk.Frame(self, bg=_CLR_BG)
        btn_frame.pack(fill="x", padx=12, pady=8)
        ttk.Button(btn_frame, text="Stop All", command=self._stop_all).pack(side="right")

        self._poll()

    def _add_row(self, session_id: int):
        row = session_id - 1
        bar_var = tk.IntVar(value=0)
        bar = ttk.Progressbar(
            self._body, variable=bar_var, maximum=100, length=120, mode="determinate"
        )
        status_lbl = tk.Label(
            self._body, text="Waiting…", bg=_CLR_BG, fg=_CLR_TAB_CARD_FG,
            font=_FONT_SMALL, anchor="w", width=22,
        )
        tk.Label(
            self._body, text=f"#{session_id}", bg=_CLR_BG, fg=_CLR_TAB_CARD_FG,
            font=_FONT_SMALL, width=6, anchor="w",
        ).grid(row=row, column=0, padx=4, pady=4)
        tk.Label(
            self._body, text="—", bg=_CLR_BG, fg=_CLR_TEXT_MUTED,
            font=_FONT_SMALL, width=4, anchor="w",
        ).grid(row=row, column=1, padx=4)
        bar.grid(row=row, column=2, padx=4)
        status_lbl.grid(row=row, column=3, padx=4)
        self._rows[session_id] = {"bar_var": bar_var, "status_lbl": status_lbl}

    def _poll(self):
        if not self._manager:
            return
        statuses = self._manager.get_status()
        for s in statuses:
            sid = s["session_id"]
            if sid not in self._rows:
                continue
            row = self._rows[sid]
            row["bar_var"].set(s["progress"])
            msg = s["status_msg"] or s["status"].capitalize()
            row["status_lbl"].config(text=_truncate(msg, 22))

        if not self._manager.all_done:
            self.after(500, self._poll)

    def _stop_all(self):
        if self._manager:
            self._manager.stop_all()


# ---------------------------------------------------------------------------
# TabPickerDialog — main window
# ---------------------------------------------------------------------------

class TabPickerDialog(tk.Toplevel):
    """
    Main drag-and-drop tab assignment dialog.

    Args:
        parent:          Parent Tk window.
        rows:            CSV rows to type (same rows sent to every session).
        browser_type:    Canonical browser key — passed to TabInjector.
        license_info:    LicenseInfo object for the Pro+ gate.
        on_run:          Called with a configured SessionManager when Run All fires.
        num_form_slots:  How many drop zones to show (default 5).
    """

    def __init__(
        self,
        parent: tk.Tk,
        rows: List[List[str]],
        browser_type: str,
        license_info=None,
        on_run: Optional[Callable] = None,
        num_form_slots: int = 5,
    ):
        super().__init__(parent)
        self.title("Multi-Form Fill — Assign Tabs")
        self.geometry("780x500")
        self.resizable(True, True)
        self.configure(bg=_CLR_BG)
        self.transient(parent)
        self.grab_set()

        self._rows = rows
        self._browser_type = browser_type
        self._license_info = license_info
        self._on_run = on_run
        self._num_slots = num_form_slots
        self._assignments: Dict[int, Optional[Dict[str, Any]]] = {
            i: None for i in range(1, num_form_slots + 1)
        }
        self._tabs: List[Dict[str, Any]] = []

        # Internal drag state
        self._drag_source = None
        self._drop_zones: List[DropZone] = []

        self._build_ui()
        self._load_tabs()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        # ── Header ────────────────────────────────────────────────────
        header = tk.Frame(self, bg=_CLR_BG)
        header.pack(fill="x", padx=16, pady=(14, 6))
        tk.Label(
            header, text="Multi-Form Fill",
            bg=_CLR_BG, fg=_CLR_TAB_CARD_FG, font=_FONT_TITLE,
        ).pack(side="left")
        tk.Label(
            header,
            text="Pro+",
            bg="#FF9F0A", fg="#000000",
            font=("Helvetica", 9, "bold"),
            padx=6, pady=2,
        ).pack(side="left", padx=(8, 0))

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=16, pady=4)

        # ── Main content area ─────────────────────────────────────────
        content = tk.Frame(self, bg=_CLR_BG)
        content.pack(fill="both", expand=True, padx=16, pady=4)
        content.columnconfigure(0, weight=2)
        content.columnconfigure(1, weight=3)
        content.rowconfigure(0, weight=1)

        # Left: tab list
        left = tk.Frame(content, bg=_CLR_BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))

        left_header = tk.Frame(left, bg=_CLR_BG)
        left_header.pack(fill="x")
        tk.Label(
            left_header, text="Open Tabs",
            bg=_CLR_BG, fg=_CLR_TAB_CARD_FG, font=_FONT_BODY,
        ).pack(side="left")
        ttk.Button(
            left_header, text="↻ Refresh",
            command=self._load_tabs,
        ).pack(side="right")

        self._tab_scroll_frame = tk.Frame(left, bg=_CLR_BG)
        self._tab_scroll_frame.pack(fill="both", expand=True, pady=(8, 0))

        # Right: drop zones
        right = tk.Frame(content, bg=_CLR_BG)
        right.grid(row=0, column=1, sticky="nsew")

        tk.Label(
            right, text="Assign to Forms",
            bg=_CLR_BG, fg=_CLR_TAB_CARD_FG, font=_FONT_BODY,
        ).pack(anchor="w")

        zones_frame = tk.Frame(right, bg=_CLR_BG)
        zones_frame.pack(fill="both", expand=True, pady=(8, 0))

        for i in range(1, self._num_slots + 1):
            zone = DropZone(
                zones_frame,
                slot_index=i,
                on_assign=self._on_assignment_changed,
            )
            zone.pack(fill="x", pady=3)
            self._drop_zones.append(zone)

        # ── Bottom bar ────────────────────────────────────────────────
        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=16, pady=6)

        bottom = tk.Frame(self, bg=_CLR_BG)
        bottom.pack(fill="x", padx=16, pady=(0, 12))

        self._assignment_label = tk.Label(
            bottom,
            text="Drag a tab to at least one form slot to begin.",
            bg=_CLR_BG, fg=_CLR_TEXT_MUTED, font=_FONT_SMALL,
        )
        self._assignment_label.pack(side="left")

        ttk.Button(bottom, text="Cancel", command=self.destroy).pack(side="right", padx=(8, 0))

        self._run_btn = tk.Button(
            bottom,
            text="▶  Run All",
            bg=_CLR_BTN_DISABLED,
            fg="#FFFFFF",
            font=("Helvetica", 11, "bold"),
            relief="flat",
            padx=16, pady=6,
            state="disabled",
            command=self._on_run_all,
        )
        self._run_btn.pack(side="right")

    # ------------------------------------------------------------------
    # Tab loading
    # ------------------------------------------------------------------

    def _load_tabs(self):
        """Read open tabs from the browser and populate the left panel."""
        for widget in self._tab_scroll_frame.winfo_children():
            widget.destroy()

        try:
            self._tabs = self._fetch_open_tabs()
        except Exception as exc:
            logger.warning("TabPicker: failed to load tabs: %s", exc)
            self._tabs = []

        if not self._tabs:
            tk.Label(
                self._tab_scroll_frame,
                text="No tabs found.\nMake sure your browser is open,\nthen click Refresh.",
                bg=_CLR_BG, fg=_CLR_TEXT_MUTED, font=_FONT_SMALL, justify="center",
            ).pack(pady=20)
            return

        for tab_info in self._tabs:
            card = TabCard(self._tab_scroll_frame, tab_info)
            card.pack(fill="x", pady=3)

    def _fetch_open_tabs(self) -> List[Dict[str, Any]]:
        """
        Return a list of open tab dicts: {index, title, url}.
        Uses BrowserContext on Mac; pywinauto on Windows.
        """
        import platform as _platform
        import subprocess as _subprocess

        tabs = []
        if _platform.system() == "Darwin":
            # Get tab count and titles via AppleScript
            browser_map = {
                "chrome": "Google Chrome",
                "brave":  "Brave Browser",
                "safari": "Safari",
            }
            app_name = browser_map.get(self._browser_type)
            if not app_name:
                return []

            if self._browser_type == "safari":
                script = (
                    f'tell application "Safari" to get {{name, URL}} '
                    f'of every tab of front window'
                )
            else:
                script = (
                    f'tell application "{app_name}" to get '
                    f'{{title, URL}} of every tab of front window'
                )

            result = _subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, check=False, timeout=4,
            )
            if result.returncode == 0:
                # AppleScript returns comma-separated lists, one for titles, one for URLs
                raw = result.stdout.strip()
                # Parse: "title1, title2, ..., url1, url2, ..."
                # AppleScript returns two lists separated by ", " at the midpoint
                parts = [p.strip() for p in raw.split(", ")]
                mid = len(parts) // 2
                titles = parts[:mid]
                urls   = parts[mid:]
                for i, (title, url) in enumerate(zip(titles, urls), start=1):
                    tabs.append({"index": i, "title": title, "url": url})

        elif _platform.system() == "Windows":
            # Use pywinauto to enumerate tab titles from the browser window
            try:
                from pywinauto import Desktop
                import win32process, psutil
                exe_map = {"chrome": "chrome.exe", "edge": "msedge.exe", "brave": "brave.exe"}
                target = exe_map.get(self._browser_type)
                if not target:
                    return []
                desktop = Desktop(backend="uia")
                for win in desktop.windows():
                    try:
                        _, pid = win32process.GetWindowThreadProcessId(win.handle)
                        if psutil.Process(pid).name().lower() != target:
                            continue
                        tab_bar = win.child_window(control_type="TabItem")
                        for i, tab in enumerate(tab_bar, start=1):
                            tabs.append({
                                "index": i,
                                "title": tab.window_text(),
                                "url": "",
                            })
                        break
                    except Exception:
                        continue
            except Exception as exc:
                logger.debug("TabPicker Windows tab fetch failed: %s", exc)

        return tabs

    # ------------------------------------------------------------------
    # Assignment tracking
    # ------------------------------------------------------------------

    def _on_assignment_changed(self, slot_index: int, tab_info: Optional[Dict[str, Any]]):
        self._assignments[slot_index] = tab_info
        assigned_count = sum(1 for v in self._assignments.values() if v is not None)

        if assigned_count > 0:
            self._run_btn.config(state="normal", bg=_CLR_BTN_RUN)
            self._assignment_label.config(
                text=f"{assigned_count} form slot(s) assigned. Ready to run."
            )
        else:
            self._run_btn.config(state="disabled", bg=_CLR_BTN_DISABLED)
            self._assignment_label.config(
                text="Drag a tab to at least one form slot to begin."
            )

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def _on_run_all(self):
        assigned = {
            slot: info
            for slot, info in self._assignments.items()
            if info is not None
        }
        if not assigned:
            messagebox.showwarning("No Assignments", "Assign at least one tab before running.")
            return

        try:
            from session_manager import SessionManager
        except ImportError:
            messagebox.showerror("Error", "session_manager module not found.")
            return

        try:
            manager = SessionManager(license_info=self._license_info)
        except Exception as exc:
            messagebox.showerror("License Error", str(exc))
            return

        for slot_index, tab_info in sorted(assigned.items()):
            manager.add_session(
                tab_index=tab_info["index"],
                browser_type=self._browser_type,
                rows=self._rows,
            )

        manager.start_all()

        # Show progress panel
        ProgressPanel(self, manager, num_sessions=len(assigned))

        if self._on_run:
            self._on_run(manager)

        logger.info(
            "TabPicker: started %d session(s) via Run All", len(assigned)
        )
