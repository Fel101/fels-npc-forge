<#
Setup.exe over the release before it is an update (installer\FelsNPCForge.iss). This installs the newest release older than
this version into a folder of its own, rolls a character into its library, runs the given Setup.exe over it, and checks the
version, the files and folders this version no longer has, the update record, .venv's packages against requirements.txt,
and the character.

  .\scripts\check_setup_update.ps1 -Setup <the new Setup.exe>
  .\scripts\check_setup_update.ps1 -Setup <the new Setup.exe> -Previous <the older release's Setup.exe> -Work <folder> -Uninstall

Run it from the repository's root, in a checkout of the commit the new Setup.exe was built from, with every tag: the files
this version no longer has are git's difference between the older release's tag and HEAD. Without -Previous, the older
release's Setup.exe is downloaded from GitHub with gh; a release without one (1.0.0 had no files) is installed as its
source ZIP was, from its tag, with its own setup.bat and this computer's Python. The servers it starts use a port, a
ComfyUI address and a suite folder of their own, so nothing else running here is touched. -Uninstall removes the updated
copy at the end, keeping its library, as the uninstaller does for anyone.
#>
param(
  [Parameter(Mandatory = $true)] [string] $Setup,
  [string] $Previous = '',
  [string] $Work = '',
  [int] $Port = 8791,
  [switch] $Uninstall
)
$ErrorActionPreference = 'Stop'
if (-not $Work) { $Work = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [System.IO.Path]::GetTempPath() } }
New-Item -ItemType Directory -Force -Path $Work | Out-Null
$Work = (Resolve-Path $Work).Path
$Setup = (Resolve-Path $Setup).Path
$version = (Select-String -Path npcgen\__init__.py -Pattern '^__version__ = "(.+)"$').Matches[0].Groups[1].Value
$repository = (Select-String -Path npcgen\updates.py -Pattern '^REPOSITORY = "(.+)"$').Matches[0].Groups[1].Value
$previousTag = git tag --list 'v*' | Where-Object { $_ -match '^v\d+\.\d+\.\d+$' -and [version]$_.Substring(1) -lt [version]$version } |
  Sort-Object { [version]$_.Substring(1) } | Select-Object -Last 1
if (-not $previousTag) { "There is no release before $version to update from."; exit 0 }
$forge = Join-Path $Work 'UpdatedForge'
if (Test-Path $forge) { throw "$forge is already there; remove it, or give another folder with -Work" }
$silent = '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/DIR=`"$forge`""
$url = "http://127.0.0.1:$Port"
# Its own port and suite folder, and a ComfyUI address where nothing answers.
$env:NPCGEN_PORT = "$Port"
$env:NPCGEN_COMFY_URL = 'http://127.0.0.1:9'
$env:FELS_SUITE_DIR = Join-Path $Work 'suite'

if (-not $Previous) {
  $download = Join-Path $Work 'previous'
  gh release download $previousTag --repo $repository --pattern 'FelsNPCForge-Setup-*.exe' --dir $download 2>$null
  if (-not $LASTEXITCODE) { $Previous = (Get-Item "$download\FelsNPCForge-Setup-*.exe").FullName }
}
if ($Previous) {
  $old = Start-Process (Resolve-Path $Previous).Path -ArgumentList ($silent + "/LOG=`"$Work\previous-setup.log`"") -Wait -PassThru
  if ($old.ExitCode) { throw "The Setup.exe of $previousTag ended with exit code $($old.ExitCode)" }
} else {
  "$previousTag has no Setup.exe: installing it as its source ZIP was, with its own setup.bat."
  $zip = Join-Path $Work "$previousTag.zip"
  git archive --format=zip --output $zip $previousTag
  if ($LASTEXITCODE) { throw "git archive could not pack $previousTag" }
  Expand-Archive -Path $zip -DestinationPath $forge
  Push-Location $forge
  try { cmd /c "setup.bat < nul"; if ($LASTEXITCODE) { throw "$previousTag's setup.bat ended with exit code $LASTEXITCODE" } } finally { Pop-Location }
}

function Start-Forge {
  $server = Start-Process "$forge\.venv\Scripts\python.exe" -ArgumentList 'server.py' -WorkingDirectory $forge -PassThru -NoNewWindow
  foreach ($try in 1..60) {
    try { $null = Invoke-RestMethod "$url/api/npcs" -TimeoutSec 2; return $server } catch { Start-Sleep 1 }
  }
  Stop-Process -Id $server.Id -Force
  throw "NPC Forge in $forge did not answer on $url"
}

function Stop-Forge($server) {
  # The venv's python.exe starts the base Python as its child; stop both, then let local\running.json's process go.
  Get-CimInstance Win32_Process -Filter "ParentProcessId = $($server.Id)" | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
  Stop-Process -Id $server.Id -Force
  Start-Sleep 2
}

$server = Start-Forge
$draft = Invoke-RestMethod -Method Post "$url/api/roll" -ContentType 'application/json' -Body '{"setting": "fantasy"}'
$draft.profile.first_name = 'Kept'
$draft.profile.last_name = 'Through-the-update'
$kept = Invoke-RestMethod -Method Post "$url/api/npcs" -ContentType 'application/json' -Body ($draft | ConvertTo-Json -Depth 30)
Stop-Forge $server
$new = Start-Process $Setup -ArgumentList ($silent + "/LOG=`"$Work\update-setup.log`"") -Wait -PassThru
if ($new.ExitCode) {
  Get-Content "$forge\local\install\setup-exe.log" -Tail 80 -ErrorAction SilentlyContinue
  throw "This Setup.exe over $previousTag ended with exit code $($new.ExitCode)"
}
$server = Start-Forge
$health = Invoke-RestMethod "$url/api/health?identity=1"
$found = Invoke-RestMethod "$url/api/npcs/$($kept.id)"
Stop-Forge $server
if ($health.fels.version -ne $version) { throw "NPC Forge says version $($health.fels.version) after the update, not $version" }
if ($found.profile.first_name -ne 'Kept') { throw "The update lost the library's character" }
$dropped = @(git diff --no-renames --diff-filter=D --name-only $previousTag HEAD)
foreach ($file in $dropped) { if (Test-Path (Join-Path $forge $file)) { throw "Setup left $file, which $version no longer has" } }
# A folder whose files all went (npcgen\voice after 1.0's voice code) must not stay behind with Python's caches alone.
$folders = @(git ls-tree -r -d --name-only HEAD)
foreach ($folder in ($dropped | ForEach-Object { Split-Path $_ -Parent } | Where-Object { $_ } | Sort-Object -Unique)) {
  $folder = $folder -replace '\\', '/'
  if ($folders -notcontains $folder -and (Test-Path (Join-Path $forge $folder))) { throw "Setup left the folder $folder, which $version no longer has" }
}
$record = Get-Content "$forge\local\update\last.json" -Raw | ConvertFrom-Json
if ($record.method -ne 'setup' -or $record.from -ne $previousTag.Substring(1) -or $record.to -ne $version) {
  throw "The update was not recorded as Setup's update from $previousTag to $version"
}
& "$forge\.venv\Scripts\python.exe" -m pip check
if ($LASTEXITCODE) { throw "pip check found a problem in the updated .venv" }
$pins = @'
import importlib.metadata, pathlib, sys
wrong = []
for line in pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    pin = line.split("#", 1)[0].strip()
    if "==" in pin:
        name, version = (part.strip() for part in pin.split("=="))
        if importlib.metadata.version(name) != version:
            wrong.append(f"{name} {importlib.metadata.version(name)}, pinned {version}")
print("Not as requirements.txt pins them: " + "; ".join(wrong) if wrong else "Every package is as requirements.txt pins it.")
sys.exit(1 if wrong else 0)
'@
$pins | & "$forge\.venv\Scripts\python.exe" - "$forge\requirements.txt"
if ($LASTEXITCODE) { throw "The update left .venv's packages unlike requirements.txt" }
"Setup.exe updated $previousTag to $version`: the character kept, $($dropped.Count) dropped file(s) gone, the packages as pinned."

if ($Uninstall) {
  # The uninstaller runs from a copy of itself, so wait for it to remove its own files. The library stays, as it does for
  # anyone who keeps it; remove the folder afterwards.
  Start-Process "$forge\unins000.exe" -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -Wait
  foreach ($try in 1..120) { if (-not (Test-Path "$forge\unins000.exe")) { break }; Start-Sleep 1 }
  if (Test-Path "$forge\unins000.exe") { throw "The uninstaller did not finish" }
  if (-not (Test-Path "$forge\library")) { throw "Uninstalling removed the library it should keep" }
  "Uninstalled the updated NPC Forge; its library is still in $forge."
}
