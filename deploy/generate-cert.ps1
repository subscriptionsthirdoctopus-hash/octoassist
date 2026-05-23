# generate-cert.ps1
# Generates a self-signed code-signing certificate for OctoAssist Asset Agent
# and exports both a .pfx (private — keep secret, used to sign the MSI) and a
# .cer (public — push to TEMA's domain Trusted Publishers store via GPO).
#
# Run this on a Windows host. Output goes to .\out\
#
# This is the no-paid-EV-cert workaround. It is NOT a substitute for a real
# DigiCert/Sectigo EV cert for public distribution, but it is functionally
# equivalent INSIDE the TEMA AD environment once the public cert is added to
# Trusted Publishers via GPO (see deploy/gpo-runbook.md).

$ErrorActionPreference = "Stop"

$Subject = "CN=Third Octopus, O=Third Octopus, C=IN"
$FriendlyName = "Third Octopus Code Signing"
$Years = 5
$OutDir = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "out"
$Pfx = Join-Path $OutDir "ThirdOctopus-CodeSigning.pfx"
$Cer = Join-Path $OutDir "ThirdOctopus-CodeSigning.cer"

New-Item -ItemType Directory -Force $OutDir | Out-Null

Write-Host "==> Generating self-signed code-signing certificate"
$cert = New-SelfSignedCertificate `
    -Subject $Subject `
    -FriendlyName $FriendlyName `
    -Type CodeSigningCert `
    -KeyAlgorithm RSA `
    -KeyLength 4096 `
    -HashAlgorithm SHA256 `
    -CertStoreLocation "Cert:\CurrentUser\My" `
    -NotAfter (Get-Date).AddYears($Years) `
    -KeyExportPolicy Exportable `
    -KeyUsage DigitalSignature

Write-Host "    Thumbprint: $($cert.Thumbprint)"
Write-Host "    Subject:    $($cert.Subject)"
Write-Host "    Valid till: $($cert.NotAfter)"

$PfxPassword = Read-Host -AsSecureString -Prompt "Set password for the PFX (do not lose this)"

Write-Host "==> Exporting PFX (private)  -> $Pfx"
Export-PfxCertificate -Cert "Cert:\CurrentUser\My\$($cert.Thumbprint)" `
    -FilePath $Pfx `
    -Password $PfxPassword | Out-Null

Write-Host "==> Exporting CER (public)   -> $Cer"
Export-Certificate -Cert "Cert:\CurrentUser\My\$($cert.Thumbprint)" `
    -FilePath $Cer | Out-Null

Write-Host ""
Write-Host "==============================================================="
Write-Host "  Done."
Write-Host ""
Write-Host "  PFX (private, used for signing MSI):"
Write-Host "    $Pfx"
Write-Host ""
Write-Host "  CER (public, deploy via GPO to Trusted Publishers):"
Write-Host "    $Cer"
Write-Host ""
Write-Host "  Hand the .CER to TEMA IT and follow the steps in"
Write-Host "  deploy/gpo-runbook.md."
Write-Host "==============================================================="
