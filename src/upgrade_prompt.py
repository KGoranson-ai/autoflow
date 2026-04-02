"""
Upgrade Prompt UI — Typestra desktop app.

Provides:
- TrialCountdownBanner: thin dismissible banner at the top of the app
- UpgradeDialog: modal dialog shown when trial expires or Pro feature is blocked
- FeatureGate: decorator / wrapper to block Pro features behind paywall
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox
import platform
import logging
import webbrowser
from typing import Optional, Callable

logger = logging.getLogger(__name__)

_TYPESTRA_PRICING_URL = "https://typestra.com/pricing"
_TYPESTRA_DOWNLOAD_URL = "https://typestra.com/download"


# ─────────────────────────────────────────────────────────────────────────────
# Trial Countdown Banner
# ─────────────────────────────────────────────────────────────────────────────

class TrialCountdownBanner(ttk.Frame):
    """
    Thin dismissible banner shown at the top of the app when a trial is active.

    Shows: "🗓 Trial ends in X days — Upgrade now →"
    Dismissed per-session (re-shown on next app launch if still applicable).
    """

    BANNER_HEIGHT = 32

    def __init__(
        self,
        parent: tk.Widget,
        days_remaining: int,
        on_upgrade_click: Callable[[], None],
        on_dismiss: Callable[[], None],
    ):
        super().__init__(parent, height=self.BANNER_HEIGHT, style="TrialBanner.TFrame")
        self._days = days_remaining
        self._on_upgrade = on_upgrade_click
        self._on_dismiss = on_dismiss
        self._dismissed = False

        self.pack_propagate(False)
        self._build()

    def _build(self) -> None:
        self.configure(style="TrialBanner.TFrame")

        container = ttk.Frame(self, style="TrialBanner.TFrame")
        container.pack(fill="x", padx=12, pady=4)

        if self._days <= 1:
            icon = "🚨"
            text = f" Trial ends {'today' if self._days == 0 else 'tomorrow'} — Upgrade now →"
            urgency = True
        else:
            icon = "🗓"
            text = f" Trial ends in {self._days} days — Upgrade now →"
            urgency = False

        msg_frame = ttk.Frame(container)
        msg_frame.pack(side="left", fill="x", expand=True)

        icon_label = ttk.Label(
            msg_frame,
            text=icon,
            font=("Arial", 11),
        )
        icon_label.pack(side="left", padx=(0, 6))

        label = ttk.Label(
            msg_frame,
            text=text,
            font=("Arial", 10, "bold" if urgency else "normal"),
            foreground="#ff6b6b" if urgency else "#ffa500",
        )
        label.pack(side="left")

        btn_frame = ttk.Frame(container)
        btn_frame.pack(side="right")

        upgrade_btn = ttk.Button(
            btn_frame,
            text="Upgrade",
            command=self._on_upgrade,
            width=8,
        )
        upgrade_btn.pack(side="left", padx=(0, 4))

        dismiss_btn = ttk.Button(
            btn_frame,
            text="✕",
            command=self._dismiss,
            width=3,
        )
        dismiss_btn.pack(side="left")

    def _dismiss(self) -> None:
        self._dismissed = True
        self.pack_forget()
        self._on_dismiss()

    def is_dismissed(self) -> bool:
        return self._dismissed


# ─────────────────────────────────────────────────────────────────────────────
# Upgrade / Trial-Expired Dialog
# ─────────────────────────────────────────────────────────────────────────────

class UpgradeDialog(tk.Toplevel):
    """
    Modal dialog shown when:
    - Trial has expired (user must upgrade or exit)
    - User tries to use a Pro/Team-only feature

    Args:
        parent: parent window
        reason: one of "trial_expired", "feature_blocked", "invalid_license"
        feature_name: name of the blocked feature (for feature_blocked reason)
        tier_required: minimum tier needed (e.g. "Pro", "Team")
        on_upgrade: callback when "Upgrade Now" is clicked
        on_enter_license: callback when "Enter License" is clicked
    """

    def __init__(
        self,
        parent: tk.Widget,
        reason: str = "trial_expired",
        feature_name: Optional[str] = None,
        tier_required: Optional[str] = None,
        on_upgrade: Optional[Callable[[], None]] = None,
        on_enter_license: Optional[Callable[[], None]] = None,
    ):
        super().__init__(parent)
        self._on_upgrade = on_upgrade
        self._on_enter_license = on_enter_license
        self._is_mac = platform.system() == "Darwin"

        self.title("Typestra — Upgrade Required")
        self.resizable(False, False)
        self.grab_set()
        self._center_on_parent(parent)

        # Modal behaviour — close goes to cancel
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        self._build(reason, feature_name, tier_required)

    def _center_on_parent(self, parent: tk.Widget) -> None:
        self.update_idletasks()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()
        ww = self.winfo_width()
        wh = self.winfo_height()
        x = px + (pw - ww) // 2
        y = py + (ph - wh) // 2
        self.geometry(f"+{x}+{y}")

    def _build(
        self,
        reason: str,
        feature_name: Optional[str],
        tier_required: Optional[str],
    ) -> None:
        outer = ttk.Frame(self, padding=24)
        outer.pack(fill="both", expand=True)

        # ── icon + title ────────────────────────────────────────────────────
        if reason == "trial_expired":
            icon = "⏱"
            title = "Your trial has ended"
            subtitle = (
                "Thanks for trying Typestra! To keep using it, choose a plan below. "
                "Your settings and data are saved — pick up right where you left off."
            )
        elif reason == "feature_blocked":
            icon = "🔒"
            title = f"\"{feature_name}\" is a Pro feature"
            subtitle = (
                f"You need a Pro or Team subscription to use \"{feature_name}\". "
                "Upgrade below to unlock it and everything else in your plan."
            )
        elif reason == "invalid_license":
            icon = "❌"
            title = "License not valid"
            subtitle = (
                "Your license key could not be validated. "
                "It may have been revoked, expired, or entered incorrectly. "
                "Enter a new license key or upgrade to a new plan."
            )
        else:
            icon = "📋"
            title = "Upgrade Required"
            subtitle = "Choose a plan to continue using Typestra."

        ttk.Label(outer, text=icon, font=("Arial", 36)).pack(pady=(0, 8))
        ttk.Label(outer, text=title, font=("Helvetica", 14, "bold")).pack(pady=(0, 6))
        ttk.Label(
            outer,
            text=subtitle,
            font=("Arial", 10),
            wraplength=380,
            justify="center",
            foreground="#666",
        ).pack(pady=(0, 16))

        # ── plan summary ─────────────────────────────────────────────────────
        plan_frame = ttk.LabelFrame(outer, text="Plans", padding=12)
        plan_frame.pack(fill="x", pady=(0, 16))

        plans = [
            ("Solo",   "$19/mo",    "Unlimited text blocks, keyboard shortcuts, spreadsheet mode"),
            ("Pro",    "$39/mo",    "+ OCR text capture, auto-calculations, multi-device (2)"),
            ("Team",   "$79/mo",    "+ Scheduled scripts, team management, dedicated support"),
        ]
        for name, price, desc in plans:
            row = ttk.Frame(plan_frame)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=f"{name}:", font=("Arial", 10, "bold"), width=8).pack(side="left")
            ttk.Label(row, text=price, font=("Arial", 10), width=10).pack(side="left")
            ttk.Label(row, text=desc, font=("Arial", 9), foreground="#555").pack(side="left", padx=(6, 0))

        # ── action buttons ───────────────────────────────────────────────────
        btn_frame = ttk.Frame(outer)
        btn_frame.pack(fill="x")

        if reason == "invalid_license" and self._on_enter_license:
            enter_btn = ttk.Button(
                btn_frame,
                text="Enter License Key",
                command=self._on_enter_license,
                width=20,
            )
            enter_btn.pack(side="left", padx=(0, 8))

        upgrade_btn = ttk.Button(
            btn_frame,
            text="Upgrade Now →",
            command=self._on_upgrade,
            style="Upgrade.TButton",
            width=20,
        )
        upgrade_btn.pack(side="right")

        # ── secondary link ────────────────────────────────────────────────────
        ttk.Label(
            outer,
            text="Already subscribed? Enter a license key instead",
            font=("Arial", 9),
            foreground="#888",
            cursor="hand2",
        ).pack(pady=(8, 0))
        self.bind("<Button-1>", lambda e: self._on_enter_license())


# ─────────────────────────────────────────────────────────────────────────────
# Feature Gate decorator
# ─────────────────────────────────────────────────────────────────────────────

class FeatureGate:
    """
    Decorator / helper to wrap feature access with upgrade prompts.

    Usage:
        @FeatureGate.require("ocr", license_manager, parent=root)
        def extract_from_image(self):
            ...

    If the user lacks the feature, a dialog is shown and the call is blocked.
    """

    @staticmethod
    def check_and_prompt(
        feature: str,
        license_manager,  # LicenseManager instance
        parent: tk.Widget,
        on_upgrade: Optional[Callable[[], None]] = None,
        on_enter_license: Optional[Callable[[], None]] = None,
    ) -> bool:
        """
        Check if the current license includes `feature`.

        Returns True if allowed, False if blocked (dialog was shown).
        Callbacks are invoked when the user clicks "Upgrade" or "Enter License".
        """
        if license_manager.has_feature(feature):
            return True

        # Determine which tier is required
        tier_map = {
            "ocr":                 "Pro",
            "auto_calculations":   "Pro",
            "multi_device":        "Pro",
            "scheduled_scripts":   "Team",
            "team_management":     "Team",
            "dedicated_support":   "Team",
        }
        tier_required = tier_map.get(feature, "Pro")
        feature_display = feature.replace("_", " ").title()

        def _open_pricing():
            webbrowser.open_new_tab(_TYPESTRA_PRICING_URL)

        cb_upgrade = on_upgrade or _open_pricing

        UpgradeDialog(
            parent,
            reason="feature_blocked",
            feature_name=feature_display,
            tier_required=tier_required,
            on_upgrade=cb_upgrade,
            on_enter_license=on_enter_license,
        )
        return False

    @staticmethod
    def require(
        feature: str,
        license_manager,  # LicenseManager instance
        parent: tk.Widget,
        on_upgrade: Optional[Callable[[], None]] = None,
        on_enter_license: Optional[Callable[[], None]] = None,
    ):
        """
        Decorator factory. Usage:

            @FeatureGate.require("ocr", my_license_manager, parent=root)
            def my_method(self, ...):
                ...
        """
        def decorator(func):
            def wrapper(*args, **kwargs):
                allowed = FeatureGate.check_and_prompt(
                    feature=feature,
                    license_manager=license_manager,
                    parent=parent,
                    on_upgrade=on_upgrade,
                    on_enter_license=on_enter_license,
                )
                if allowed:
                    return func(*args, **kwargs)
                # Blocked — dialog was shown; do nothing
                return None
            return wrapper
        return decorator
