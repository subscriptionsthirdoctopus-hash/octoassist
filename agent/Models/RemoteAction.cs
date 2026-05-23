using System.Text.Json.Serialization;

namespace OctoAssistAgent.Models;

public class PendingAction
{
    [JsonPropertyName("id")] public int Id { get; set; }
    [JsonPropertyName("kind")] public string Kind { get; set; } = "";
    [JsonPropertyName("params")] public Dictionary<string, object>? Params { get; set; }
}

public class ActionResult
{
    [JsonPropertyName("exit_code")] public int? ExitCode { get; set; }
    [JsonPropertyName("stdout")] public string? Stdout { get; set; }
    [JsonPropertyName("stderr")] public string? Stderr { get; set; }
    [JsonPropertyName("result")] public Dictionary<string, object>? Result { get; set; }
    [JsonPropertyName("success")] public bool Success { get; set; }
}
