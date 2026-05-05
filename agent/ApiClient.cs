using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.Extensions.Logging;
using OctoAssistAgent.Models;

namespace OctoAssistAgent;

public class ApiClient
{
    private readonly HttpClient _http;
    private readonly ILogger<ApiClient> _log;

    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    public ApiClient(HttpClient http, ILogger<ApiClient> log)
    {
        _http = http;
        _log = log;
        _http.Timeout = TimeSpan.FromSeconds(30);
        _http.DefaultRequestHeaders.UserAgent.Add(new ProductInfoHeaderValue("OctoAssistAgent", "0.1.0"));
    }

    public void Configure(string serverUrl)
    {
        _http.BaseAddress = new Uri(serverUrl.TrimEnd('/') + "/");
    }

    public async Task<RegisterResult> RegisterAsync(string enrolmentKey, string machineId, string hostname, CancellationToken ct)
    {
        var body = new { enrolment_key = enrolmentKey, machine_id = machineId, hostname };
        var resp = await _http.PostAsJsonAsync("api/v1/agent/register", body, JsonOpts, ct);
        resp.EnsureSuccessStatusCode();
        var result = await resp.Content.ReadFromJsonAsync<RegisterResult>(JsonOpts, ct);
        if (result == null) throw new InvalidOperationException("Empty register response");
        _log.LogInformation("Agent registered: agent_id={id}", result.AgentId);
        return result;
    }

    public async Task CheckinAsync(string agentToken, AssetSnapshot snapshot, CancellationToken ct)
    {
        using var req = new HttpRequestMessage(HttpMethod.Post, "api/v1/agent/checkin");
        req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", agentToken);
        req.Content = JsonContent.Create(snapshot, options: JsonOpts);

        using var resp = await _http.SendAsync(req, ct);
        if (!resp.IsSuccessStatusCode)
        {
            var body = await resp.Content.ReadAsStringAsync(ct);
            _log.LogWarning("Checkin failed: {status} {body}", resp.StatusCode, body);
            resp.EnsureSuccessStatusCode();
        }
        _log.LogInformation("Checkin accepted");
    }

    public class RegisterResult
    {
        [JsonPropertyName("agent_id")] public int AgentId { get; set; }
        [JsonPropertyName("agent_token")] public string AgentToken { get; set; } = "";
    }
}
