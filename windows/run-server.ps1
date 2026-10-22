<#
.SYNOPSIS
  Plano B: corre o servidor de Icarus nativamente no Windows (sem Docker/Wine),
  usando a MESMA pasta ..\data e o mesmo .env do projeto Docker.

.DESCRIPTION
  Instala o SteamCMD (se faltar), instala/atualiza o servidor, escreve as chaves
  geridas do ServerSettings.ini a partir do .env e arranca o servidor.
  Se o servidor for abaixo, volta a arrancar ao fim de 10 s. Ctrl+C para sair.

  Antes de usar: docker compose stop icarus   (nunca os dois ao mesmo tempo!)

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\windows\run-server.ps1
#>
param(
  [string]$InstallDir = "C:\IcarusServer",
  [switch]$SkipUpdate
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
$DataDir = Join-Path $ProjectDir "data"
$SteamCmdDir = Join-Path $InstallDir "steamcmd"
$GameDir = Join-Path $InstallDir "game"
$ServerExe = Join-Path $GameDir "Icarus\Binaries\Win64\IcarusServer-Win64-Shipping.exe"
$IniFile = Join-Path $DataDir "Saved\Config\WindowsServer\ServerSettings.ini"

function Log($msg) { Write-Host "[plano-b] $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg" }

# --- .env ---------------------------------------------------------------------
$envFile = Join-Path $ProjectDir ".env"
$cfg = @{}
if (Test-Path $envFile) {
  foreach ($line in Get-Content $envFile -Encoding UTF8) {
    if ($line -match '^\s*([A-Z0-9_]+)\s*=(.*)$') { $cfg[$Matches[1]] = $Matches[2].Trim() }
  }
}
function Get-Setting($key, $default) {
  if ($cfg.ContainsKey($key) -and $cfg[$key] -ne "") { return $cfg[$key] } else { return $default }
}

# --- SteamCMD + game ----------------------------------------------------------
if (-not (Test-Path (Join-Path $SteamCmdDir "steamcmd.exe"))) {
  Log "A instalar o SteamCMD em $SteamCmdDir"
  New-Item -ItemType Directory -Force -Path $SteamCmdDir | Out-Null
  $zip = Join-Path $env:TEMP "steamcmd.zip"
  Invoke-WebRequest "https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip" -OutFile $zip
  Expand-Archive $zip -DestinationPath $SteamCmdDir -Force
}

function Update-Game {
  $branch = Get-Setting "STEAM_BRANCH" ""
  $steamArgs = @("+force_install_dir", $GameDir, "+login", "anonymous", "+app_update", "2089300")
  if ($branch) { $steamArgs += @("-beta", $branch) }
  $steamArgs += @("validate", "+quit")
  Log "A instalar/atualizar o Icarus via SteamCMD"
  & (Join-Path $SteamCmdDir "steamcmd.exe") @steamArgs
}

# --- ServerSettings.ini (same managed keys as the Docker version) -------------
function Set-IniKey($key, $value) {
  $lines = [System.Collections.Generic.List[string]](Get-Content $IniFile -Encoding UTF8)
  $index = $lines.FindIndex({ param($l) $l.StartsWith("$key=") })
  if ($index -ge 0) { $lines[$index] = "$key=$value" } else { $lines.Add("$key=$value") }
  Set-Content -Path $IniFile -Value $lines -Encoding UTF8
}

function Write-Settings {
  New-Item -ItemType Directory -Force -Path (Split-Path $IniFile) | Out-Null
  if (-not (Test-Path $IniFile)) { Set-Content $IniFile "[/Script/Icarus.DedicatedServerSettings]" -Encoding UTF8 }
  Set-IniKey "SessionName" (Get-Setting "SERVER_NAME" "Icarus Caseiro")
  Set-IniKey "JoinPassword" (Get-Setting "JOIN_PASSWORD" "")
  Set-IniKey "AdminPassword" (Get-Setting "ADMIN_PASSWORD" "")
  Set-IniKey "MaxPlayers" (Get-Setting "MAX_PLAYERS" "4")
  Set-IniKey "ShutdownIfNotJoinedFor" (Get-Setting "SHUTDOWN_IF_NOT_JOINED_FOR" "-1")
  Set-IniKey "ShutdownIfEmptyFor" (Get-Setting "SHUTDOWN_IF_EMPTY_FOR" "-1")
  Set-IniKey "AllowNonAdminsToLaunchProspects" (Get-Setting "ALLOW_NON_ADMINS_LAUNCH" "True")
  Set-IniKey "AllowNonAdminsToDeleteProspects" (Get-Setting "ALLOW_NON_ADMINS_DELETE" "False")
  Set-IniKey "LoadProspect" (Get-Setting "LOAD_PROSPECT" "")
  Set-IniKey "ResumeProspect" (Get-Setting "RESUME_PROSPECT" "True")
}

# --- Run ----------------------------------------------------------------------
if (-not $SkipUpdate -or -not (Test-Path $ServerExe)) { Update-Game }
if (-not (Test-Path $ServerExe)) { throw "O servidor não ficou instalado em $GameDir" }

while ($true) {
  Write-Settings
  $name = Get-Setting "SERVER_NAME" "Icarus Caseiro"
  $port = Get-Setting "GAME_PORT" "17777"
  $queryPort = Get-Setting "QUERY_PORT" "27015"
  Log "A arrancar '$name' (UDP $port / $queryPort) com os dados em $DataDir"
  $proc = Start-Process -FilePath $ServerExe -PassThru -NoNewWindow -ArgumentList @(
    "-Log", "-UserDir=`"$DataDir`"", "-SteamServerName=`"$name`"", "-PORT=$port", "-QueryPort=$queryPort"
  )
  $proc.WaitForExit()
  Log "O servidor terminou (código $($proc.ExitCode)); a reiniciar em 10 s (Ctrl+C para sair)"
  Start-Sleep -Seconds 10
}
