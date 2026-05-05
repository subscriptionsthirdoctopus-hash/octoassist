using System.Management;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using Microsoft.Extensions.Logging;
using Microsoft.Win32;
using OctoAssistAgent.Models;

namespace OctoAssistAgent;

public class AssetCollector
{
    private readonly ILogger<AssetCollector> _log;

    public AssetCollector(ILogger<AssetCollector> log) => _log = log;

    public AssetSnapshot Collect()
    {
        var snap = new AssetSnapshot
        {
            Os = ReadOs(),
            Cpu = ReadCpu(),
            Memory = ReadMemory(),
            Disks = ReadDisks(),
            Network = ReadNetwork(),
            Bios = ReadBios(),
            System = ReadSystem(),
            LoggedInUser = ReadLoggedInUser(),
            Software = ReadInstalledSoftware(),
        };
        _log.LogInformation("Collected snapshot: OS={os}, CPU={cpu}, Software={n}",
            snap.Os.Caption, snap.Cpu.Name, snap.Software.Count);
        return snap;
    }

    public string ReadMachineId()
    {
        try
        {
            using var searcher = new ManagementObjectSearcher("SELECT UUID FROM Win32_ComputerSystemProduct");
            foreach (ManagementObject mo in searcher.Get())
            {
                var uuid = mo["UUID"]?.ToString();
                if (!string.IsNullOrWhiteSpace(uuid) && uuid != "00000000-0000-0000-0000-000000000000")
                    return uuid;
            }
        }
        catch (Exception ex) { _log.LogWarning(ex, "Win32_ComputerSystemProduct.UUID failed"); }

        return Environment.MachineName + "-" + Environment.OSVersion.Platform;
    }

    public string ReadHostname() => Environment.MachineName;

    private OsInfo ReadOs()
    {
        var info = new OsInfo();
        try
        {
            using var searcher = new ManagementObjectSearcher(
                "SELECT Caption, Version, BuildNumber, OSArchitecture, InstallDate FROM Win32_OperatingSystem");
            foreach (ManagementObject mo in searcher.Get())
            {
                info.Caption = mo["Caption"]?.ToString();
                info.Version = mo["Version"]?.ToString();
                info.BuildNumber = mo["BuildNumber"]?.ToString();
                info.Architecture = mo["OSArchitecture"]?.ToString();
                var raw = mo["InstallDate"]?.ToString();
                if (!string.IsNullOrEmpty(raw))
                {
                    try { info.InstallDate = ManagementDateTimeConverter.ToDateTime(raw); } catch { }
                }
                break;
            }
        }
        catch (Exception ex) { _log.LogWarning(ex, "ReadOs failed"); }
        return info;
    }

    private CpuInfo ReadCpu()
    {
        var info = new CpuInfo();
        try
        {
            using var searcher = new ManagementObjectSearcher(
                "SELECT Name, NumberOfCores, NumberOfLogicalProcessors, MaxClockSpeed FROM Win32_Processor");
            foreach (ManagementObject mo in searcher.Get())
            {
                info.Name = mo["Name"]?.ToString()?.Trim();
                info.Cores = ToInt(mo["NumberOfCores"]);
                info.LogicalProcessors = ToInt(mo["NumberOfLogicalProcessors"]);
                info.MaxClockMhz = ToUint(mo["MaxClockSpeed"]);
                break;
            }
        }
        catch (Exception ex) { _log.LogWarning(ex, "ReadCpu failed"); }
        return info;
    }

    private MemoryInfo ReadMemory()
    {
        var info = new MemoryInfo();
        try
        {
            using var searcher = new ManagementObjectSearcher(
                "SELECT TotalPhysicalMemory FROM Win32_ComputerSystem");
            foreach (ManagementObject mo in searcher.Get())
            {
                var raw = mo["TotalPhysicalMemory"]?.ToString();
                if (ulong.TryParse(raw, out var bytes))
                    info.TotalGb = Math.Round(bytes / 1024.0 / 1024.0 / 1024.0, 2);
                break;
            }
        }
        catch (Exception ex) { _log.LogWarning(ex, "ReadMemory failed"); }
        return info;
    }

    private List<DiskInfo> ReadDisks()
    {
        var disks = new List<DiskInfo>();
        try
        {
            using var searcher = new ManagementObjectSearcher(
                "SELECT DeviceID, FileSystem, Size, FreeSpace, DriveType FROM Win32_LogicalDisk WHERE DriveType=3");
            foreach (ManagementObject mo in searcher.Get())
            {
                var d = new DiskInfo
                {
                    Drive = mo["DeviceID"]?.ToString() ?? "",
                    Filesystem = mo["FileSystem"]?.ToString(),
                };
                if (ulong.TryParse(mo["Size"]?.ToString(), out var size))
                    d.SizeGb = Math.Round(size / 1024.0 / 1024.0 / 1024.0, 2);
                if (ulong.TryParse(mo["FreeSpace"]?.ToString(), out var free))
                    d.FreeGb = Math.Round(free / 1024.0 / 1024.0 / 1024.0, 2);
                disks.Add(d);
            }
        }
        catch (Exception ex) { _log.LogWarning(ex, "ReadDisks failed"); }
        return disks;
    }

    private List<NetworkInfo> ReadNetwork()
    {
        var nets = new List<NetworkInfo>();
        try
        {
            foreach (var nic in NetworkInterface.GetAllNetworkInterfaces())
            {
                if (nic.OperationalStatus != OperationalStatus.Up) continue;
                if (nic.NetworkInterfaceType == NetworkInterfaceType.Loopback) continue;

                string? ip = null;
                foreach (var a in nic.GetIPProperties().UnicastAddresses)
                {
                    if (a.Address.AddressFamily == AddressFamily.InterNetwork)
                    { ip = a.Address.ToString(); break; }
                }
                nets.Add(new NetworkInfo
                {
                    Name = nic.Name,
                    Mac = string.Join(":", BitConverter.ToString(nic.GetPhysicalAddress().GetAddressBytes()).Split('-')),
                    Ip = ip,
                });
            }
        }
        catch (Exception ex) { _log.LogWarning(ex, "ReadNetwork failed"); }
        return nets;
    }

    private BiosInfo ReadBios()
    {
        var info = new BiosInfo();
        try
        {
            using var searcher = new ManagementObjectSearcher(
                "SELECT Manufacturer, SMBIOSBIOSVersion, SerialNumber FROM Win32_BIOS");
            foreach (ManagementObject mo in searcher.Get())
            {
                info.Manufacturer = mo["Manufacturer"]?.ToString();
                info.Version = mo["SMBIOSBIOSVersion"]?.ToString();
                info.Serial = mo["SerialNumber"]?.ToString();
                break;
            }
        }
        catch (Exception ex) { _log.LogWarning(ex, "ReadBios failed"); }
        return info;
    }

    private SystemInfo ReadSystem()
    {
        var info = new SystemInfo();
        try
        {
            using var searcher = new ManagementObjectSearcher(
                "SELECT Manufacturer, Model, Domain, Workgroup, PartOfDomain FROM Win32_ComputerSystem");
            foreach (ManagementObject mo in searcher.Get())
            {
                info.Manufacturer = mo["Manufacturer"]?.ToString();
                info.Model = mo["Model"]?.ToString();
                var partOfDomain = mo["PartOfDomain"] as bool? ?? false;
                if (partOfDomain) info.Domain = mo["Domain"]?.ToString();
                else info.Workgroup = mo["Workgroup"]?.ToString();
                break;
            }
        }
        catch (Exception ex) { _log.LogWarning(ex, "ReadSystem failed"); }
        return info;
    }

    private string? ReadLoggedInUser()
    {
        try
        {
            using var searcher = new ManagementObjectSearcher(
                "SELECT UserName FROM Win32_ComputerSystem");
            foreach (ManagementObject mo in searcher.Get())
            {
                var u = mo["UserName"]?.ToString();
                if (!string.IsNullOrWhiteSpace(u)) return u;
            }
        }
        catch (Exception ex) { _log.LogWarning(ex, "ReadLoggedInUser failed"); }
        return null;
    }

    private List<SoftwareEntry> ReadInstalledSoftware()
    {
        var list = new List<SoftwareEntry>();
        var roots = new[]
        {
            @"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
            @"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        };

        foreach (var rootPath in roots)
        {
            try
            {
                using var root = Registry.LocalMachine.OpenSubKey(rootPath);
                if (root == null) continue;
                foreach (var subName in root.GetSubKeyNames())
                {
                    using var k = root.OpenSubKey(subName);
                    if (k == null) continue;
                    var name = k.GetValue("DisplayName") as string;
                    if (string.IsNullOrWhiteSpace(name)) continue;
                    if ((k.GetValue("SystemComponent") as int? ?? 0) == 1) continue;

                    list.Add(new SoftwareEntry
                    {
                        Name = name!,
                        Version = k.GetValue("DisplayVersion") as string,
                        Publisher = k.GetValue("Publisher") as string,
                        InstallDate = k.GetValue("InstallDate") as string,
                    });
                }
            }
            catch (Exception ex) { _log.LogWarning(ex, "ReadInstalledSoftware failed at {root}", rootPath); }
        }

        return list
            .GroupBy(s => (s.Name, s.Version))
            .Select(g => g.First())
            .OrderBy(s => s.Name)
            .ToList();
    }

    private static int? ToInt(object? o) => int.TryParse(o?.ToString(), out var v) ? v : null;
    private static uint? ToUint(object? o) => uint.TryParse(o?.ToString(), out var v) ? v : null;
}
