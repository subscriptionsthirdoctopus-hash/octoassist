using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Logging.EventLog;
using OctoAssistAgent;

if (args.Length > 0 && args[0].Equals("--bootstrap", StringComparison.OrdinalIgnoreCase))
{
    return Bootstrap.Run(args);
}

var builder = Host.CreateApplicationBuilder(args);

builder.Services.AddWindowsService(options =>
{
    options.ServiceName = "OctoAssistAgent";
});

builder.Services.Configure<EventLogSettings>(opts =>
{
    opts.SourceName = "OctoAssistAgent";
    opts.LogName = "Application";
});

builder.Logging.AddEventLog();

builder.Services.AddSingleton<AgentConfig>();
builder.Services.AddSingleton<AssetCollector>();
builder.Services.AddHttpClient<ApiClient>();
builder.Services.AddHostedService<Worker>();

var host = builder.Build();
host.Run();
return 0;
