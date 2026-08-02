<#
.SYNOPSIS
Despliega TikTok Live Recorder en DigitalOcean desde Windows.

USO:
  1) Ejecuta SIN parametros: genera tu clave SSH e imprime la clave publica.
     Pega esa clave en DigitalOcean al crear el droplet.
  2) Crea el droplet (Ubuntu 24.04, plan Basic $6/mo).
  3) Ejecuta:  .\setup_digitalocean.ps1 -Ip TU_IP
     Sube el proyecto, instala Docker y deja el recorder + web corriendo.
#>
param(
    [string]$Ip = ""
)

$ErrorActionPreference = "Stop"

$proj = $PSScriptRoot
$keyDir = Join-Path $env:USERPROFILE ".ssh"
$keyPath = Join-Path $keyDir "id_ed25519"
$tmp = Join-Path $env:TEMP "tiktok_deploy"
$tarFile = Join-Path $tmp "tiktok.tar.gz"

if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] No encuentro ssh. Instala 'OpenSSH Client' en Configuracion > Apps > Caracteristicas opcionales." -ForegroundColor Red
    exit 1
}

# ================= MODO 1: generar clave y mostrar publica =================
if (-not $Ip) {
    if (-not (Test-Path $keyPath)) {
        New-Item -ItemType Directory -Force -Path $keyDir | Out-Null
        Write-Host "Generando clave SSH..." -ForegroundColor Yellow
        & ssh-keygen -t ed25519 -f $keyPath -N '""' -C "digitalocean" | Out-Null
    }
    Write-Host ""
    Write-Host "=== TU CLAVE PUBLICA (copia toda la linea) ===" -ForegroundColor Green
    Write-Host (Get-Content "$keyPath.pub")
    Write-Host ""
    Write-Host "Siguientes pasos:"
    Write-Host "  1. En DigitalOcean: Create > Droplets."
    Write-Host "  2. Imagen: Ubuntu 24.04 LTS x64 | Plan: Basic > Regular > \$6/mo."
    Write-Host "  3. Authentication > SSH Key: pega la clave de arriba."
    Write-Host "  4. Crea el droplet y copia su IP."
    Write-Host "  5. Ejecuta de nuevo:  .\setup_digitalocean.ps1 -Ip LA_IP"
    exit 0
}

# ================= MODO 2: subir y desplegar =================
Write-Host "=== [1/4] Empacando proyecto (sin grabaciones ni perfil de navegador) ===" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
if (Test-Path $tarFile) { Remove-Item $tarFile -Force }
Push-Location $proj
tar -czf $tarFile --exclude=grabaciones --exclude=browser_profile --exclude=logs --exclude=__pycache__ --exclude=mpv --exclude=.git --exclude=".env" . | Out-Null
Pop-Location
$sizeMb = [math]::Round((Get-Item $tarFile).Length / 1MB, 1)
Write-Host "Empaquetado: $sizeMb MB"

Write-Host "=== [2/4] Datos de configuracion ===" -ForegroundColor Cyan
$tiktokUser = Read-Host "Usuario de TikTok a monitorear"
$webUser = Read-Host "Usuario de la web (login)"
$webPass = Read-Host "Contrasena de la web (login)"
$secret = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 48 | ForEach-Object { [char]$_ })

function b64($s) { return [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($s)) }
$bUser = b64 $tiktokUser
$bWebUser = b64 $webUser
$bWebPass = b64 $webPass
$bSecret = b64 $secret

$remote = "root@$Ip"

Write-Host "=== [3/4] Subiendo proyecto a $remote ===" -ForegroundColor Cyan
scp -i $keyPath $tarFile "${remote}:/tmp/tiktok.tar.gz"
if ($LASTEXITCODE -ne 0) { Write-Host "[ERROR] Fallo la subida. Revisa la IP y que el droplet tenga tu clave SSH." -ForegroundColor Red; exit 1 }

Write-Host "=== [4/4] Instalando y arrancando (5-10 min la primera vez) ===" -ForegroundColor Cyan
$cmd = 'mkdir -p ~/tiktok && tar -xzf /tmp/tiktok.tar.gz -C ~/tiktok && cd ~/tiktok && chmod +x deploy.sh && export TIKTOK_USERNAME=$(echo ' + $bUser + ' | base64 -d) && export WEB_USERNAME=$(echo ' + $bWebUser + ' | base64 -d) && export WEB_PASSWORD=$(echo ' + $bWebPass + ' | base64 -d) && export SECRET_KEY=$(echo ' + $bSecret + ' | base64 -d) && bash deploy.sh'
ssh -i $keyPath $remote $cmd
if ($LASTEXITCODE -ne 0) { Write-Host "[ERROR] El despliegue fallo." -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "=== LISTO! ===" -ForegroundColor Green
Write-Host "Para ver tu web (privada, sin abrir puertos):"
Write-Host "  ssh -i $keyPath -L 8080:localhost:8080 $remote" -ForegroundColor Yellow
Write-Host "  y abre http://localhost:8080  (login con $webUser)"
Write-Host ""
Write-Host "Logs del recorder:  ssh -i $keyPath $remote 'docker compose -f /root/tiktok/docker-compose.yml logs -f recorder'"
