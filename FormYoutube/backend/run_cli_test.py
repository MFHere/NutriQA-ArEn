"""Run CLI test: process first 1 URL from Arabic_Youtube_URLs.xlsx (page 2). Run from anywhere."""
import os
import sys
from pathlib import Path

_backend = Path(__file__).resolve().parent
os.chdir(_backend)
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

# Find Arabic_Youtube_URLs.xlsx (project root or csv_version)
for base in (_backend.parent.parent, _backend.parent):
    xlsx = base / "Arabic_Youtube_URLs.xlsx"
    if xlsx.is_file():
        break
else:
    print("Arabic_Youtube_URLs.xlsx not found in project root or csv_version.")
    sys.exit(1)

sys.argv = ["cli_main.py", str(xlsx), "--limit", "1"]
from cli_main import main
main()
