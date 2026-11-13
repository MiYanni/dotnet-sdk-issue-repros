import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
platform = sys.argv[2]
records = []
for path in root.rglob("*.consumer.json"):
    data = json.loads(path.read_text())
    data["case"] = str(path.parent.relative_to(root))
    records.append(data)
    if not data["registeredLockFileFound"] or not data.get("consumedPackageFolders"):
        raise RuntimeError(f"Missing consumed lockfile evidence: {path}")
baseline = "linux-only-first" if platform == "linux" else "wsl-clean"
baseline_records = [r for r in records if r["case"] == baseline]
if len(baseline_records) != 3 or not all(r["succeeded"] for r in baseline_records):
    raise RuntimeError(f"Baseline did not pass with three consumed lockfiles: {baseline}")
if platform == "linux":
    second = [r for r in records if r["case"] == "linux-only-second"]
    if len(second) != 3 or not all(r["succeeded"] for r in second):
        raise RuntimeError("Second Linux-only control did not pass")
    environment = [r for r in records if r["case"] == "linux-colon-consumer"]
    if len(environment) != 3 or not all("Unable to find fallback package folder" in r.get("consumerException", "") for r in environment):
        raise RuntimeError("Malformed environment restore did not produce the exact downstream consumer exception")
else:
    injected = [r for r in records if r["case"] == "wsl-after-configured-windows"]
    if len(injected) != 3 or not all("Unable to find fallback package folder" in r.get("consumerException", "") for r in injected):
        raise RuntimeError("The deliberately configured valid Windows fallback did not produce the exact Linux exception")
    standard = root / "wsl-standard-after-configured-windows"
    if json.loads((standard / "invocation.json").read_text())["exitCode"] == 0:
        raise RuntimeError("The ordinary SDK ResolvePackageAssets target unexpectedly succeeded")
    if "MSB4018" not in (standard / "diagnostics.txt").read_text():
        raise RuntimeError("The ordinary SDK target did not reproduce the original MSB4018")
    isolated = [r for r in records if r["case"] == "wsl-isolated"]
    if len(isolated) != 3 or not all(r["succeeded"] for r in isolated):
        raise RuntimeError("Host-isolated artifacts control did not pass")
summary = [
    {"case": r["case"], "project": pathlib.PurePath(r["project"]).name,
     "host": r["host"], "succeeded": r["succeeded"],
     "consumedPackageFolders": r["consumedPackageFolders"],
     "exception": r.get("consumerException"), "pid": r["pid"]}
    for r in records
]
(root / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
print(json.dumps(summary, indent=2))
