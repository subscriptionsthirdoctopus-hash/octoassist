<#
    OctoAssist - SAM product pages 404 when the product name contains "+".

    SYMPTOM. Software (SAM) -> click "Microsoft Visual C++ 2015-2022
    Redistributable" -> IIS's own error page: "404 - File or directory not
    found." 125 of TEMA's product names contain a "+" and every one of them
    fails the same way.

    CAUSE. The application is fine - requested directly on 127.0.0.1:8091 that
    exact URL returns 200. IIS never forwards it. The link is correctly encoded
    by the template's |urlencode filter, so "+" arrives as "%2B", and IIS
    Request Filtering rejects "%2B" in a path as double escaping (404.11)
    because its second unescape pass reads a literal "+" as a space. The check
    runs BEFORE the rewrite rule, which is why the app never sees the request.

    FIX. Set allowDoubleEscaping="true" on THIS SITE ONLY.

    Why that is safe here, and not a general recommendation: this site is a
    pure reverse proxy. Its web.config rewrites <match url="(.*)" /> to
    http://127.0.0.1:8091, and its physical folder contains nothing but that
    web.config. IIS never resolves a request path to a file, so the traversal
    attack the filter exists to stop has nothing to reach. The setting is
    written into the site's own web.config, so it cannot leak to the other
    seven sites on this server.

    The durable fix belongs in the product: arbitrary text like a product name
    should travel in a query string, not a path segment. This unblocks the
    125 pages now, without touching application code.

        .\Fix-PlusUrl404.ps1 -Check      # report only, change nothing
        .\Fix-PlusUrl404.ps1             # apply
        .\Fix-PlusUrl404.ps1 -Verify     # probe the URLs through IIS
        .\Fix-PlusUrl404.ps1 -Rollback   # restore the newest backup

    Editing web.config recycles the IIS application pool by itself. The
    OctoAssist service is NOT restarted and does not need to be.
#>
[CmdletBinding()]
param(
    [string]$ConfigPath = 'C:\Program Files\Third Octopus\OctoAssist Server\iis-site\web.config',
    [string]$SiteHost   = 'octoassist.temaindia.com',
    [switch]$Check,
    [switch]$Verify,
    [switch]$Rollback
)

$ErrorActionPreference = 'Stop'
$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
function Say ([string]$m, [string]$c = 'Gray') { Write-Host $m -ForegroundColor $c }

function Probe ([string]$path) {
    $req = [System.Net.HttpWebRequest]::Create('http://127.0.0.1' + $path)
    $req.Host = $SiteHost
    $req.AllowAutoRedirect = $false
    $req.Timeout = 20000
    try { $resp = $req.GetResponse(); $code = [int]$resp.StatusCode; $resp.Close() }
    catch [System.Net.WebException] {
        if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
        else { $code = $_.Exception.Status }
    }
    return $code
}

# A "+" URL and a plain one. 404 on the first with 303 on the second is the
# signature of this fault; 303 on both means it is fixed (303 = the app's
# redirect to the login page, i.e. the request reached the app).
$PlusUrl  = '/software/product/Microsoft/Microsoft%20Visual%20C%2B%2B%202015-2022%20Redistributable'
$PlainUrl = '/software/product/Microsoft/Microsoft%20Edge'

Say ""
Say "OctoAssist - SAM '+' URL 404 fix" Cyan
Say "config : $ConfigPath"
Say ""

if (-not (Test-Path -LiteralPath $ConfigPath)) {
    Say "NOT FOUND: $ConfigPath" Red
    exit 1
}

if ($Verify) {
    Say ("plus-name URL  : {0}" -f (Probe $PlusUrl))
    Say ("plain-name URL : {0}" -f (Probe $PlainUrl))
    Say ""
    Say "303 on both = fixed. 404 then 303 = still blocked by request filtering." Gray
    exit 0
}

if ($Rollback) {
    $bak = Get-ChildItem -Path (Split-Path $ConfigPath) -Filter 'web.config.plusfix-*.bak' -ErrorAction SilentlyContinue |
           Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($bak) {
        Copy-Item -LiteralPath $bak.FullName -Destination $ConfigPath -Force
        Say "restored web.config from $($bak.Name)" Green
    } else {
        Say "no backup found" Yellow
    }
    exit 0
}

# XML, not string surgery: web.config is the file that decides whether this
# site serves at all, and a malformed one takes the site down rather than
# failing loudly.
$xml = New-Object System.Xml.XmlDocument
$xml.PreserveWhitespace = $true
$xml.Load($ConfigPath)

$sws = $xml.SelectSingleNode('/configuration/system.webServer')
if (-not $sws) { Say "no <system.webServer> in web.config - not the expected file" Red; exit 1 }

$rf = $xml.SelectSingleNode('/configuration/system.webServer/security/requestFiltering')
if ($rf -and $rf.GetAttribute('allowDoubleEscaping') -eq 'true') {
    Say "Already applied - allowDoubleEscaping is already true. Nothing to do." Green
    Say ""
    Say ("plus-name URL  : {0}" -f (Probe $PlusUrl))
    exit 0
}

Say "before:"
Say ("  plus-name URL  : {0}" -f (Probe $PlusUrl))
Say ("  plain-name URL : {0}" -f (Probe $PlainUrl))

if ($Check) {
    Say ""
    Say "would set: /configuration/system.webServer/security/requestFiltering/@allowDoubleEscaping = true" Cyan
    Say "-Check: nothing was written." Cyan
    exit 0
}

Copy-Item -LiteralPath $ConfigPath -Destination "$ConfigPath.plusfix-$Stamp.bak" -Force
Say ""
Say "backed up : web.config.plusfix-$Stamp.bak" Gray

# Whitespace nodes so the file a human opens next year is still readable.
$nl = $xml.CreateWhitespace("`r`n    ")
$nl2 = $xml.CreateWhitespace("`r`n  ")

$sec = $sws.SelectSingleNode('security')
if (-not $sec) {
    $sec = $xml.CreateElement('security')
    $note = $xml.CreateComment(
        ' Product names in the SAM catalogue contain "+" (Microsoft Visual C++ ...). ' +
        'The template encodes it correctly as %2B, but IIS Request Filtering rejects ' +
        '%2B in a path as double escaping, before the rewrite rule below ever runs. ' +
        'Safe to allow here: this site serves no files - every request is rewritten ' +
        'to 127.0.0.1:8091 and the folder holds only this web.config. ')
    [void]$sws.AppendChild($nl.CloneNode($true))
    [void]$sws.AppendChild($note)
    [void]$sws.AppendChild($nl.CloneNode($true))
    [void]$sws.AppendChild($sec)
    [void]$sws.AppendChild($nl2.CloneNode($true))
}
$rf = $sec.SelectSingleNode('requestFiltering')
if (-not $rf) {
    $rf = $xml.CreateElement('requestFiltering')
    [void]$sec.AppendChild($rf)
}
$rf.SetAttribute('allowDoubleEscaping', 'true')
$xml.Save($ConfigPath)
Say "written   : allowDoubleEscaping=true" Green

# The app pool recycles on a web.config write; give it a moment to pick up.
Start-Sleep -Seconds 4

Say ""
Say "after:"
$after = Probe $PlusUrl
Say ("  plus-name URL  : {0}" -f $after)
Say ("  plain-name URL : {0}" -f (Probe $PlainUrl))
Say ""
if ($after -eq 404) {
    Say "STILL 404 - the change did not take. Roll back and investigate:" Red
    Say "  .\Fix-PlusUrl404.ps1 -Rollback" Yellow
    exit 1
}
if ($after -notin @(200, 301, 302, 303, 307, 401, 403)) {
    # Anything else means the probe never got an HTTP answer, so it proves
    # nothing either way. Say so rather than reporting a fix that was not seen.
    Say "Could not verify from this host (probe returned '$after')." Yellow
    Say "The config was written; check the page in a browser." Yellow
    exit 0
}
Say "Fixed - the '+' product pages now reach the application." Green
Say "Undo at any time:  .\Fix-PlusUrl404.ps1 -Rollback" Gray
