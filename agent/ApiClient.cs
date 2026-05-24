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

    public async Task<List<PendingAction>> GetPendingActionsAsync(string agentToken, CancellationToken ct)
    {
        using var req = new HttpRequestMessage(HttpMethod.Get, "api/v1/agent/actions");
        req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", agentToken);

        using var resp = await _http.SendAsync(req, ct);
        if (!resp.IsSuccessStatusCode)
        {
            var body = await resp.Content.ReadAsStringAsync(ct);
            _log.LogWarning("Failed to get pending actions: {status} {body}", resp.StatusCode, body);
            return new List<PendingAction>();
        }
        var result = await resp.Content.ReadFromJsonAsync<List<PendingAction>>(JsonOpts, ct);
        return result ?? new List<PendingAction>();
    }

    public async Task StartActionAsync(string agentToken, int actionId, CancellationToken ct)
    {
        using var req = new HttpRequestMessage(HttpMethod.Post, $"api/v1/agent/actions/{actionId}/start");
        req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", agentToken);

        using var resp = await _http.SendAsync(req, ct);
        if (!resp.IsSuccessStatusCode)
        {
            var body = await resp.Content.ReadAsStringAsync(ct);
            _log.LogWarning("Failed to start action {id}: {status} {body}", actionId, resp.StatusCode, body);
        }
    }

    public async Task PostActionResultAsync(string agentToken, int actionId, ActionResult body, CancellationToken ct)
    {
        using var req = new HttpRequestMessage(HttpMethod.Post, $"api/v1/agent/actions/{actionId}/result");
        req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", agentToken);
        req.Content = JsonContent.Create(body, options: JsonOpts);

        using var resp = await _http.SendAsync(req, ct);
        if (!resp.IsSuccessStatusCode)
        {
            var resBody = await resp.Content.ReadAsStringAsync(ct);
            _log.LogWarning("Failed to post action {id} result: {status} {resBody}", actionId, resp.StatusCode, resBody);
            resp.EnsureSuccessStatusCode();
        }
    }

    public async Task<List<PendingDeployment>> GetPendingDeploymentsAsync(string agentToken, CancellationToken ct)
    {
        using var req = new HttpRequestMessage(HttpMethod.Get, "api/v1/agent/deployments");
        req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", agentToken);

        using var resp = await _http.SendAsync(req, ct);
        if (!resp.IsSuccessStatusCode)
        {
            var body = await resp.Content.ReadAsStringAsync(ct);
            _log.LogWarning("Failed to get pending deployments: {status} {body}", resp.StatusCode, body);
            return new List<PendingDeployment>();
        }
        var result = await resp.Content.ReadFromJsonAsync<List<PendingDeployment>>(JsonOpts, ct);
        return result ?? new List<PendingDeployment>();
    }

    public async Task StartDeploymentAsync(string agentToken, int targetId, CancellationToken ct)
    {
        using var req = new HttpRequestMessage(HttpMethod.Post, $"api/v1/agent/deployments/{targetId}/start");
        req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", agentToken);

        using var resp = await _http.SendAsync(req, ct);
        if (!resp.IsSuccessStatusCode)
        {
            var body = await resp.Content.ReadAsStringAsync(ct);
            _log.LogWarning("Failed to start deployment {id}: {status} {body}", targetId, resp.StatusCode, body);
        }
    }

    public async Task PostDeploymentAttemptAsync(string agentToken, int targetId, DeploymentAttempt body, CancellationToken ct)
    {
        using var req = new HttpRequestMessage(HttpMethod.Post, $"api/v1/agent/deployments/{targetId}/attempt");
        req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", agentToken);
        req.Content = JsonContent.Create(body, options: JsonOpts);

        using var resp = await _http.SendAsync(req, ct);
        if (!resp.IsSuccessStatusCode)
        {
            var resBody = await resp.Content.ReadAsStringAsync(ct);
            _log.LogWarning("Failed to post deployment attempt for target {id}: {status} {resBody}", targetId, resp.StatusCode, resBody);
            resp.EnsureSuccessStatusCode();
        }
    }

    public async Task FinishDeploymentAsync(string agentToken, int targetId, DeploymentFinish body, CancellationToken ct)
    {
        using var req = new HttpRequestMessage(HttpMethod.Post, $"api/v1/agent/deployments/{targetId}/finish");
        req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", agentToken);
        req.Content = JsonContent.Create(body, options: JsonOpts);

        using var resp = await _http.SendAsync(req, ct);
        if (!resp.IsSuccessStatusCode)
        {
            var resBody = await resp.Content.ReadAsStringAsync(ct);
            _log.LogWarning("Failed to finish deployment target {id}: {status} {resBody}", targetId, resp.StatusCode, resBody);
            resp.EnsureSuccessStatusCode();
        }
    }

    public class RegisterResult
    {
        [JsonPropertyName("agent_id")] public int AgentId { get; set; }
        [JsonPropertyName("agent_token")] public string AgentToken { get; set; } = "";
    }
}
