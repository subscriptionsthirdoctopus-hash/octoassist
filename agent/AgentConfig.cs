using System.Text.Json;

namespace OctoAssistAgent;

public class AgentConfig
{
    private static readonly string ConfigDir = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),
        "OctoAssist");

    private static readonly string ConfigPath = Path.Combine(ConfigDir, "agent.json");

    public string ServerUrl { get; set; } = "";
    public string EnrolmentKey { get; set; } = "";
    public string AgentToken { get; set; } = "";
    public int? AgentId { get; set; }
    public string? MachineId { get; set; }
    public int CheckinIntervalHours { get; set; } = 6;

    public static AgentConfig Load()
    {
        if (!File.Exists(ConfigPath))
        {
            throw new FileNotFoundException(
                $"OctoAssist agent config not found at {ConfigPath}. " +
                "The MSI installer is responsible for writing this file.");
        }
        var json = File.ReadAllText(ConfigPath);
        var cfg = JsonSerializer.Deserialize<AgentConfig>(json,
            new JsonSerializerOptions { PropertyNameCaseInsensitive = true });
        return cfg ?? throw new InvalidDataException("Empty or malformed agent.json");
    }

    public void Save()
    {
        Directory.CreateDirectory(ConfigDir);
        var json = JsonSerializer.Serialize(this, new JsonSerializerOptions { WriteIndented = true });
        File.WriteAllText(ConfigPath, json);
    }

    public bool IsRegistered => !string.IsNullOrEmpty(AgentToken) && AgentId.HasValue;
}
