using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

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
}
