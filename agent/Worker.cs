using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using OctoAssistAgent.Models;
using System.Diagnostics;
using System.Text;
using System.Text.Json;

namespace OctoAssistAgent;

public class Worker : BackgroundService
{
    private readonly ILogger<Worker> _log;
    private readonly ApiClient _api;
    private readonly AssetCollector _collector;

    private AgentConfig _cfg = null!;

    public Worker(ILogger<Worker> log, ApiClient api, AssetCollector collector)
    {
        _log = log;
        _api = api;
        _collector = collector;
    }

    protected override async Task ExecuteAsync(CancellationToken ct)
    {
        try
        {
            _cfg = AgentConfig.Load();
        }
        catch (Exception ex)
        {
            _log.LogCritical(ex, "Failed to load agent config — service cannot start");
            return;
        }

        _api.Configure(_cfg.ServerUrl);

        if (!_cfg.IsRegistered)
        {
            await EnsureRegistered(ct);
        }

        // First check-in immediately, then periodic.
        await SafeCheckin(ct);

        // Start the fast polling loop as a background task.
        var fastPollTask = Task.Run(() => FastPollingLoopAsync(ct), ct);

        var interval = TimeSpan.FromHours(Math.Max(1, _cfg.CheckinIntervalHours));
        using var timer = new PeriodicTimer(interval);
        try
        {
            while (await timer.WaitForNextTickAsync(ct))
            {
                await SafeCheckin(ct);
            }
        }
        catch (OperationCanceledException) { /* shutdown */ }

        try
        {
            await fastPollTask;
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "Fast polling loop task ended with an error");
        }
    }

    private async Task EnsureRegistered(CancellationToken ct)
    {
        var machineId = _collector.ReadMachineId();
        var hostname = _collector.ReadHostname();

        const int maxAttempts = 5;
        for (int attempt = 1; attempt <= maxAttempts; attempt++)
        {
            try
            {
                var r = await _api.RegisterAsync(_cfg.EnrolmentKey, machineId, hostname, ct);
                _cfg.AgentId = r.AgentId;
                _cfg.AgentToken = r.AgentToken;
                _cfg.MachineId = machineId;
                _cfg.Save();
                return;
            }
            catch (Exception ex) when (attempt < maxAttempts)
            {
                var backoff = TimeSpan.FromSeconds(Math.Pow(2, attempt));
                _log.LogWarning(ex, "Register attempt {n} failed, retrying in {s}s", attempt, backoff.TotalSeconds);
                await Task.Delay(backoff, ct);
            }
        }
        _log.LogError("Could not register agent after {n} attempts", maxAttempts);
    }

    private async Task SafeCheckin(CancellationToken ct)
    {
        if (!_cfg.IsRegistered)
        {
            _log.LogWarning("Skipping checkin — agent not registered");
            await EnsureRegistered(ct);
            if (!_cfg.IsRegistered) return;
        }

        try
        {
            var snap = _collector.Collect();
            await _api.CheckinAsync(_cfg.AgentToken, snap, ct);
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "Checkin failed; will retry on next interval");
        }
    }

    private async Task FastPollingLoopAsync(CancellationToken ct)
    {
        _log.LogInformation("Fast polling loop started (every 30 seconds)");
        // 30 seconds interval for sub-minute command propagation
        var interval = TimeSpan.FromSeconds(30);
        using var timer = new PeriodicTimer(interval);
        try
        {
            while (await timer.WaitForNextTickAsync(ct))
            {
                if (!_cfg.IsRegistered) continue;
                await PollAndExecuteActions(ct);
            }
        }
        catch (OperationCanceledException) { /* shutdown */ }
        catch (Exception ex)
        {
            _log.LogError(ex, "Fast polling loop encountered an error");
        }
    }

    private async Task PollAndExecuteActions(CancellationToken ct)
    {
        try
        {
            var actions = await _api.GetPendingActionsAsync(_cfg.AgentToken, ct);
            if (actions == null || actions.Count == 0) return;

            _log.LogInformation("Retrieved {count} pending remote actions", actions.Count);
            int processed = 0;

            foreach (var a in actions)
            {
                _log.LogInformation("Action {id}: {kind} — running", a.Id, a.Kind);

                // Mark in-progress server-side
                try
                {
                    await _api.StartActionAsync(_cfg.AgentToken, a.Id, ct);
                }
                catch (Exception ex)
                {
                    _log.LogWarning(ex, "Failed to mark action {id} as started", a.Id);
                }

                // Dispatch and execute action
                ActionResult result;
                try
                {
                    result = await ExecuteActionAsync(a, ct);
                }
                catch (TimeoutException)
                {
                    result = new ActionResult
                    {
                        Success = false,
                        ExitCode = 124,
                        Stderr = "timeout",
                    };
                }
                catch (Exception ex)
                {
                    result = new ActionResult
                    {
                        Success = false,
                        ExitCode = 1,
                        Stderr = $"{ex.GetType().Name}: {ex.Message}",
                    };
                }

                // Truncate output to keep payloads small
                if (result.Stdout != null && result.Stdout.Length > 15000)
                {
                    result.Stdout = result.Stdout.Substring(result.Stdout.Length - 15000);
                }
                if (result.Stderr != null && result.Stderr.Length > 4000)
                {
                    result.Stderr = result.Stderr.Substring(result.Stderr.Length - 4000);
                }

                // Post results back
                try
                {
                    await _api.PostActionResultAsync(_cfg.AgentToken, a.Id, result, ct);
                }
                catch (Exception ex)
                {
                    _log.LogError(ex, "Failed to post result for action {id}", a.Id);
                }

                _log.LogInformation("Action {id}: done success={success} exit={exitCode}",
                    a.Id, result.Success, result.ExitCode);
                processed++;
            }

            if (processed > 0)
            {
                _log.LogInformation("Fast cycle: {processed} action(s) processed. Triggering resync checkin.", processed);
                await SafeCheckin(ct);
            }
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Action poll/execute cycle failed");
        }
    }

    private async Task<ActionResult> ExecuteActionAsync(PendingAction action, CancellationToken ct)
    {
        var parameters = action.Params ?? new Dictionary<string, object>();
        switch (action.Kind.ToLowerInvariant())
        {
            case "run_executable":
                return await ActionRunExecutableAsync(parameters, ct);
            case "reboot":
                return await ActionRebootAsync(parameters, ct);
            case "lock_workstation":
                return await ActionLockWorkstationAsync(parameters, ct);
            case "send_toast":
                return await ActionSendToastAsync(parameters, ct);
            case "list_processes":
                return await ActionListProcessesAsync(parameters, ct);
            case "kill_process":
                return await ActionKillProcessAsync(parameters, ct);
            case "set_wallpaper":
                return await ActionSetWallpaperAsync(parameters, ct);
            case "reset_password":
                return await ActionResetPasswordAsync(parameters, ct);
            case "custom_powershell":
                return await ActionCustomPowershellAsync(parameters, ct);
            case "force_refresh":
                return await ActionForceRefreshAsync(parameters, ct);
            default:
                return new ActionResult
                {
                    Success = false,
                    ExitCode = -1,
                    Stderr = $"unknown action kind: {action.Kind}",
                };
        }
    }

    // -------------------- Action Handlers --------------------

    private async Task<ActionResult> ActionRunExecutableAsync(Dictionary<string, object> parameters, CancellationToken ct)
    {
        if (!parameters.TryGetValue("url", out var urlObj) || string.IsNullOrWhiteSpace(urlObj?.ToString()))
        {
            return new ActionResult { Success = false, ExitCode = -1, Stderr = "missing 'url' param" };
        }
        var url = urlObj.ToString()!;
        var args = parameters.TryGetValue("args", out var argsObj) ? argsObj?.ToString() ?? "" : "";

        // Determine filename and target path
        string fname;
        try
        {
            var uri = new Uri(url);
            fname = Path.GetFileName(uri.LocalPath);
        }
        catch
        {
            fname = "";
        }

        if (string.IsNullOrWhiteSpace(fname) || 
            (!fname.EndsWith(".exe", StringComparison.OrdinalIgnoreCase) &&
             !fname.EndsWith(".msi", StringComparison.OrdinalIgnoreCase) &&
             !fname.EndsWith(".msu", StringComparison.OrdinalIgnoreCase) &&
             !fname.EndsWith(".bat", StringComparison.OrdinalIgnoreCase) &&
             !fname.EndsWith(".cmd", StringComparison.OrdinalIgnoreCase)))
        {
            var hashed = url.GetHashCode().ToString("X8");
            fname = $"octo_install_{hashed}.exe";
        }

        var tempDir = Path.GetTempPath();
        var targetPath = Path.Combine(tempDir, fname);

        _log.LogInformation("run_executable: downloading {url} -> {targetPath}", url, targetPath);
        long fileSize = 0;
        try
        {
            using var client = new HttpClient();
            client.Timeout = TimeSpan.FromMinutes(10);
            var response = await client.GetAsync(url, ct);
            response.EnsureSuccessStatusCode();
            using (var fs = new FileStream(targetPath, FileMode.Create, FileAccess.Write, FileShare.None))
            {
                await response.Content.CopyToAsync(fs, ct);
                fileSize = fs.Length;
            }
        }
        catch (Exception ex)
        {
            return new ActionResult { Success = false, ExitCode = -1, Stderr = $"download failed: {ex.Message}" };
        }

        _log.LogInformation("run_executable: downloaded {size} bytes; running with args={args}", fileSize, args);
        int exitCode;
        string stdout = "", stderr = "";
        try
        {
            var isMsi = targetPath.EndsWith(".msi", StringComparison.OrdinalIgnoreCase);
            if (isMsi)
            {
                var finalArgs = $"/i \"{targetPath}\" " + (string.IsNullOrWhiteSpace(args) ? "/quiet /norestart" : args);
                var res = await RunProcessAsync("msiexec.exe", finalArgs, 3600000);
                exitCode = res.ExitCode;
                stdout = res.Stdout;
                stderr = res.Stderr;
            }
            else
            {
                var res = await RunProcessAsync(targetPath, args, 3600000);
                exitCode = res.ExitCode;
                stdout = res.Stdout;
                stderr = res.Stderr;
            }
        }
        finally
        {
            try
            {
                if (File.Exists(targetPath)) File.Delete(targetPath);
            }
            catch { }
        }

        return new ActionResult
        {
            Success = exitCode == 0,
            ExitCode = exitCode,
            Stdout = stdout,
            Stderr = stderr,
            Result = new Dictionary<string, object>
            {
                { "downloaded_bytes", fileSize },
                { "filename", fname },
                { "args", args }
            }
        };
    }

    private async Task<ActionResult> ActionRebootAsync(Dictionary<string, object> parameters, CancellationToken ct)
    {
        var delay = parameters.TryGetValue("delay_seconds", out var dObj) && int.TryParse(dObj?.ToString(), out var d) ? d : 60;
        var reason = parameters.TryGetValue("reason", out var rObj) ? rObj?.ToString() ?? "OctoAssist-initiated reboot" : "OctoAssist-initiated reboot";
        if (reason.Length > 127) reason = reason.Substring(0, 127);

        var res = await RunProcessAsync("shutdown.exe", $"/r /t {delay} /f /c \"{reason}\"", 15000);
        return new ActionResult
        {
            Success = res.ExitCode == 0,
            ExitCode = res.ExitCode,
            Stdout = res.Stdout,
            Stderr = res.Stderr,
            Result = new Dictionary<string, object> { { "delay_seconds", delay }, { "reason", reason } }
        };
    }

    private async Task<ActionResult> ActionLockWorkstationAsync(Dictionary<string, object> parameters, CancellationToken ct)
    {
        var psScript = @"$ErrorActionPreference = 'Stop'
$src = @""
using System;
using System.Runtime.InteropServices;
public class OctoLock {
    [DllImport(""wtsapi32.dll"", SetLastError=true)]
    public static extern bool WTSQueryUserToken(uint sessionId, out IntPtr token);
    [DllImport(""kernel32.dll"", SetLastError=true)]
    public static extern bool CloseHandle(IntPtr h);
    [DllImport(""advapi32.dll"", SetLastError=true, CharSet=CharSet.Unicode)]
    public static extern bool CreateProcessAsUser(
        IntPtr hToken, string lpApplicationName, string lpCommandLine,
        IntPtr lpProcessAttributes, IntPtr lpThreadAttributes, bool bInheritHandles,
        uint dwCreationFlags, IntPtr lpEnvironment, string lpCurrentDirectory,
        ref STARTUPINFO lpStartupInfo, out PROCESS_INFORMATION lpProcessInformation);
    [DllImport(""userenv.dll"", SetLastError=true)]
    public static extern bool CreateEnvironmentBlock(out IntPtr lpEnvironment, IntPtr hToken, bool bInherit);
    [DllImport(""userenv.dll"", SetLastError=true)]
    public static extern bool DestroyEnvironmentBlock(IntPtr lpEnvironment);
    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
    public struct STARTUPINFO {
        public Int32 cb; public string lpReserved; public string lpDesktop;
        public string lpTitle; public Int32 dwX; public Int32 dwY;
        public Int32 dwXSize; public Int32 dwYSize; public Int32 dwXCountChars;
        public Int32 dwYCountChars; public Int32 dwFillAttribute; public Int32 dwFlags;
        public Int16 wShowWindow; public Int16 cbReserved2; public IntPtr lpReserved2;
        public IntPtr hStdInput; public IntPtr hStdOutput; public IntPtr hStdError;
    }
    [StructLayout(LayoutKind.Sequential)]
    public struct PROCESS_INFORMATION {
        public IntPtr hProcess; public IntPtr hThread;
        public Int32 dwProcessId; public Int32 dwThreadId;
    }
}
""@
Add-Type -TypeDefinition $src
$active = @()
quser 2>$null | Select-Object -Skip 1 | ForEach-Object {
    if ($_ -match '\s+(\d+)\s+Active') { $active += [int]$matches[1] }
}
$launched = 0
foreach ($sid in $active) {
    $token = [IntPtr]::Zero
    if ([OctoLock]::WTSQueryUserToken([uint32]$sid, [ref]$token)) {
        $envBlock = [IntPtr]::Zero
        [OctoLock]::CreateEnvironmentBlock([ref]$envBlock, $token, $false) | Out-Null
        $si = New-Object OctoLock+STARTUPINFO
        $si.cb = [System.Runtime.InteropServices.Marshal]::SizeOf($si)
        $si.lpDesktop = 'winsta0\default'
        $pi = New-Object OctoLock+PROCESS_INFORMATION
        if ([OctoLock]::CreateProcessAsUser($token, $null,
                'rundll32.exe user32.dll,LockWorkStation',
                [IntPtr]::Zero, [IntPtr]::Zero, $false, 0x00000400,
                $envBlock, $null, [ref]$si, [ref]$pi)) {
            $launched++
            [OctoLock]::CloseHandle($pi.hProcess) | Out-Null
            [OctoLock]::CloseHandle($pi.hThread)  | Out-Null
        }
        if ($envBlock -ne [IntPtr]::Zero) { [OctoLock]::DestroyEnvironmentBlock($envBlock) | Out-Null }
        [OctoLock]::CloseHandle($token) | Out-Null
    }
}
""LOCKED:$launched""";

        var res = await RunPowerShellAsync(psScript, 25000);
        int locked = 0;
        if (res.Stdout != null)
        {
            var match = System.Text.RegularExpressions.Regex.Match(res.Stdout, @"LOCKED:(\d+)");
            if (match.Success) locked = int.Parse(match.Groups[1].Value);
        }
        bool success = locked > 0;
        return new ActionResult
        {
            Success = success,
            ExitCode = success ? 0 : (res.ExitCode != 0 ? res.ExitCode : 1),
            Stdout = res.Stdout != null && res.Stdout.Length > 500 ? res.Stdout.Substring(res.Stdout.Length - 500) : res.Stdout,
            Stderr = success ? "" : (res.Stderr != null && res.Stderr.Length > 500 ? res.Stderr.Substring(res.Stderr.Length - 500) : (res.Stderr ?? "no active user sessions")),
            Result = new Dictionary<string, object> { { "locked_sessions", locked } }
        };
    }

    private async Task<ActionResult> ActionSendToastAsync(Dictionary<string, object> parameters, CancellationToken ct)
    {
        var title = parameters.TryGetValue("title", out var tVal) ? tVal?.ToString() ?? "OctoAssist" : "OctoAssist";
        var body = parameters.TryGetValue("body", out var bVal) ? bVal?.ToString() ?? "" : "";
        if (title.Length > 80) title = title.Substring(0, 80);
        if (body.Length > 500) body = body.Substring(0, 500);

        var titleBase64 = Convert.ToBase64String(Encoding.Unicode.GetBytes(title));
        var bodyBase64 = Convert.ToBase64String(Encoding.Unicode.GetBytes(body));

        var psScript = @"$ErrorActionPreference = 'Stop'
$src = @""
using System;
using System.Runtime.InteropServices;
public class OctoToast {
    [DllImport(""wtsapi32.dll"", SetLastError=true, CharSet=CharSet.Unicode)]
    public static extern bool WTSSendMessage(
        IntPtr hServer, int SessionId,
        [MarshalAs(UnmanagedType.LPWStr)] string pTitle, int TitleLength,
        [MarshalAs(UnmanagedType.LPWStr)] string pMessage, int MessageLength,
        int Style, int Timeout, out int pResponse, bool bWait);
}
""@
Add-Type -TypeDefinition $src
$title = [System.Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('__TITLE_B64__'))
$body  = [System.Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('__BODY_B64__'))
$sessions = @()
try {
    quser 2>$null | Select-Object -Skip 1 | ForEach-Object {
        if ($_ -match '\s+(\d+)\s+(Active|Disc)') { $sessions += [int]$matches[1] }
    }
} catch {}
if ($sessions.Count -eq 0) {
    Add-Type -AssemblyName System.Management
    try {
        Get-CimInstance Win32_LogonSession -ErrorAction SilentlyContinue | ForEach-Object {
            if ($_.LogonType -eq 2 -or $_.LogonType -eq 10) { }
        }
    } catch {}
}
$count = 0
$errors = @()
foreach ($sid in $sessions) {
    $resp = 0
    try {
        $ok = [OctoToast]::WTSSendMessage([IntPtr]::Zero, $sid, $title,
                $title.Length*2, $body, $body.Length*2, 0x40, 0, [ref]$resp, $false)
        if ($ok) { $count++ } else { $errors += ("session {0}: WTSSendMessage returned false (last error {1})" -f $sid, [Runtime.InteropServices.Marshal]::GetLastWin32Error()) }
    } catch {
        $errors += ("session {0}: {1}" -f $sid, $_.Exception.Message)
    }
}
""DELIVERED:$count""
""SESSIONS:$($sessions -join ',')""
if ($errors.Count) { ""ERRORS:"" + ($errors -join ' | ') }"
            .Replace("__TITLE_B64__", titleBase64)
            .Replace("__BODY_B64__", bodyBase64);

        var res = await RunPowerShellAsync(psScript, 25000);
        int delivered = 0;
        if (res.Stdout != null)
        {
            var match = System.Text.RegularExpressions.Regex.Match(res.Stdout, @"DELIVERED:(\d+)");
            if (match.Success) delivered = int.Parse(match.Groups[1].Value);
        }
        bool success = delivered > 0;
        string note = "";
        if (!success)
        {
            if (res.Stdout != null && res.Stdout.Contains("SESSIONS:") && 
                System.Text.RegularExpressions.Regex.IsMatch(res.Stdout, @"SESSIONS:\s*\r?\n"))
            {
                note = "No active user sessions on this endpoint.";
            }
            else
            {
                note = "Toast couldn't reach any user session.";
            }
        }

        return new ActionResult
        {
            Success = success,
            ExitCode = success ? 0 : (res.ExitCode != 0 ? res.ExitCode : 1),
            Stdout = res.Stdout != null && res.Stdout.Length > 800 ? res.Stdout.Substring(res.Stdout.Length - 800) : res.Stdout,
            Stderr = success ? "" : (note + " " + (res.Stderr ?? "")),
            Result = new Dictionary<string, object> { { "delivered_to_sessions", delivered } }
        };
    }

    private async Task<ActionResult> ActionListProcessesAsync(Dictionary<string, object> parameters, CancellationToken ct)
    {
        var psScript = @"$ErrorActionPreference = 'SilentlyContinue'
Get-Process | Where-Object { $_.MainWindowTitle -ne '' -or $_.WorkingSet -gt 50MB } |
  Sort-Object -Property WorkingSet -Descending |
  Select-Object -First 60 -Property Name, Id, @{N='RAM_MB';E={[math]::Round($_.WorkingSet/1MB,1)}}, CPU, Path |
  ConvertTo-Json -Compress";

        var res = await RunPowerShellAsync(psScript, 30000);
        var procList = new List<Dictionary<string, object>>();
        try
        {
            if (!string.IsNullOrWhiteSpace(res.Stdout))
            {
                var doc = JsonDocument.Parse(res.Stdout);
                if (doc.RootElement.ValueKind == JsonValueKind.Array)
                {
                    foreach (var elem in doc.RootElement.EnumerateArray())
                    {
                        var dict = JsonSerializer.Deserialize<Dictionary<string, object>>(elem.GetRawText());
                        if (dict != null) procList.Add(dict);
                    }
                }
                else if (doc.RootElement.ValueKind == JsonValueKind.Object)
                {
                    var dict = JsonSerializer.Deserialize<Dictionary<string, object>>(doc.RootElement.GetRawText());
                    if (dict != null) procList.Add(dict);
                }
            }
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Failed to parse process list JSON");
        }

        return new ActionResult
        {
            Success = res.ExitCode == 0,
            ExitCode = res.ExitCode,
            Stdout = null,
            Stderr = res.Stderr,
            Result = new Dictionary<string, object> { { "processes", procList } }
        };
    }

    private async Task<ActionResult> ActionKillProcessAsync(Dictionary<string, object> parameters, CancellationToken ct)
    {
        var pidVal = parameters.TryGetValue("pid", out var pv) ? pv?.ToString() : null;
        var nameVal = parameters.TryGetValue("name", out var nv) ? nv?.ToString() : null;

        if (string.IsNullOrEmpty(pidVal) && string.IsNullOrEmpty(nameVal))
        {
            return new ActionResult { Success = false, ExitCode = -1, Stderr = "missing name or pid" };
        }

        string ps;
        if (!string.IsNullOrEmpty(pidVal))
        {
            ps = $"Stop-Process -Id {int.Parse(pidVal)} -Force -ErrorAction Stop";
        }
        else
        {
            var safe = nameVal!.Trim().TrimEnd('.', 'e', 'x', 'e', 'E', 'X', 'E');
            ps = $"Stop-Process -Name '{safe}' -Force -ErrorAction Stop";
        }

        var res = await RunPowerShellAsync(ps, 20000);
        return new ActionResult
        {
            Success = res.ExitCode == 0,
            ExitCode = res.ExitCode,
            Stdout = res.Stdout,
            Stderr = res.Stderr,
            Result = new Dictionary<string, object> { { "target", nameVal ?? $"pid={pidVal}" } }
        };
    }

    private async Task<ActionResult> ActionSetWallpaperAsync(Dictionary<string, object> parameters, CancellationToken ct)
    {
        if (!parameters.TryGetValue("url", out var urlObj) || string.IsNullOrWhiteSpace(urlObj?.ToString()))
        {
            return new ActionResult { Success = false, ExitCode = -1, Stderr = "missing 'url' param" };
        }
        var url = urlObj.ToString()!;
        var wpDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "OctoAssist");
        Directory.CreateDirectory(wpDir);
        var targetPath = Path.Combine(wpDir, "wallpaper.jpg");

        try
        {
            using var client = new HttpClient();
            var response = await client.GetAsync(url, ct);
            response.EnsureSuccessStatusCode();
            using (var fs = new FileStream(targetPath, FileMode.Create, FileAccess.Write, FileShare.None))
            {
                await response.Content.CopyToAsync(fs, ct);
            }
        }
        catch (Exception ex)
        {
            return new ActionResult { Success = false, ExitCode = -1, Stderr = $"download failed: {ex.Message}" };
        }

        var psScript = $@"$ErrorActionPreference = 'SilentlyContinue'
$img = '{targetPath}'
Get-ChildItem 'Registry::HKEY_USERS' | Where-Object {{ $_.Name -match 'S-1-5-21-' -and $_.Name -notlike '*_Classes' }} |
  ForEach-Object {{
    $sid = $_.PSChildName
    $key = ""Registry::HKEY_USERS\$sid\Control Panel\Desktop""
    if (Test-Path $key) {{
      Set-ItemProperty -Path $key -Name 'Wallpaper' -Value $img -ErrorAction SilentlyContinue
      Set-ItemProperty -Path $key -Name 'WallpaperStyle' -Value '10' -ErrorAction SilentlyContinue
    }}
  }}
Add-Type @""
using System;
using System.Runtime.InteropServices;
public static class W {{
  [DllImport(""user32.dll"", CharSet=CharSet.Auto)]
  public static extern int SystemParametersInfo(int uAction, int uParam, string lpvParam, int fuWinIni);
}}
""@
[W]::SystemParametersInfo(20, 0, $img, 3) | Out-Null
'OK'";

        var res = await RunPowerShellAsync(psScript, 30000);
        bool success = res.ExitCode == 0 && res.Stdout != null && res.Stdout.Contains("OK");
        return new ActionResult
        {
            Success = success,
            ExitCode = res.ExitCode,
            Stdout = res.Stdout,
            Stderr = res.Stderr,
            Result = new Dictionary<string, object> { { "saved_to", targetPath } }
        };
    }

    private async Task<ActionResult> ActionResetPasswordAsync(Dictionary<string, object> parameters, CancellationToken ct)
    {
        var username = parameters.TryGetValue("username", out var uObj) ? uObj?.ToString() : null;
        var newPassword = parameters.TryGetValue("new_password", out var pObj) ? pObj?.ToString() : null;

        if (string.IsNullOrWhiteSpace(username) || string.IsNullOrWhiteSpace(newPassword))
        {
            return new ActionResult { Success = false, ExitCode = -1, Stderr = "missing username or new_password" };
        }

        var res = await RunProcessAsync("net.exe", $"user \"{username}\" \"{newPassword}\"", 30000);
        var cleanStdout = res.Stdout?.Replace(newPassword, "***") ?? "";
        var cleanStderr = res.Stderr?.Replace(newPassword, "***") ?? "";

        return new ActionResult
        {
            Success = res.ExitCode == 0,
            ExitCode = res.ExitCode,
            Stdout = cleanStdout,
            Stderr = cleanStderr,
            Result = new Dictionary<string, object> { { "username", username }, { "scope", "local" } }
        };
    }

    private async Task<ActionResult> ActionCustomPowershellAsync(Dictionary<string, object> parameters, CancellationToken ct)
    {
        var script = parameters.TryGetValue("script", out var sObj) ? sObj?.ToString() ?? "" : "";
        if (string.IsNullOrWhiteSpace(script))
        {
            return new ActionResult { Success = false, ExitCode = -1, Stderr = "missing 'script' param" };
        }

        var res = await RunPowerShellAsync(script, 600000);
        return new ActionResult
        {
            Success = res.ExitCode == 0,
            ExitCode = res.ExitCode,
            Stdout = res.Stdout,
            Stderr = res.Stderr,
            Result = new Dictionary<string, object> { { "script_length", script.Length } }
        };
    }

    private async Task<ActionResult> ActionForceRefreshAsync(Dictionary<string, object> parameters, CancellationToken ct)
    {
        try
        {
            var snap = _collector.Collect();
            await _api.CheckinAsync(_cfg.AgentToken, snap, ct);
            return new ActionResult
            {
                Success = true,
                ExitCode = 0,
                Result = new Dictionary<string, object>
                {
                    { "os", snap.Os.Caption ?? "" },
                    { "software_count", snap.Software.Count },
                    { "snapshot_at", snap.SnapshotAt }
                }
            };
        }
        catch (Exception ex)
        {
            return new ActionResult
            {
                Success = false,
                ExitCode = 1,
                Stderr = $"{ex.GetType().Name}: {ex.Message}"
            };
        }
    }

    // -------------------- Core Shell Runners --------------------

    private static async Task<(int ExitCode, string Stdout, string Stderr)> RunProcessAsync(string filename, string arguments, int timeoutMs)
    {
        using var process = new Process();
        process.StartInfo = new ProcessStartInfo
        {
            FileName = filename,
            Arguments = arguments,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
        };

        var stdoutBuilder = new StringBuilder();
        var stderrBuilder = new StringBuilder();

        process.OutputDataReceived += (s, e) => { if (e.Data != null) stdoutBuilder.AppendLine(e.Data); };
        process.ErrorDataReceived += (s, e) => { if (e.Data != null) stderrBuilder.AppendLine(e.Data); };

        process.Start();
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();

        using var cts = new CancellationTokenSource(timeoutMs);
        try
        {
            await process.WaitForExitAsync(cts.Token);
        }
        catch (OperationCanceledException)
        {
            if (!process.HasExited)
            {
                process.Kill(true);
            }
            throw new TimeoutException($"Process '{filename}' timed out after {timeoutMs}ms");
        }

        return (process.ExitCode, stdoutBuilder.ToString(), stderrBuilder.ToString());
    }

    private static async Task<(int ExitCode, string Stdout, string Stderr)> RunPowerShellAsync(string script, int timeoutMs)
    {
        var bytes = Encoding.Unicode.GetBytes(script);
        var base64 = Convert.ToBase64String(bytes);
        return await RunProcessAsync("powershell.exe", $"-NoProfile -ExecutionPolicy Bypass -EncodedCommand {base64}", timeoutMs);
    }
}
