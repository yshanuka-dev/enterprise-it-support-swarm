"""Profile transcript JSON files: distributions, plus problem-vs-resolution pairs to eyeball.

Usage: python scripts/profile_transcripts.py [DIR] [--pairs]
Heuristic: the longest agent message is usually the resolution message.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

DEFAULT_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "transcripts"


def main() -> None:
    """Print counters over all transcripts in a directory."""
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    show_pairs = "--pairs" in sys.argv
    folder = Path(args[0]) if args else DEFAULT_DIR
    files = sorted(folder.glob("*.json"))
    docs = [
        d for p in files
        if (d := json.loads(p.read_text(encoding="utf-8"))) and "messages" in d
    ]
    print(f"{len(docs)} transcripts in {folder}")
    if not docs:
        print("  nothing to profile; pass a directory containing transcript .json files")
        return

    for key in ("ticket_type", "channel", "user_tone", "agent_style", "was_escalated",
                "had_false_start", "satisfaction_score"):
        print(f"  {key:<18} {dict(Counter(d.get(key) for d in docs))}")

    if show_pairs:
        print("\nFirst user message vs longest agent message:")
        for d in docs:
            msgs = d["messages"]
            first_user = next(m["text"] for m in msgs if m["role"] == "user")
            resolution = max((m["text"] for m in msgs if m["role"] == "agent"), key=len)
            print(f"\n{d['transcript_id']} [{d['ticket_type']}]")
            print(f"  USER : {first_user[:110]}")
            print(f"  AGENT: {resolution[:110]}")


if __name__ == "__main__":
    main()
