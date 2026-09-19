param([string]$RepositoryUrl = "https://github.com/yangaier0920-afk/mongo-consistency-project.git")
$ErrorActionPreference = 'Stop'
$packageRoot = Split-Path $PSScriptRoot -Parent
function Run-Git {
    param([string[]]$GitArgs)
    & git @GitArgs
    if ($LASTEXITCODE -ne 0) { throw "Git command failed: $($GitArgs -join ' ')" }
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'Install Git for Windows first.' }
if (-not $RepositoryUrl) { $RepositoryUrl = Read-Host 'GitHub repository HTTPS URL' }
if ($RepositoryUrl -notmatch '^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?/?$') {
    throw 'Use a GitHub HTTPS repository URL without credentials or query parameters.'
}
$manifest = Get-Content -LiteralPath (Join-Path $packageRoot 'RELEASE_SHA256.json') -Raw | ConvertFrom-Json
foreach ($entry in $manifest.PSObject.Properties) {
    $file = Join-Path $packageRoot $entry.Name
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Missing: $($entry.Name)" }
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { $actualHash = [BitConverter]::ToString($sha.ComputeHash([System.IO.File]::ReadAllBytes($file))).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
    if ($actualHash -ne $entry.Value) {
        throw "Changed file: $($entry.Name). Refresh the reviewed package before upload."
    }
}
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$clonePath = Join-Path ([System.IO.Path]::GetTempPath()) ('mongo-submission-' + [guid]::NewGuid().ToString('N'))
$branch = 'submission/final-' + $stamp
Write-Host "Cloning into $clonePath"
Run-Git -GitArgs @('clone','--',$RepositoryUrl,$clonePath)
Run-Git -GitArgs @('-C',$clonePath,'checkout','-b',$branch)
# Preserve superseded project materials in the new branch; main is untouched.
$oldPaths = @('experiments','experiments_v2','experiments-new','results','results-new','setup','report','report_assets','report_draft.md','report_draft_zh.md','report_outline_zh.md','report_outline_final_zh.md','README_legacy.md','FINAL_RESULTS.md','EXPERIMENT_PROTOCOL.md','Project Requirement.txt')
$archive = 'archive/github-before-' + $stamp
foreach ($old in $oldPaths) {
    $tracked = & git -C $clonePath ls-files -- $old
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect tracked files.' }
    if ($tracked) {
        $archiveDir = Join-Path $clonePath $archive
        New-Item -ItemType Directory -Path $archiveDir -Force | Out-Null
        Run-Git -GitArgs @('-C',$clonePath,'mv','--',$old,($archive + '/' + $old))
    }
}
$names = @($manifest.PSObject.Properties.Name) + @('RELEASE_SHA256.json')
foreach ($name in $names) {
    $destination = Join-Path $clonePath $name
    New-Item -ItemType Directory -Path (Split-Path $destination -Parent) -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $packageRoot $name) -Destination $destination -Force
}
# Only package paths and the explicitly archived paths are staged.
for ($offset = 0; $offset -lt $names.Count; $offset += 50) {
    $last = [Math]::Min($offset + 49, $names.Count - 1)
    $addArgs = @('-C',$clonePath,'add','--') + @($names[$offset..$last])
    Run-Git -GitArgs $addArgs
}
Run-Git -GitArgs @('-C',$clonePath,'diff','--cached','--stat')
Run-Git -GitArgs @('-C',$clonePath,'commit','-m','Organize final consistency experiments, evidence and report')
Run-Git -GitArgs @('-C',$clonePath,'push','-u','origin',$branch)
Write-Host "Uploaded branch: $branch"
Write-Host "Open the repository on GitHub, compare this branch and merge after review. main was not directly changed."
Write-Host "Local clone retained at: $clonePath"
