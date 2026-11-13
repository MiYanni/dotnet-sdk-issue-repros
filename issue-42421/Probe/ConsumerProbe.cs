using System.Collections;
using System.Diagnostics;
using System.Reflection;
using System.Runtime.Loader;
using System.Security.Cryptography;
using System.Text.Json;
using Microsoft.Build.Framework;

public sealed class ConsumerProbe : Microsoft.Build.Utilities.Task
{
    [Required] public string TaskAssembly { get; set; } = "";
    [Required] public string AssetsPath { get; set; } = "";
    [Required] public string CachePath { get; set; } = "";
    [Required] public string ProjectPath { get; set; } = "";
    [Required] public string Framework { get; set; } = "";
    [Required] public string EvidencePath { get; set; } = "";
    public ITaskItem[] Values { get; set; } = [];

    public override bool Execute()
    {
        string path = Path.GetFullPath(AssetsPath, Path.GetDirectoryName(ProjectPath)!);
        string key = "LockFileCache:" + path;
        var engine = (IBuildEngine4)BuildEngine;
        object? prior = engine.GetRegisteredTaskObject(key, RegisteredTaskObjectLifetime.Build);
        var record = new Dictionary<string, object?>
        {
            ["project"] = ProjectPath,
            ["assetsPath"] = path,
            ["cachePath"] = CachePath,
            ["host"] = OperatingSystem.IsWindows() ? "Windows" : "Linux",
            ["pid"] = Environment.ProcessId,
            ["processStartedUtc"] = Process.GetCurrentProcess().StartTime.ToUniversalTime(),
            ["startedUtc"] = DateTime.UtcNow,
            ["evaluatedValues"] = Values.ToDictionary(item => item.ItemSpec, item => item.GetMetadata("Value")),
            ["diskBefore"] = Snapshot(path)
        };
        string taskDirectory = Path.GetDirectoryName(TaskAssembly)!;
        string sdkDirectory = Path.GetFullPath(Path.Combine(taskDirectory, "..", "..", "..", ".."));
        Assembly? Resolve(AssemblyLoadContext context, AssemblyName name)
        {
            foreach (string directory in new[] { taskDirectory, sdkDirectory })
            {
                string candidate = Path.Combine(directory, name.Name + ".dll");
                if (File.Exists(candidate))
                    return context.LoadFromAssemblyPath(candidate);
            }
            return null;
        }
        AssemblyLoadContext.Default.Resolving += Resolve;
        try
        {
            Assembly assembly = Assembly.LoadFrom(TaskAssembly);
            Type type = assembly.GetType("Microsoft.NET.Build.Tasks.ResolvePackageAssets", throwOnError: true)!;
            var task = (ITask)Activator.CreateInstance(type)!;
            task.BuildEngine = BuildEngine;
            void Set(string name, object value) => type.GetProperty(name)!.SetValue(task, value);
            Set("ProjectAssetsFile", path);
            Set("ProjectAssetsCacheFile", CachePath);
            Set("ProjectPath", ProjectPath);
            Set("TargetFramework", Framework);
            Set("DefaultImplicitPackages", "Microsoft.NETCore.App");
            Set("DotNetAppHostExecutableNameWithoutExtension", "apphost");
            Set("DisablePackageAssetsCache", true);
            record["taskAssembly"] = assembly.Location;
            record["taskAssemblyVersion"] = assembly.GetName().Version?.ToString();
            record["taskAssemblySha256"] = Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(assembly.Location)));
            try
            {
                record["succeeded"] = task.Execute();
            }
            catch (Exception ex)
            {
                record["succeeded"] = false;
                record["consumerException"] = ex.ToString();
                Log.LogMessage(MessageImportance.High, "Captured consumer failure for {0}: {1}", ProjectPath, ex.Message);
            }
            object? consumed = engine.GetRegisteredTaskObject(key, RegisteredTaskObjectLifetime.Build);
            record["registeredLockFileFound"] = consumed != null;
            record["reusedPriorLockFile"] = prior != null && ReferenceEquals(prior, consumed);
            if (consumed != null)
            {
                var folders = (IEnumerable)consumed.GetType().GetProperty("PackageFolders")!.GetValue(consumed)!;
                record["consumedPackageFolders"] = folders.Cast<object>()
                    .Select(folder => (string)folder.GetType().GetProperty("Path")!.GetValue(folder)!).ToArray();
                record["nugetProjectModelVersion"] = consumed.GetType().Assembly.GetName().Version?.ToString();
            }
            record["finishedUtc"] = DateTime.UtcNow;
            record["diskAfter"] = Snapshot(path);
            Directory.CreateDirectory(EvidencePath);
            File.WriteAllText(Path.Combine(EvidencePath, Path.GetFileNameWithoutExtension(ProjectPath) + ".consumer.json"),
                JsonSerializer.Serialize(record, new JsonSerializerOptions { WriteIndented = true }));
            if (consumed == null)
            {
                Log.LogError("The exact consumed LockFile was not found; this is a harness evidence failure.");
                return false;
            }
            return true;
        }
        finally
        {
            AssemblyLoadContext.Default.Resolving -= Resolve;
        }
    }

    private static object? Snapshot(string path)
    {
        if (!File.Exists(path))
            return null;
        byte[] bytes = File.ReadAllBytes(path);
        using JsonDocument document = JsonDocument.Parse(bytes);
        return new
        {
            sha256 = Convert.ToHexString(SHA256.HashData(bytes)),
            mtimeUtc = File.GetLastWriteTimeUtc(path),
            packageFolders = document.RootElement.GetProperty("packageFolders").EnumerateObject().Select(p => p.Name).ToArray()
        };
    }
}
