"""CLI entrypoint to run a diagnostic pass over an error log."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from diagnostic_engine.agent.graph import get_agent
from diagnostic_engine.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the FastAPI diagnostic agent")
    parser.add_argument(
        "--log",
        type=Path,
        default=None,
        help="Path to error log (defaults to ERROR_LOG_PATH)",
    )
    parser.add_argument(
        "--text",
        type=str,
        default=None,
        help="Inline traceback text instead of a log file",
    )
    args = parser.parse_args()

    settings = get_settings()
    if args.text:
        raw = args.text
    else:
        log_path = args.log or settings.error_log_path
        raw = log_path.read_text(encoding="utf-8") if log_path.exists() else ""

    agent = get_agent()
    final = agent.invoke({"raw_log_entry": raw})

    # Make dataclasses JSON-serializable for CLI output
    out = dict(final)
    parsed = out.get("parsed_traceback")
    if parsed is not None and hasattr(parsed, "__dict__"):
        failing = parsed.failing_frame
        out["parsed_traceback"] = {
            "exception_type": parsed.exception_type,
            "exception_message": parsed.exception_message,
            "failing_frame": (
                {
                    "filename": failing.filename,
                    "line_number": failing.line_number,
                    "function_name": failing.function_name,
                    "code_context": failing.code_context,
                }
                if failing
                else None
            ),
            "frame_count": len(parsed.full_frames),
        }
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
