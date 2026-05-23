namespace OctoAssistAgent;

/// <summary>
/// Bootstrap mode: invoked by the MSI installer right after files are laid down.
/// Writes the initial agent.json configuration so the Windows Service can start.
///
/// Invocation:
///   OctoAssistAgent.exe --bootstrap --server-url=https://octoassist.tema.local --enrolment-key=XXXX [--interval-hours=6]
/// </summary>
public static class Bootstrap
{
    public static int Run(string[] args)
    {
        try
        {
            var serverUrl = GetArg(args, "--server-url");
            var enrolmentKey = GetArg(args, "--enrolment-key");
            var intervalHours = int.TryParse(GetArg(args, "--interval-hours", optional: true), out var h) ? h : 6;

            if (string.IsNullOrWhiteSpace(serverUrl) || string.IsNullOrWhiteSpace(enrolmentKey))
            {
                Console.Error.WriteLine("Bootstrap: --server-url and --enrolment-key are required");
                return 2;
            }

            var cfg = new AgentConfig
            {
                ServerUrl = serverUrl!,
                EnrolmentKey = enrolmentKey!,
                CheckinIntervalHours = intervalHours,
            };
            cfg.Save();
            Console.Out.WriteLine("OctoAssist agent bootstrapped successfully.");
            return 0;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"Bootstrap failed: {ex.Message}");
            return 1;
        }
    }

    private static string? GetArg(string[] args, string key, bool optional = false)
    {
        foreach (var a in args)
        {
            if (a.StartsWith(key + "=", StringComparison.OrdinalIgnoreCase))
                return a.Substring(key.Length + 1).Trim('"');
        }
        // Also support space-separated form: --server-url X
        for (int i = 0; i < args.Length - 1; i++)
        {
            if (string.Equals(args[i], key, StringComparison.OrdinalIgnoreCase))
                return args[i + 1].Trim('"');
        }
        return optional ? null : null;
    }
}
