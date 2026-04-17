"""
CLI for autoflow-engine. Mirrors the behavior of src/autoflow.py:
with --text runs typing; without --text tries to launch the GUI (when available).
"""

import argparse
import sys

import pyautogui

from autoflow_engine import TypingConfig, TypingEngine


def main() -> None:
    """Entry point for the autoflow console script. Runs CLI typing or launches GUI."""
    parser = argparse.ArgumentParser(
        description="AutoFlow - Human-like typing automation"
    )
    parser.add_argument(
        "--text",
        type=str,
        default=None,
        help="Text to type (CLI mode; omit to launch GUI)",
    )
    parser.add_argument(
        "--wpm",
        type=int,
        default=50,
        help="Words per minute (default: 50)",
    )
    parser.add_argument(
        "--human-level",
        type=int,
        default=2,
        choices=[1, 2, 3],
        metavar="1|2|3",
        dest="human_level",
        help="Humanization level: 1=Low, 2=Medium, 3=High (default: 2)",
    )
    parser.add_argument(
        "--countdown",
        type=int,
        default=5,
        help="Countdown seconds before typing (default: 5)",
    )
    parser.add_argument(
        "--no-speed-variation",
        action="store_true",
        help="Disable speed variation",
    )
    parser.add_argument(
        "--no-thinking",
        action="store_true",
        help="Disable thinking pauses",
    )
    parser.add_argument(
        "--no-punctuation",
        action="store_true",
        help="Disable punctuation pauses",
    )
    parser.add_argument(
        "--no-typos",
        action="store_true",
        help="Disable typos and corrections",
    )
    parser.add_argument(
        "--csv",
        type=str,
        default=None,
        help="Path to a CSV file to type in spreadsheet mode",
    )
    parser.add_argument(
        "--schedule",
        type=str,
        default=None,
        metavar="DATETIME",
        help=(
            "Schedule a CSV job to run at a future time. "
            'Format: "YYYY-MM-DD HH:MM"  (e.g. "2026-04-17 18:00"). '
            "Requires --csv."
        ),
    )
    args = parser.parse_args()

    # ── Scheduled CSV job ────────────────────────────────────────────────────
    if args.schedule is not None:
        if not args.csv:
            print("Error: --schedule requires --csv.")
            sys.exit(1)
        try:
            from job_scheduler import JobScheduler, parse_schedule_time
        except ImportError:
            print("Error: job_scheduler module not found.")
            sys.exit(1)

        try:
            run_at = parse_schedule_time(args.schedule)
        except ValueError as e:
            print(f"Error: {e}")
            sys.exit(1)

        config = TypingConfig(
            wpm=args.wpm,
            humanization_level=args.human_level,
            speed_variation=not args.no_speed_variation,
            thinking_pauses=not args.no_thinking,
            punctuation_pauses=not args.no_punctuation,
            typos_enabled=not args.no_typos,
            mode="spreadsheet",
            countdown_seconds=args.countdown,
        )

        def engine_factory(rows, on_status):
            engine = TypingEngine(
                config,
                on_status=on_status,
            )
            engine.type_spreadsheet(rows)

        scheduler = JobScheduler(engine_factory=engine_factory)
        scheduler.start()
        job_id = scheduler.add_job(
            csv_path=args.csv,
            scheduled_at=run_at,
            label=args.csv,
        )
        print(f"Job scheduled: {job_id}")
        print(f"  CSV:  {args.csv}")
        print(f"  Runs: {run_at.strftime('%Y-%m-%d %H:%M %Z')}")
        print("Waiting for scheduled time... (Ctrl+C to cancel)")
        try:
            while True:
                job = scheduler.get_job(job_id)
                if job and job["status"] in ("done", "failed", "cancelled"):
                    status = job["status"]
                    if status == "failed":
                        print(f"Job failed: {job.get('error')}")
                        sys.exit(1)
                    print(f"Job {status}.")
                    break
                import time as _time
                _time.sleep(10)
        except KeyboardInterrupt:
            scheduler.cancel_job(job_id)
            print("\nJob cancelled.")
        return

    # ── Immediate CSV spreadsheet mode ───────────────────────────────────────
    if args.csv is not None:
        config = TypingConfig(
            wpm=args.wpm,
            humanization_level=args.human_level,
            speed_variation=not args.no_speed_variation,
            thinking_pauses=not args.no_thinking,
            punctuation_pauses=not args.no_punctuation,
            typos_enabled=not args.no_typos,
            mode="spreadsheet",
            countdown_seconds=args.countdown,
        )
        try:
            import csv as _csv
            with open(args.csv, newline="", encoding="utf-8-sig") as f:
                rows = list(_csv.reader(f))
            engine = TypingEngine(config)
            engine.type_spreadsheet(rows)
            print("Spreadsheet fill complete.")
        except FileNotFoundError:
            print(f"Error: CSV file not found: {args.csv}")
            sys.exit(1)
        except pyautogui.FailSafeException:
            print("Emergency stop - mouse moved to corner.")
        except KeyboardInterrupt:
            print("Interrupted.")
        return

    # ── Immediate text mode ───────────────────────────────────────────────────
    if args.text is not None:
        config = TypingConfig(
            wpm=args.wpm,
            humanization_level=args.human_level,
            speed_variation=not args.no_speed_variation,
            thinking_pauses=not args.no_thinking,
            punctuation_pauses=not args.no_punctuation,
            typos_enabled=not args.no_typos,
            mode="text",
            countdown_seconds=args.countdown,
        )
        engine = TypingEngine(config)
        try:
            engine.type_text(args.text)
            print("Typing complete.")
        except pyautogui.FailSafeException:
            print("Emergency stop - mouse moved to corner.")
        except KeyboardInterrupt:
            print("Interrupted.")
        return

    # ── No actionable args — launch GUI or print help ─────────────────────────
    try:
        from autoflow_v3 import run_cli_or_gui
        run_cli_or_gui()
    except ImportError:
        parser.print_help()
        print()
        print("Provide --text to type from the CLI.")
        print("Provide --csv to fill a spreadsheet.")
        print("Combine --csv with --schedule to queue a job.")
        print("For the GUI app, run from source: python src/autoflow.py")
        sys.exit(0)
