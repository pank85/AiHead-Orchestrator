# herdr-ask.ps1 — tab ❓/ℹ️ χωρίς LLM session (0 tokens).
# Δείχνει ένα .md, παίρνει την απάντηση του χρήστη και τη στέλνει ΑΥΤΟΥΣΙΑ στον master με `hc send`.
# Χρήση: pwsh -NoProfile -File herdr-ask.ps1 -File <path.md> -To <tN του master> -Tag <approval-<code>> [-Info] [-Answer <κείμενο, για test>]
param(
  [Parameter(Mandatory)][string]$File,
  [Parameter(Mandatory)][string]$To,
  [Parameter(Mandatory)][string]$Tag,
  [switch]$Info,        # ℹ️: σκέτο Enter = «οκ»
  [string]$Answer       # μόνο για test: παρακάμπτει την ερώτηση
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Get-Content -LiteralPath $File -Encoding UTF8 | Write-Host
Write-Host ''
if (-not $Answer) {
  $hint = if ($Info) { 'Enter = οκ, ή γράψε ερώτηση' } else { 'Η απάντησή σου (μία γραμμή)' }
  do {
    $Answer = Read-Host $hint
    if ($Info -and -not $Answer) { $Answer = 'οκ' }
  } while (-not $Answer)
}
# Μία γραμμή απάντησης· για πολύγραμμες απαντήσεις γράψε στον master απευθείας.
hc send $To "${Tag}: $Answer" | Out-Null
if ($LASTEXITCODE -ne 0) {
  Write-Host "ΑΠΟΤΥΧΙΑ αποστολής (hc exit $LASTEXITCODE). Γράψε την απάντηση στον master." -ForegroundColor Red
  Read-Host 'Enter για κλείσιμο' | Out-Null
  exit 1
}
Write-Host "στάλθηκε → $To" -ForegroundColor Green
