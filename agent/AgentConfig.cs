using System.Globalization;
using System.Text.Json;
using System.Text.Json.Serialization;

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

    // --- Post-patch restart policy -----------------------------------------
    // A patch that needs a reboot is not applied until the machine reboots, so
    // the restart cannot be optional. It can be postponed: the user is asked,
    // and "Restart later" buys another RebootDeferralMinutes, up to
    // MaxRebootDeferrals times. After that the restart proceeds with a final
    // warning and no choice. Defaults are deliberately conservative — a
    // machine with a user at the keyboard gets roughly six hours of grace.

    /// <summary>How long the restart prompt waits for an answer before
    /// treating the machine as unattended and restarting.</summary>
    public int RebootPromptTimeoutSeconds { get; set; } = 300;

    /// <summary>How long "Restart later" postpones the next prompt.</summary>
    public int RebootDeferralMinutes { get; set; } = 120;

    /// <summary>How many times a user may postpone. 0 disables deferral and
    /// restores the old forced-restart behaviour.</summary>
    public int MaxRebootDeferrals { get; set; } = 3;

    /// <summary>Grace period between the final warning and the restart, once
    /// there is no choice left.</summary>
    public int RebootFinalWarningSeconds { get; set; } = 600;

    // --- Post-patch restart state (written by the agent, not the installer) --

    /// <summary>Deferrals used against the reboot currently pending. Reset
    /// once the machine restarts or the pending reboot is satisfied.</summary>
    public int RebootDeferralsUsed { get; set; }

    /// <summary>When the next restart prompt is due, ISO-8601 UTC. Null when
    /// no reboot is pending. Persisted so a deferral survives a service
    /// restart — otherwise stopping the service would silently drop it.</summary>
    public string? NextRebootPromptAtUtc { get; set; }

    /// <summary>Boot the deferral state above belongs to. The state must
    /// outlive a service restart but not an actual reboot: a user who
    /// restarts on their own has satisfied the pending reboot, and carrying
    /// their spent deferrals into the next patch cycle would quietly give
    /// them fewer postponements than the policy promises.</summary>
    public string? RebootStateBootStamp { get; set; }

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

    /// <summary>NextRebootPromptAtUtc as a UTC instant, or null when unset or
    /// unparseable. JsonIgnore keeps it out of agent.json — the string field is
    /// the stored form. A malformed value reads as "no reboot pending" rather
    /// than throwing on every poll; the next deployment rewrites it.</summary>
    [JsonIgnore]
    public DateTime? NextRebootPromptAt =>
        DateTime.TryParse(NextRebootPromptAtUtc, CultureInfo.InvariantCulture,
                          DateTimeStyles.AdjustToUniversal | DateTimeStyles.AssumeUniversal,
                          out var t) ? t : null;

    /// <summary>Identifies the current boot. Derived from uptime rather than a
    /// WMI call, and rounded to the minute so clock drift and rounding do not
    /// make one boot look like several. Static, so it is never serialised.</summary>
    public static string CurrentBootStamp
    {
        get
        {
            var boot = DateTime.UtcNow - TimeSpan.FromMilliseconds(Environment.TickCount64);
            return new DateTime(boot.Year, boot.Month, boot.Day, boot.Hour, boot.Minute, 0, DateTimeKind.Utc)
                .ToString("yyyy-MM-ddTHH:mm:ssZ");
        }
    }

    /// <summary>Arm the next restart prompt and persist it.</summary>
    public void ScheduleRebootPrompt(DateTime whenUtc)
    {
        NextRebootPromptAtUtc = whenUtc.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ");
        RebootStateBootStamp = CurrentBootStamp;
        Save();
    }

    /// <summary>Drop deferral state left over from a previous boot. Returns
    /// true when something was cleared, so the caller can say so in the log.
    /// </summary>
    public bool ResetRebootStateIfRebooted()
    {
        if (RebootStateBootStamp == null || RebootStateBootStamp == CurrentBootStamp) return false;
        var hadState = NextRebootPromptAtUtc != null || RebootDeferralsUsed > 0;
        ClearPendingReboot();          // clears the stamp too, and persists
        return hadState;
    }

    /// <summary>Forget the pending reboot and its deferral budget. Called once
    /// the restart is committed, so the next patch run starts from a full
    /// allowance rather than inheriting a spent one.</summary>
    public void ClearPendingReboot()
    {
        NextRebootPromptAtUtc = null;
        RebootDeferralsUsed = 0;
        RebootStateBootStamp = null;
        Save();
    }
}
