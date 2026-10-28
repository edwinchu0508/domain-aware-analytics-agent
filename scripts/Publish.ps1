# Publishes this fresh, sanitized repository; never force-pushes or overwrites.
param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Push-Location -LiteralPath $root
try {
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { throw 'GitHub CLI is required; install it and run gh auth login first.' }
    & gh auth status
    if ($LASTEXITCODE -ne 0) { throw 'Authenticate with gh auth login, then run this script again.' }
    $login = (& gh api user --jq '.login').Trim()
    $accountId = (& gh api user --jq '.id').Trim()
    if ($LASTEXITCODE -ne 0 -or $login -notmatch '^[A-Za-z0-9-]+$' -or $accountId -notmatch '^\d+$') { throw 'Cannot verify GitHub account identity.' }
    & $Python -I -B -c 'import sys; assert sys.version_info[:2] == (3, 11)'
    if ($LASTEXITCODE -ne 0) { throw 'Python unavailable or incompatible. Supply -Python with the full environment python.exe path.' }
    & $Python -I -B scripts/verify_release.py
    if ($LASTEXITCODE -ne 0) { throw 'Release hashes failed.' }
    if (-not (Test-Path -LiteralPath '.git')) { & git init -b main; if ($LASTEXITCODE -ne 0) { throw 'git init failed.' } }
    $remotes = @(& git remote)
    if ($remotes.Count -gt 0) { throw 'A remote already exists. Review it manually; this script does not overwrite repositories.' }
    # Windows PowerShell promotes native stderr to an exception under Stop.
    # Capture the expected HTTP 404; every other API failure still stops safely.
    $previousErrorAction = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $repoResponse = @(& gh api "repos/$login/domain-aware-analytics-agent" --include 2>&1)
        $repoExit = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previousErrorAction }
    if ($repoExit -eq 0) { throw 'The proposed repository already exists; choose/review a different name manually.' }
    if (($repoResponse -join "`n") -notmatch '(?m)^HTTP/\S+\s+404(?:\s|$)') { throw 'Cannot establish repository absence: GitHub API check failed. Publication stopped.' }
    $repoResponse = $null
    & git config --local user.name $login
    & git config --local user.email ("{0}+{1}{2}users.noreply.github.com" -f $accountId, $login, [char]64)
    & git add --all
    if ($LASTEXITCODE -ne 0) { throw 'git add failed.' }
    & $Python -I -B scripts/scan_publication.py --staged
    if ($LASTEXITCODE -ne 0) { throw 'Staged-file scan failed.' }
    & git diff --cached --stat
    & git commit -m 'Publish analytics agent and controlled evaluation findings'
    if ($LASTEXITCODE -ne 0) { throw 'Commit failed; do not publish.' }
    & gh repo create "$login/domain-aware-analytics-agent" --public --source . --remote origin --push
    if ($LASTEXITCODE -ne 0) { throw 'Repository creation/push failed; inspect state before retrying.' }
    & gh repo view --json url --jq '.url'
    & git ls-remote origin refs/heads/main
} finally { Pop-Location }
