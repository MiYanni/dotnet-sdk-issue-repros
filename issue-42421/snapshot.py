import hashlib
import json
import pathlib
import sys
import time

root, output = map(pathlib.Path, sys.argv[1:3])
output.mkdir(parents=True, exist_ok=True)
records = []
for base in ("App", "Library", "Sibling", "artifacts-windows", "artifacts-linux"):
    directory = root / base
    for path in directory.rglob("*"):
        if not path.is_file() or not (
            path.name == "project.assets.json"
            or path.name.endswith((".nuget.dgspec.json", ".nuget.g.props", ".nuget.g.targets"))
        ):
            continue
        data = path.read_bytes()
        record = {
            "path": str(path),
            "mtimeNs": path.stat().st_mtime_ns,
            "sha256": hashlib.sha256(data).hexdigest(),
            "content": data.decode("utf-8-sig"),
        }
        if path.name == "project.assets.json":
            record["packageFolders"] = list(json.loads(data)["packageFolders"])
        records.append(record)
(output / "snapshots.json").write_text(
    json.dumps({"capturedUnixNs": time.time_ns(), "files": records}, indent=2), encoding="utf-8"
)
