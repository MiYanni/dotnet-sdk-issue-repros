# Cross-host SDK issue 42421 evidence

This standalone public fixture pins SDK 10.0.100 and Newtonsoft.Json 13.0.3.
App references Library, which references Sibling. Windows and WSL restore the same
files; the Ubuntu-only job is a separate-host control.

The diagnostic task executes the installed SDK's unmodified ResolvePackageAssets
task with evaluated paths, then inspects its build-engine-registered LockFile.
`consumedPackageFolders` therefore records the parsed object used by the consumer,
not an inference from a later disk snapshot. Before/after file hashes are separate
observations; they are not represented as an atomic hash of the original read.
The probe bypasses the task's disk cache, not the lockfile object cache.

Cases distinguish default Windows CLI/VS MSBuild restore from a deliberately
configured but real Windows fallback cache populated by ordinary restore.
The latter is a controlled cross-host configuration case, not proof that the VS
IDE naturally injects the same setting. Scheduled and concurrent Windows restores
model competing writers; no VS IDE/debugger is launched. Every Windows restore
has PID, command, time interval, and NuGet write messages. Linux strace captures
file operations and PIDs. Windows attribution is invocation-level NuGet logging,
not an independent kernel filesystem trace.

Only allowlisted JSON and filtered text are uploaded. No environment dump,
credentials, raw command logs, raw strace, or binary logs are published. Binlogs
are generated on the transient runners but intentionally not uploaded to this
public repository. Expected negative cases remain failures in the evidence;
the harness requires successful clean and isolated controls.
