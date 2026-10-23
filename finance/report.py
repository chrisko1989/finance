"""One-shot monitoring report for a scheduler; never fetches quotes or trades."""
import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .analytics import analyze
from .storage import Store


def main():
    parser = argparse.ArgumentParser(description="Prüft gespeicherte Depotdaten und erzeugt einen JSON-Bericht.")
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parent.parent / ".data")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true", help="Exit 2 bei Hinweisen, sonst 0; technische Fehler liefern 1.")
    args = parser.parse_args()
    if not (args.data_dir / "portfolio.sqlite3").is_file():
        parser.exit(1, "Noch keine Datenbank. Anwendung zunächst starten und Depotdaten importieren.\n")
    state = Store(args.data_dir).read()
    result = {"generated_at": datetime.now(timezone.utc).isoformat(), "revision": state["revision"], "currency": state["config"]["currency"], **analyze(state)}
    text = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=args.output.parent, prefix=".report-")
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(text)
            os.replace(temporary, args.output)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    else:
        print(text, end="")
    if args.check and result["alerts"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
