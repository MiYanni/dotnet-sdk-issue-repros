import pathlib
import re
import sys

source, destination = map(pathlib.Path, sys.argv[1:3])
destination.mkdir(parents=True, exist_ok=True)
for path in source.rglob("*"):
    if not path.is_file() or path.suffix not in (".json", ".txt"):
        continue
    text = path.read_text(encoding="utf-8-sig")
    if re.search(r"github_pat_|gh[pousr]_[A-Za-z0-9]{20,}|Authorization:|password\s*=", text, re.I):
        raise RuntimeError(f"Credential marker in allowlisted artifact {path.name}")
    target = destination / path.relative_to(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
print(f"Published allowlisted JSON/text evidence; raw logs, strace, and binlogs excluded: {destination}")
