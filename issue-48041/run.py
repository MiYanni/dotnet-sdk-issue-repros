import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile


ROOT = Path(__file__).resolve().parent
RAW = ROOT / ".raw"
OUT = ROOT / "evidence"
SDK = os.environ["REPRO_SDK"]
WORKLOAD = os.environ["REPRO_WORKLOAD"]
FRAMEWORK = os.environ["REPRO_FRAMEWORK"]
RESULTS = []
HOST_RID = "maccatalyst-arm64" if os.uname().machine == "arm64" else "maccatalyst-x64"
RID_PAIR = "maccatalyst-x64;maccatalyst-arm64"


def sanitize(text):
    roots = [str(ROOT), os.environ.get("GITHUB_WORKSPACE", ""),
             os.environ.get("RUNNER_TEMP", ""), str(Path.home())]
    for index, root in enumerate(roots):
        if root:
            text = text.replace(root, f"<root-{index}>")
    for name, value in os.environ.items():
        if re.search(r"TOKEN|SECRET|PASSWORD|CREDENTIAL", name, re.I) and len(value) > 8:
            text = text.replace(value, "<redacted>")
    text = re.sub(r"gh[pousr]_[A-Za-z0-9_]+", "<redacted>", text)
    return text


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(sanitize(json.dumps(value, indent=2)) + "\n")


def command(case, phase, args, cwd=ROOT, timeout=360, required=False):
    print(f"{case}: {phase}", flush=True)
    try:
        completed = subprocess.run(args, cwd=cwd, text=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, timeout=timeout)
        code = completed.returncode
        output = completed.stdout
    except subprocess.TimeoutExpired as error:
        code = 124
        output = (error.stdout or b"").decode(errors="replace") if isinstance(error.stdout, bytes) else (error.stdout or "")
        output += "\nREPRO COMMAND TIMEOUT\n"
    log = OUT / case / (phase + ".log")
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(sanitize("$ " + " ".join(args) + "\n" + output))
    entry = {"case": case, "phase": phase, "exit": code,
             "diagnostics": re.findall(r"(?:error|warning) [A-Z]+\d+:.*", sanitize(output))}
    RESULTS.append(entry)
    write_json(OUT / "commands.json", RESULTS)
    print(f"exit={code}", flush=True)
    if required and code:
        raise RuntimeError(f"Required setup {phase} failed with exit {code}")
    return code


def properties(case_root, phase, extra):
    capture = case_root / "capture" / phase
    capture.mkdir(parents=True, exist_ok=True)
    return ["-m:1", "-nr:false", "-p:UseSharedCompilation=false",
            f"-p:ReproCaptureDirectory={capture}", f"-p:ReproPhase={phase}", *extra]


def run_project(case, phase, operation, project, extra, timeout=360):
    case_root = project.parent
    binlog = RAW / "binlogs" / case / (phase + ".binlog")
    binlog.parent.mkdir(parents=True, exist_ok=True)
    args = ["dotnet", operation, str(project)]
    if operation in ("build", "pack"):
        args.append("--no-restore")
    args += properties(case_root, phase, extra)
    args += ["-v:minimal", f"-bl:{binlog};ProjectImports=None"]
    return command(case, phase, args, cwd=case_root, timeout=timeout)


def inventory(case, phase, project):
    files = []
    for directory in ("bin", "obj"):
        for path in sorted((project.parent / directory).rglob("*")):
            if path.is_file() and (path.name == project.stem + ".dll" or "runtimeconfig" in path.name):
                files.append({"path": str(path.relative_to(project.parent)),
                              "size": path.stat().st_size,
                              "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    archives = []
    for archive in sorted((project.parent / "bin").rglob("*.nupkg")):
        with zipfile.ZipFile(archive) as package:
            archives.append({"path": str(archive.relative_to(project.parent)),
                             "entries": [{"path": item.filename, "size": item.file_size,
                                          "sha256": hashlib.sha256(package.read(item)).hexdigest()}
                                         for item in package.infolist()]})
    write_json(OUT / case / (phase + "-files.json"), {"files": files, "packages": archives})


def prepare_app(case):
    directory = RAW / "cases" / case
    shutil.copytree(RAW / "template", directory)
    project = directory / "CatalystPackApp.csproj"
    tree = ET.parse(project)
    for group in tree.getroot().findall("PropertyGroup"):
        for element in list(group):
            if element.tag in ("TargetFramework", "TargetFrameworks"):
                group.remove(element)
    tree.getroot().find("PropertyGroup").insert(0, ET.Element("TargetFrameworks"))
    tree.getroot().find("PropertyGroup/TargetFrameworks").text = FRAMEWORK + "-maccatalyst"
    tree.write(project, encoding="unicode")
    (directory / "Directory.Build.targets").write_text(
        f'<Project><Import Project="{ROOT / "Capture.targets"}" /></Project>\n')
    (OUT / case).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(project, OUT / case / "project.csproj")
    return project


def probes(case, project, extra, rids):
    requested = [
        ("probe-outer", extra + ["-p:TargetFramework=" + FRAMEWORK + "-maccatalyst"])
    ]
    for rid in rids:
        requested.append(("probe-" + rid, [
            "-p:Configuration=" + ("Release" if case.startswith("Release") else "Debug"),
            "-p:TargetFramework=" + FRAMEWORK + "-maccatalyst",
            "-p:RuntimeIdentifiers=", "-p:RuntimeIdentifier=" + rid,
            "-p:AppendRuntimeIdentifierToOutputPath=true", "-p:_IsMultiRidBuild=true"]))
    for phase, props in requested:
        run_project(case, phase, "msbuild", project, props + [
            "-t:BuiltProjectOutputGroup",
            "-getProperty:Configuration,TargetFramework,RuntimeIdentifier,RuntimeIdentifiers,OutputPath,TargetPath,ProjectRuntimeConfigFilePath,_CanOutputAppBundle,_IsMultiRidBuild,PackRelease",
            "-getItem:BuiltProjectOutputGroupOutput"])


def run_case(configuration, mode):
    case = configuration + "-" + mode
    project = prepare_app(case)
    extra = ["-p:Configuration=" + configuration]
    if mode == "rid":
        extra += ["-p:RuntimeIdentifier=" + HOST_RID]
        rids = [HOST_RID]
    elif mode == "list-one":
        extra += ["-p:RuntimeIdentifiers=" + HOST_RID]
        rids = [HOST_RID]
    elif mode == "list-two":
        extra += ["-p:RuntimeIdentifiers=" + RID_PAIR.replace(";", "%3B")]
        rids = RID_PAIR.split(";")
    else:
        rids = RID_PAIR.split(";") if configuration == "Release" else [HOST_RID]
    restore = command(case, "restore", ["dotnet", "restore", str(project),
                      *properties(project.parent, "restore", extra), "-v:minimal"],
                      cwd=project.parent)
    if restore:
        return
    run_project(case, "build", "build", project, extra)
    inventory(case, "after-build", project)
    probes(case, project, extra, rids)
    run_project(case, "pack", "pack", project, extra)
    inventory(case, "after-pack", project)
    if mode == "default" and configuration == "Release":
        run_project(case, "pack-unqualified", "pack", project, [])
        inventory(case, "after-pack-unqualified", project)
    capture = project.parent / "capture"
    for path in capture.rglob("*.txt"):
        destination = OUT / case / "capture" / path.relative_to(capture)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(sanitize(path.read_text()))


def library_control():
    case = "Release-library"
    directory = RAW / "cases" / case
    directory.mkdir(parents=True)
    project = directory / "CatalystLibrary.csproj"
    project.write_text(f'<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
                       f'<TargetFramework>{FRAMEWORK}-maccatalyst</TargetFramework>'
                       f'</PropertyGroup><Import Project="{ROOT / "Capture.targets"}" /></Project>')
    (directory / "Library.cs").write_text("public class CatalystLibrary { }\n")
    extra = ["-p:Configuration=Release"]
    if command(case, "restore", ["dotnet", "restore", str(project), *extra, "-v:minimal"],
               cwd=directory) == 0:
        run_project(case, "pack", "pack", project, extra)
        inventory(case, "after-pack", project)
    for path in (directory / "capture").rglob("*.txt"):
        destination = OUT / case / "capture" / path.relative_to(directory / "capture")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(sanitize(path.read_text()))


def main():
    RAW.mkdir()
    OUT.mkdir()
    (ROOT / "global.json").write_text(json.dumps({
        "sdk": {"version": SDK, "rollForward": "disable", "allowPrerelease": False}
    }))
    command("environment", "sdk-before", ["dotnet", "--info"], required=True)
    command("environment", "xcode", ["xcodebuild", "-version"], required=True)
    command("environment", "os", ["sw_vers"], required=True)
    command("environment", "workload-install", ["dotnet", "workload", "install",
            "maui-maccatalyst", "--version", WORKLOAD, "--skip-manifest-update",
            "--source", "https://api.nuget.org/v3/index.json", "--disable-parallel"],
            timeout=1200, required=True)
    command("environment", "sdk-after", ["dotnet", "--info"], required=True)
    command("environment", "workload-list", ["dotnet", "workload", "list"], required=True)
    command("environment", "template", ["dotnet", "new", "maui", "-n", "CatalystPackApp",
            "-o", str(RAW / "template"), "--no-restore"], required=True)
    write_json(OUT / "provenance.json", {"sdk": SDK, "workload_set": WORKLOAD,
               "framework": FRAMEWORK, "host_rid": HOST_RID,
               "run_url": "https://github.com/" + os.environ["GITHUB_REPOSITORY"] +
                          "/actions/runs/" + os.environ["GITHUB_RUN_ID"],
               "head": os.environ["GITHUB_SHA"],
               "binary_log_policy": "Raw binlogs retained only on ephemeral runner; never uploaded.",
               "project_policy": "MAUI template restricted to Mac Catalyst, no signing/output overrides."})
    for configuration in ("Debug", "Release"):
        for mode in ("rid", "list-one", "list-two", "default"):
            run_case(configuration, mode)
    library_control()
    rows = ["# Mac Catalyst build/pack command results", "",
            "Expected reproduction failures are recorded, not converted into success.",
            "This workflow's success means the evidence collector completed.", "",
            "| Case | Phase | Exit |", "| --- | --- | --- |"]
    rows += [f"| {item['case']} | {item['phase']} | {item['exit']} |" for item in RESULTS]
    (OUT / "summary.md").write_text("\n".join(rows) + "\n")
    # Fail the collector if a requested case could not restore or a command timed out.
    if any(item["exit"] == 124 or (item["phase"] == "restore" and item["exit"]) for item in RESULTS):
        raise RuntimeError("Matrix has setup failures or timeouts; inspect evidence.")


if __name__ == "__main__":
    main()
