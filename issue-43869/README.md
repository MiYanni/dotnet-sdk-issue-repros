🤖Copilot🤖
Isolated public reproduction of https://github.com/dotnet/sdk/issues/43869.

The workflow uses ephemeral Intel and Arm64 macOS runners and the SHA-512-verified official
.NET SDK 8.0.400. It exercises the original Traversal 4.1.0 project with empty,
independent NuGet package/HTTP/home caches, normally and under sudo. The source is
only nuget.org; no private feed, credential provider, secret, or user machine is
involved. Sudo is confined to the ephemeral runner.

Each mode runs first workload restore, a warm repeat, independent first
evaluation, independent first ordinary restore, then workload restore after
ordinary restore. Additional isolated skip-manifest-update and instrumented
workload controls distinguish manifest activity from evaluation and capture
the CLI's effective HOME after its sudo-home override. The instrumented project
has the same SDK reference but adds an environment-only diagnostic target;
its results are labeled separately from the exact reported project. A workload
install download-link-only control exercises that command's sudo initialization
without installing an unrelated workload or Apple packs.

All child processes receive an allowlisted environment without GitHub tokens.
Only selected environment names, SDK/package outcomes, public source configuration,
and sanitized command logs are uploaded. No raw binlog or complete host environment
is uploaded. A negative hosted-runner result does not establish that the reporter's
custom macOS agent works.
