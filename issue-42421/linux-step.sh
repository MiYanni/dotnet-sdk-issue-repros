#!/usr/bin/env bash
set -euo pipefail
mode=$1
root=$2
label=$3
artifacts=${4:-}
cd "$root"
export DOTNET_ADD_GLOBAL_TOOLS_TO_PATH=0 DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1
export DOTNET_CLI_HOME="$HOME/.issue42421/cli-home"
export NUGET_PACKAGES="$HOME/.issue42421/packages"
export NUGET_HTTP_CACHE_PATH="$HOME/.issue42421/http"
export NUGET_PLUGINS_CACHE_PATH="$HOME/.issue42421/plugins"
dotnet=${DOTNET42421:-$HOME/dotnet42421/dotnet}
private="$root/private-evidence/$label"
mkdir -p "$private" "$HOME/.issue42421/probe"
probe="$HOME/.issue42421/probe/Probe.dll"
options=()
if [[ -n "$artifacts" ]]; then options+=("-p:ArtifactsPath=$artifacts"); fi
if [[ "$mode" == prepare ]]; then
    "$dotnet" --info > "$private/dotnet-info.txt"
    uname -a > "$private/kernel.txt"
    set +e
    "$dotnet" build Probe/Probe.csproj --configfile NuGet.config -m:1 -nr:false \
        -p:OutputPath="$HOME/.issue42421/probe/" \
        -p:BaseIntermediateOutputPath="$HOME/.issue42421/probe-obj/" \
        -p:UseSharedCompilation=false > "$private/build.raw" 2>&1
    status=$?
    set -e
    grep -E 'error |warning |Build succeeded|Build FAILED' "$private/build.raw" > "$private/build-diagnostics.txt" || true
    if [[ "$status" != 0 ]]; then
        cat "$private/build-diagnostics.txt"
        python3 sanitize.py "$root/private-evidence" "$root/evidence"
        exit "$status"
    fi
    test -f "$probe"
    exit 0
fi
if [[ "$mode" == bad-env ]]; then
    export NUGET_FALLBACK_PACKAGES="$NUGET_PACKAGES:$NUGET_HTTP_CACHE_PATH"
    mode=restore
fi
if [[ "$mode" == restore ]]; then
    command=("$dotnet" restore App/App.csproj --configfile NuGet.config --force -v:diag -m:1 -nr:false "${options[@]}" "-bl:$private/restore.binlog")
elif [[ "$mode" == probe ]]; then
    command=("$dotnet" msbuild App/App.csproj -t:ProbeGraph -v:normal -m:1 -nr:false "${options[@]}" "-p:ProbeAssembly=$probe" "-p:ProbeOutput=$private" "-bl:$private/consumer.binlog")
elif [[ "$mode" == standard ]]; then
    command=("$dotnet" msbuild App/App.csproj -t:ResolvePackageAssets -v:normal -m:1 -nr:false "${options[@]}" "-p:DisablePackageAssetsCache=true" "-bl:$private/standard-consumer.binlog")
else
    echo "Unknown mode: $mode" >&2
    exit 2
fi
started=$(date -u +%FT%T.%NZ)
set +e
strace -f -ttt -e trace=openat,rename,renameat,renameat2,unlink \
    -o "$private/io.raw" "${command[@]}" > "$private/command.raw" 2>&1 &
trace_pid=$!
wait "$trace_pid"
status=$?
set -e
python3 - "$private" "$mode" "$started" "$status" "$trace_pid" <<'PY'
import datetime, json, os, pathlib, re, sys
directory = pathlib.Path(sys.argv[1])
record = dict(mode=sys.argv[2], startedUtc=sys.argv[3], exitCode=int(sys.argv[4]),
              stracePid=int(sys.argv[5]), host="Linux",
              finishedUtc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
              fallbackEnvironment=os.environ.get("NUGET_FALLBACK_PACKAGES"))
(directory/"invocation.json").write_text(json.dumps(record, indent=2))
lines=(directory/"io.raw").read_text().splitlines()
selected=[line for line in lines if re.search(r'project\.assets\.json|\.nuget\.dgspec\.json|\.nuget\.g\.props|\.nuget\.g\.targets',line)]
(directory/"file-io.txt").write_text("\n".join(selected))
lines=(directory/"command.raw").read_text().splitlines()
selected=[line for line in lines if re.search(r'Writing assets file|Generating MSBuild file|Persisting dg|error |fallback package folder|Captured consumer|SDK Version:',line)]
(directory/"diagnostics.txt").write_text("\n".join(selected))
PY
python3 snapshot.py "$root" "$private"
echo "$label $mode exit=$status"
