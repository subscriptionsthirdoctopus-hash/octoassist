using System.Text.Json.Serialization;

namespace OctoAssistAgent.Models;

public class PendingDeployment
{
    [JsonPropertyName("target_id")] public int TargetId { get; set; }
    [JsonPropertyName("window_name")] public string WindowName { get; set; } = "";
    [JsonPropertyName("selected_packages")] public List<string> SelectedPackages { get; set; } = new();
}

public class DeploymentAttempt
{
    [JsonPropertyName("package_name")] public string PackageName { get; set; } = "";
    [JsonPropertyName("started_at")] public string StartedAt { get; set; } = "";
    [JsonPropertyName("finished_at")] public string FinishedAt { get; set; } = "";
    [JsonPropertyName("exit_code")] public int ExitCode { get; set; }
    [JsonPropertyName("success")] public bool Success { get; set; }
    [JsonPropertyName("needs_reboot")] public bool NeedsReboot { get; set; }
    [JsonPropertyName("stdout")] public string? Stdout { get; set; }
    [JsonPropertyName("stderr")] public string? Stderr { get; set; }
    [JsonPropertyName("method")] public string Method { get; set; } = "";
}

public class DeploymentFinish
{
    [JsonPropertyName("note")] public string Note { get; set; } = "";
    [JsonPropertyName("needs_reboot")] public bool NeedsReboot { get; set; }
}
