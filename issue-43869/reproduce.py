import hashlib
import json
import os
import pathlib
import platform
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import urllib.request

VERSION = "8.0.400"
PROJECT = '<Project Sdk="Microsoft.Build.Traversal"><ItemGroup></ItemGroup></Project>\n'
CONFIG = """<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <packageSources><clear/><add key="nuget.org" value="https://api.nuget.org/v3/index.json" protocolVersion="3"/></packageSources>
  <disabledPackageSources><clear/></disabledPackageSources>
  <fallbackPackageFolders><clear/></fallbackPackageFolders>
</configuration>
"""
CAPTURE_KEYS = (
    "HOME", "SUDO_UID", "SUDO_USER", "DOTNET_CLI_HOME", "NUGET_PACKAGES",
    "NUGET_HTTP_CACHE_PATH", "NUGET_NETCORE_PLUGIN_PATHS", "NUGET_PLUGIN_PATHS",
    "MSBuildSDKsPath", "MSBuildExtensionsPath", "DOTNET_NUGET_SIGNATURE_VERIFICATION",
)


def sanitized(text, root):
    text = text.replace(str(root), "<fixture>")
    for name, label in (("RUNNER_TEMP", "<runner-temp>"),
                        ("GITHUB_WORKSPACE", "<checkout>")):
        value = os.environ.get(name)
        if value:
            text = text.replace(value, label)
    text = re.sub(r"/Users/[^/\s]+", "/Users/<runner>", text)
    text = re.sub(r"(https?://)[^/\s@]+:[^/\s@]+@", r"\1<redacted>@", text)
    text = re.sub(r"(?i)([?&](?:token|sig|password|secret|key)=)[^&\s]+",
                  r"\1<redacted>", text)
    return "\n".join(line for line in text.splitlines()
                     if not re.search(r"(?i)(github_token|gh_token|authorization:|"
                                      r"client_secret|access_token|password\s*=)", line)) + "\n"


def execute():
    dotnet, record_path, *args = sys.argv[2:]
    record = {
        "environmentBeforeCli": {key: os.environ.get(key) for key in CAPTURE_KEYS},
        "effectiveUid": os.geteuid(),
        "pluginDirectoryExists": (pathlib.Path(os.environ["HOME"]) / ".nuget/plugins").exists(),
        "userConfigDirectoryExists": (pathlib.Path(os.environ["HOME"]) / ".nuget/NuGet").exists(),
    }
    pathlib.Path(record_path).write_text(json.dumps(record, indent=2))
    os.execv(dotnet, [dotnet, *args])


def main():
    if platform.system() != "Darwin":
        raise RuntimeError("This harness is for ephemeral macOS runners only.")
    here = pathlib.Path(__file__).resolve().parent
    evidence = here / "evidence"
    evidence.mkdir()
    root = pathlib.Path(os.environ["RUNNER_TEMP"]) / "issue-43869"
    root.mkdir()
    arch = {"arm64": "arm64", "x86_64": "x64"}[platform.machine()]
    metadata_url = "https://builds.dotnet.microsoft.com/dotnet/release-metadata/8.0/releases.json"
    with urllib.request.urlopen(metadata_url, timeout=60) as response:
        metadata = json.load(response)
    sdk = next(s for release in metadata["releases"] for s in release.get("sdks", [])
               if s["version"] == VERSION)
    artifact = next(f for f in sdk["files"]
                    if f["rid"] == f"osx-{arch}" and f["name"].endswith(".tar.gz"))
    archive = root / "sdk.tar.gz"
    urllib.request.urlretrieve(artifact["url"], archive)
    with archive.open("rb") as downloaded:
        actual = hashlib.file_digest(downloaded, "sha512").hexdigest()
    if actual.lower() != artifact["hash"].lower():
        raise RuntimeError("Official SDK archive hash mismatch.")
    base = root / "base-sdk"
    base.mkdir()
    with tarfile.open(archive) as package:
        package.extractall(base, filter="data")
    archive.unlink()
    results = []
    sdk_version_file = (base / "sdk" / VERSION / ".version").read_text()
    summary = {
        "issue": "https://github.com/dotnet/sdk/issues/43869",
        "os": platform.platform(), "architecture": arch, "sdk": VERSION,
        "sdkVersionFile": sdk_version_file,
        "sdkArchiveUrl": artifact["url"], "sdkArchiveSha512": actual,
        "runUrl": f'https://github.com/{os.environ["GITHUB_REPOSITORY"]}/actions/runs/{os.environ["GITHUB_RUN_ID"]}',
        "commit": os.environ["GITHUB_SHA"], "results": results,
    }
    (evidence / "summary.json").write_text(json.dumps(summary, indent=2))
    for mode in ("user", "sudo"):
        sdk_root = root / f"{mode}-sdk"
        shutil.copytree(base, sdk_root)
        dotnet = sdk_root / "dotnet"
        mode_root = root / mode
        mode_root.mkdir()

        def run(case_name, operation, warm=False, instrument=False, skip=False):
            case = mode_root / case_name
            if not warm:
                case.mkdir()
                (case / "project").mkdir()
                (case / "home").mkdir()
                (case / "home/.nuget/NuGet").mkdir(parents=True)
                (case / "home/.nuget/NuGet/NuGet.Config").write_text(CONFIG)
                project = PROJECT
                if instrument:
                    messages = "".join(
                        f'<Message Importance="High" Text="REPRO_CONTEXT {key}=$([System.Environment]::GetEnvironmentVariable(\'{key}\'))" />'
                        for key in CAPTURE_KEYS
                    )
                    project = project.replace("</Project>",
                        '<Target Name="CaptureReproContext" BeforeTargets="_GetRequiredWorkloads">'
                        + messages + '</Target></Project>')
                (case / "project/dirs.proj").write_text(project)
                (case / "project/NuGet.config").write_text(CONFIG)
                (case / "project/global.json").write_text(json.dumps({
                    "sdk": {"version": VERSION, "rollForward": "patch", "allowPrerelease": False},
                    "msbuild-sdks": {"Microsoft.Build.Traversal": "4.1.0"},
                }, indent=2))
            env = {
                "PATH": os.environ["PATH"], "HOME": str(case / "home"),
                "LANG": "en_US.UTF-8", "DOTNET_ROOT": str(sdk_root),
                "DOTNET_CLI_HOME": str(case / "home"),
                "DOTNET_ADD_GLOBAL_TOOLS_TO_PATH": "0",
                "DOTNET_GENERATE_ASPNET_CERTIFICATE": "false", "DOTNET_NOLOGO": "1",
                "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
                "DOTNET_CLI_TELEMETRY_SESSIONID": os.environ["DOTNET_CLI_TELEMETRY_SESSIONID"],
                "DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE": "true",
                "DOTNET_CLI_USE_MSBUILD_SERVER": "0", "MSBUILDUSESERVER": "0",
                "MSBUILDDISABLENODEREUSE": "1",
                "NUGET_PACKAGES": str(case / "packages"),
                "NUGET_HTTP_CACHE_PATH": str(case / "http"),
                "NUGET_PLUGINS_CACHE_PATH": str(case / "plugins"),
                "NUGET_SCRATCH": str(case / "scratch"),
                "TMPDIR": str(case / "temp"),
            }
            (case / "temp").mkdir(exist_ok=True)
            command = {
                "workload": ["workload", "restore", "-v", "diag"],
                "evaluation": ["msbuild", "dirs.proj", "-getProperty:MSBuildProjectFullPath", "-v:diag", "-m:1", "-nr:false"],
                "restore": ["restore", "dirs.proj", "-v", "diag", "-m:1", "-nr:false"],
                "install": ["workload", "install", "wasm-tools", "--print-download-link-only", "--skip-manifest-update"],
                "info": ["--info"],
            }[operation]
            if skip and operation == "workload":
                command += ["--skip-manifest-update"]
            label = f"{mode}-{case_name}-{operation}-{'warm' if warm else 'cold'}"
            context = case / f"{label}-context.json"
            invocation = [sys.executable, str(pathlib.Path(__file__).resolve()),
                          "--execute", str(dotnet), str(context), *command]
            if mode == "sudo":
                invocation = ["/usr/bin/sudo", "-n", "-E", "--", *invocation]
            package_path = case / "packages/microsoft.build.traversal/4.1.0"
            before = package_path.exists()
            started = time.monotonic()
            process = subprocess.Popen(invocation, cwd=case / "project", env=env,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, start_new_session=True)
            try:
                output, _ = process.communicate(timeout=240)
                exit_code = process.returncode
                timed_out = False
            except subprocess.TimeoutExpired:
                if mode == "sudo":
                    subprocess.run(["/usr/bin/sudo", "-n", "/bin/kill", "-KILL",
                                    f"-{process.pid}"], env=env, check=True)
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                output, _ = process.communicate()
                output += "\nHARNESS: command exceeded 240 second bound.\n"
                exit_code, timed_out = 124, True
            clean = sanitized(output, root)
            (evidence / f"{label}.log").write_text(clean)
            pre_cli = json.loads(context.read_text()) if context.exists() else None
            row = {
                "label": label, "mode": mode, "operation": operation,
                "warm": warm, "instrumented": instrument, "command": command,
                "exitCode": exit_code, "timedOut": timed_out,
                "seconds": round(time.monotonic() - started, 2),
                "packageExistedBefore": before, "packageExistsAfter": package_path.exists(),
                "sdkPropsExistAfter": (package_path / "Sdk/Sdk.props").exists(),
                "sdkTargetsExistAfter": (package_path / "Sdk/Sdk.targets").exists(),
                "sdkNotFound": "MSB4236" in output or "specified could not be found" in output,
                "environmentBeforeCli": pre_cli,
                "effectiveCliContext": [line for line in clean.splitlines() if "REPRO_CONTEXT " in line],
            }
            row = json.loads(sanitized(json.dumps(row), root))
            results.append(row)
            (evidence / "summary.json").write_text(json.dumps(summary, indent=2))
            print(json.dumps({key: row[key] for key in
                              ("label", "exitCode", "packageExistedBefore", "packageExistsAfter", "sdkNotFound", "timedOut")}),
                  flush=True)

        run("info", "info")
        run("workload", "workload")
        run("workload", "workload", warm=True)
        run("evaluation", "evaluation")
        run("restore", "restore")
        run("restore", "workload", warm=True)
        run("skip-manifests", "workload", skip=True)
        run("context", "workload", instrument=True, skip=True)
        run("install-links", "install")
    (evidence / "NuGet.config").write_text(CONFIG)
    print("Completed bounded macOS controls; only sanitized evidence is uploaded.", flush=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--execute":
        execute()
    else:
        main()
