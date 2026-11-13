# Mac Catalyst pack reproduction

Reproduces [dotnet/sdk#48041](https://github.com/dotnet/sdk/issues/48041) using
GitHub-hosted macOS 15 and installed Xcode. This is not a source-built SDK test.

The first environment pins the reported SDK 9.0.202, workload set 9.0.200, and
Xcode 16.2. The second pins the .NET 10 GA SDK/workload set and Xcode 26.0.1 as a
newer-version control, not as a claim about the latest serviced SDK.

Each environment builds and packs the actual MAUI template, restricted to Mac
Catalyst, in Debug and Release. Cases use a concrete host RID, a one-entry RID
list, a two-entry RID list, and unmodified RID defaults. The Release default case
also performs unqualified pack after explicit Release build. A small Apple
class-library pack is a component/toolchain control.

Capture.targets observes runtimeconfig generation, Apple app-bundle dispatch,
built output groups, and NuGet pack task inputs. After each build, explicit
output-group probes query the outer and concrete-RID inner contexts without
building them again. Files and package contents include SHA-256 digests.

The workflow has bounded commands, a job timeout, and concurrency cancellation.
Only sanitized text/JSON/project evidence is uploaded. Raw binary logs remain on
the ephemeral runner because an environment-bearing binlog is not safe to publish
without separate review. Expected build/pack failures remain visible in the
command table; workflow success means collection completed, not that pack passed.

The current push run repeats only the two-entry list cases; the previous full
run's CLI-escaped two-entry input represented a single literal RID and is excluded.
Lists are now written as literal project properties. Manual dispatch defaults to
the full matrix; the selected modes are recorded in provenance.json.
