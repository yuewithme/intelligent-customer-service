param(
    [string]$RepositoryPath = (Split-Path -Parent $PSScriptRoot),
    [switch]$ForceDeploy
)

$ErrorActionPreference = "Stop"
$repository = (Resolve-Path -LiteralPath $RepositoryPath).Path
$runtimeDirectory = Join-Path $repository "runtime-data"
$logPath = Join-Path $runtimeDirectory "auto-deploy.log"
$lockPath = Join-Path $runtimeDirectory "auto-deploy.lock"
New-Item -ItemType Directory -Force -Path $runtimeDirectory | Out-Null

function Write-DeployLog {
    param([string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -LiteralPath $logPath -Value "[$timestamp] $Message" -Encoding utf8
}

$lockStream = $null
try {
    try {
        $lockStream = [System.IO.File]::Open(
            $lockPath,
            [System.IO.FileMode]::OpenOrCreate,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None
        )
    }
    catch {
        exit 0
    }

    Push-Location $repository
    try {
        $trackedChanges = git status --porcelain --untracked-files=no
        if ($LASTEXITCODE -ne 0) { throw "Unable to inspect deployment checkout" }
        if ($trackedChanges) { throw "Deployment checkout contains tracked changes" }

        git fetch origin main --quiet
        if ($LASTEXITCODE -ne 0) { throw "Unable to fetch origin/main" }
        $currentRevision = (git rev-parse HEAD).Trim()
        $remoteRevision = (git rev-parse origin/main).Trim()
        if ($LASTEXITCODE -ne 0) { throw "Unable to resolve Git revisions" }

        if (-not $ForceDeploy -and $currentRevision -eq $remoteRevision) {
            exit 0
        }

        git merge --ff-only origin/main --quiet
        if ($LASTEXITCODE -ne 0) { throw "Unable to fast-forward deployment checkout" }

        Write-DeployLog "Deploying revision $remoteRevision"
        docker compose -p intelligent-customer-service -f docker-compose.prod.yml up -d --build
        if ($LASTEXITCODE -ne 0) { throw "Docker Compose deployment failed" }

        $healthy = $false
        for ($attempt = 1; $attempt -le 12; $attempt++) {
            try {
                $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:21873/health" -TimeoutSec 5
                if ($response.StatusCode -eq 200) {
                    $healthy = $true
                    break
                }
            }
            catch {
                Start-Sleep -Seconds 5
            }
        }
        if (-not $healthy) { throw "Deployment health check failed" }
        Write-DeployLog "Deployment healthy at revision $remoteRevision"
    }
    finally {
        Pop-Location
    }
}
catch {
    Write-DeployLog "ERROR: $($_.Exception.Message)"
    exit 1
}
finally {
    if ($null -ne $lockStream) { $lockStream.Dispose() }
}
