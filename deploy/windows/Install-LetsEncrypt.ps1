<#
    OctoAssist — replace the IIS certificate with a free, self-renewing
    Let's Encrypt certificate (via win-acme).

    Written for TEMA India's self-hosted server after the October 2026 eMudhra
    CA incident left their eMudhra wildcard certificate untrusted by Chrome,
    Edge, Safari and Firefox (NET::ERR_CERT_AUTHORITY_INVALID). Generic enough
    for any Windows install of OctoAssist behind IIS.

    What it does, in order:
      1. Finds the IIS site (default name "OctoAssist") and its webroot.
      2. Adds two rewrite rules ahead of the reverse proxy: serve
         /.well-known/acme-challenge/ from disk, and 301-redirect every other
         plain-HTTP request to HTTPS, so port 80 can never reach the app.
         Backs up web.config first.
      3. Downloads the latest win-acme release into C:\win-acme.
      4. Runs win-acme unattended: HTTP validation on port 80, certificate for
         the host name, installed into the site's HTTPS binding (replacing the
         current certificate), renewal scheduled task registered.
      5. Prints how to verify from outside.

    Requirements:
      - Run in an ELEVATED PowerShell on the server.
      - Inbound TCP/80 must reach this server from the internet during
        validation and at each renewal (Let's Encrypt connects to
        http://<host>/.well-known/acme-challenge/...). If the perimeter
        firewall only allows 443, ask the network team to allow 80 as well.
        With the redirect rule this script adds, port 80 serves only the
        validation path and otherwise redirects to HTTPS, exactly as the
        Third Octopus droplet and every public HTTPS site do.
      - DNS for the host name must point at this server.

        .\Install-LetsEncrypt.ps1 -Email it@temaindia.com
        .\Install-LetsEncrypt.ps1 -Email it@temaindia.com -HostName octoassist.temaindia.com -SiteName OctoAssist
        .\Install-LetsEncrypt.ps1 -Check          # report only, change nothing
#>
[CmdletBinding()]
param(
    [string]$HostName = 'octoassist.temaindia.com',
    [string]$SiteName = 'OctoAssist',
    [Parameter(Mandatory = $false)][string]$Email,
    [string]$WacsDir  = 'C:\win-acme',
    [switch]$Check
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Fail($msg) { Write-Host $msg -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------- 0. checks
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin -and -not $Check) { Fail 'Run this in an elevated (Administrator) PowerShell.' }
if (-not $Email -and -not $Check)   { Fail 'Pass -Email <address>. Let''s Encrypt sends expiry warnings there if renewal ever fails.' }

Import-Module WebAdministration -ErrorAction Stop

# ---------------------------------------------------------------- 1. site
$site = Get-Website -Name $SiteName -ErrorAction SilentlyContinue
if (-not $site) {
    Write-Host "No IIS site named '$SiteName'. Sites on this server:" -ForegroundColor Yellow
    Get-Website | Format-Table Name, ID, State, PhysicalPath -AutoSize
    Fail "Re-run with -SiteName <one of the above>."
}
$webroot = [Environment]::ExpandEnvironmentVariables($site.PhysicalPath)
$bindings = Get-WebBinding -Name $SiteName
Write-Host "site      : $SiteName (id $($site.Id), $($site.State))"
Write-Host "webroot   : $webroot"
Write-Host "bindings  : $(($bindings | ForEach-Object { $_.protocol + ' ' + $_.bindingInformation }) -join ', ')"

$httpsBinding = $bindings | Where-Object { $_.protocol -eq 'https' } | Select-Object -First 1
if ($httpsBinding -and $httpsBinding.certificateHash) {
    $cur = Get-ChildItem -Path Cert:\LocalMachine\My | Where-Object { $_.Thumbprint -eq $httpsBinding.certificateHash }
    if ($cur) { Write-Host "current   : $($cur.Subject) / issuer $($cur.Issuer) / expires $($cur.NotAfter.ToString('yyyy-MM-dd'))" }
}
if (-not ($bindings | Where-Object { $_.protocol -eq 'http' })) {
    Write-Host "WARNING: no http (port 80) binding on '$SiteName'. win-acme's HTTP validation needs one." -ForegroundColor Yellow
}

# ---------------------------------------------------------------- 2. web.config
$webConfig = Join-Path $webroot 'web.config'
if (-not (Test-Path -LiteralPath $webConfig)) { Fail "web.config not found at $webConfig" }
$xmlText = [IO.File]::ReadAllText($webConfig)
$hasAcme     = $xmlText -match 'name="ACME challenge passthrough"'
$hasRedirect = $xmlText -match 'name="Redirect HTTP to HTTPS"'
Write-Host "acme rule : $(if ($hasAcme) {'already present'} else {'missing - will be added'})"
Write-Host "redirect  : $(if ($hasRedirect) {'already present'} else {'missing - will be added'})"

if ($Check) { Write-Host ''; Write-Host 'Check only - nothing changed.' -ForegroundColor Green; exit 0 }

if (-not $hasAcme -or -not $hasRedirect) {
    $bk = "$webConfig.bak.$(Get-Date -Format yyyyMMdd-HHmmss)"
    Copy-Item -LiteralPath $webConfig -Destination $bk -Force
    Write-Host "backup    : $bk"

    # Both rules go ahead of the catch-all reverse-proxy rule. The ACME rule
    # must come first: "None" + stopProcessing leaves the challenge file to
    # IIS's static handler. The redirect then catches every other plain-HTTP
    # request. Each rule is added independently so a web.config patched by
    # an earlier version of this script (ACME rule only) gains the redirect.
    $acmeRule = @'
        <!-- OCTO-ACME: Let's Encrypt HTTP validation must be served from disk,
             not proxied to the app. Added by deploy/windows/Install-LetsEncrypt.ps1 -->
        <rule name="ACME challenge passthrough" stopProcessing="true">
          <match url="^\.well-known/acme-challenge/.*" />
          <action type="None" />
        </rule>

'@
    $redirectRule = @'
        <!-- OCTO-ACME: everything else that arrives on plain HTTP is sent to
             HTTPS before it can reach the app, so opening port 80 for
             validation never exposes a login page over HTTP. -->
        <rule name="Redirect HTTP to HTTPS" stopProcessing="true">
          <match url="(.*)" />
          <conditions>
            <add input="{HTTPS}" pattern="off" ignoreCase="true" />
          </conditions>
          <action type="Redirect" url="https://{HTTP_HOST}/{R:1}" redirectType="Permanent" />
        </rule>

'@
    if (-not $hasAcme) {
        $m = [regex]::Match($xmlText, '(?m)^[ \t]*<rule name="')
        if (-not $m.Success) { Fail 'Could not find the first <rule> in web.config - add the ACME rule by hand.' }
        $xmlText = $xmlText.Insert($m.Index, $acmeRule)
        Write-Host 'acme rule : added'
    }
    if (-not $hasRedirect) {
        # Directly after the ACME rule, which is now guaranteed to exist.
        $m = [regex]::Match($xmlText, '(?s)<rule name="ACME challenge passthrough".*?</rule>\r?\n')
        if (-not $m.Success) { Fail 'Could not find the ACME rule to insert the redirect after.' }
        $xmlText = $xmlText.Insert($m.Index + $m.Length, $redirectRule)
        Write-Host 'redirect  : added'
    }
    [IO.File]::WriteAllText($webConfig, $xmlText, (New-Object Text.UTF8Encoding($false)))
}

# win-acme writes its own web.config inside the challenge folder; make sure the
# folder exists and extensionless files are served.
$challengeDir = Join-Path $webroot '.well-known\acme-challenge'
New-Item -ItemType Directory -Path $challengeDir -Force | Out-Null

# ---------------------------------------------------------------- 3. win-acme
$wacs = Join-Path $WacsDir 'wacs.exe'
if (-not (Test-Path -LiteralPath $wacs)) {
    Write-Host 'win-acme  : downloading latest release from GitHub...'
    $rel   = Invoke-RestMethod -Uri 'https://api.github.com/repos/win-acme/win-acme/releases/latest' -Headers @{ 'User-Agent' = 'OctoAssist-LetsEncrypt' }
    $asset = $rel.assets | Where-Object { $_.name -like '*x64.pluggable.zip' } | Select-Object -First 1
    if (-not $asset) { Fail 'Could not find the x64 pluggable zip in the latest win-acme release. Download it from https://www.win-acme.com and extract to C:\win-acme, then re-run.' }
    $zip = Join-Path $env:TEMP $asset.name
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zip -UseBasicParsing
    New-Item -ItemType Directory -Path $WacsDir -Force | Out-Null
    Expand-Archive -Path $zip -DestinationPath $WacsDir -Force
    Remove-Item $zip -Force
    Write-Host "win-acme  : $($rel.tag_name) installed to $WacsDir"
} else {
    Write-Host "win-acme  : already at $wacs"
}

# ---------------------------------------------------------------- 4. issue + bind
Write-Host ''
Write-Host "Requesting a Let's Encrypt certificate for $HostName ..." -ForegroundColor Cyan
& $wacs `
    --source manual --host $HostName `
    --validation filesystem --webroot $webroot `
    --store certificatestore `
    --installation iis --installationsiteid $site.Id `
    --emailaddress $Email --accepttos `
    --verbose
$rc = $LASTEXITCODE
if ($rc -ne 0) {
    Write-Host ''
    Write-Host "win-acme exited with code $rc. Nothing was changed on the HTTPS binding." -ForegroundColor Red
    Write-Host "Most common cause: Let's Encrypt could not reach http://$HostName/.well-known/acme-challenge/ on port 80."
    Write-Host "Confirm from outside the network:  curl -sI http://$HostName/.well-known/acme-challenge/test"
    Write-Host '(a 404 from IIS is fine; a timeout means port 80 is blocked at the perimeter).'
    exit $rc
}

# ---------------------------------------------------------------- 5. verify
Write-Host ''
$httpsBinding = Get-WebBinding -Name $SiteName | Where-Object { $_.protocol -eq 'https' } | Select-Object -First 1
if ($httpsBinding -and $httpsBinding.certificateHash) {
    $new = Get-ChildItem -Path Cert:\LocalMachine\My | Where-Object { $_.Thumbprint -eq $httpsBinding.certificateHash }
    if ($new) { Write-Host "now bound : $($new.Subject) / issuer $($new.Issuer) / expires $($new.NotAfter.ToString('yyyy-MM-dd'))" -ForegroundColor Green }
}
Write-Host 'renewal   : win-acme registered a scheduled task (Task Scheduler > win-acme renew); it renews ~30 days before expiry.'
Write-Host ''
Write-Host 'Verify from a laptop outside the network:' -ForegroundColor Cyan
Write-Host "  openssl s_client -connect ${HostName}:443 -servername $HostName </dev/null 2>/dev/null | openssl x509 -noout -issuer -dates"
Write-Host '  (issuer should read Let''s Encrypt; then open the site in Chrome and Safari)'
