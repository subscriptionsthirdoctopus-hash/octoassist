namespace OctoAssistAgent.Models;

public class AssetSnapshot
{
    public DateTime SnapshotAt { get; set; } = DateTime.UtcNow;
    public OsInfo Os { get; set; } = new();
    public CpuInfo Cpu { get; set; } = new();
    public MemoryInfo Memory { get; set; } = new();
    public List<DiskInfo> Disks { get; set; } = new();
    public List<NetworkInfo> Network { get; set; } = new();
    public BiosInfo Bios { get; set; } = new();
    public SystemInfo System { get; set; } = new();
    public string? LoggedInUser { get; set; }
    public List<SoftwareEntry> Software { get; set; } = new();
}

public class OsInfo
{
    public string? Caption { get; set; }
    public string? Version { get; set; }
    public string? BuildNumber { get; set; }
    public string? Architecture { get; set; }
    public DateTime? InstallDate { get; set; }
}

public class CpuInfo
{
    public string? Name { get; set; }
    public int? Cores { get; set; }
    public int? LogicalProcessors { get; set; }
    public uint? MaxClockMhz { get; set; }
}

public class MemoryInfo
{
    public double? TotalGb { get; set; }
}

public class DiskInfo
{
    public string Drive { get; set; } = "";
    public string? Filesystem { get; set; }
    public double? SizeGb { get; set; }
    public double? FreeGb { get; set; }
}

public class NetworkInfo
{
    public string Name { get; set; } = "";
    public string? Mac { get; set; }
    public string? Ip { get; set; }
}

public class BiosInfo
{
    public string? Manufacturer { get; set; }
    public string? Version { get; set; }
    public string? Serial { get; set; }
}

public class SystemInfo
{
    public string? Manufacturer { get; set; }
    public string? Model { get; set; }
    public string? Domain { get; set; }
    public string? Workgroup { get; set; }
}

public class SoftwareEntry
{
    public string Name { get; set; } = "";
    public string? Version { get; set; }
    public string? Publisher { get; set; }
    public string? InstallDate { get; set; }
}
