#!/usr/bin/env bash
#
# Despliegue de TikTok Live Recorder en DigitalOcean (Ubuntu 24.04)
# Uso:
#   cd tiktok
#   TIKTOK_USERNAME=usuario \
#   WEB_USERNAME=admin \
#   WEB_PASSWORD=clave_segura \
#   SECRET_KEY=clave_aleatoria_larga \
#   ./deploy.sh
#
set -euo pipefail

TIKTOK_USERNAME="${TIKTOK_USERNAME:?Falta TIKTOK_USERNAME}"
WEB_USERNAME="${WEB_USERNAME:?Falta WEB_USERNAME}"
WEB_PASSWORD="${WEB_PASSWORD:?Falta WEB_PASSWORD}"
SECRET_KEY="${SECRET_KEY:?Falta SECRET_KEY}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
POLL_INTERVAL="${POLL_INTERVAL:-60}"

cd "$(dirname "$0")"
echo "=== [1/5] Dependencias del sistema ==="
apt-get update -qq
apt-get install -y -qq curl ca-certificates >/dev/null

echo "=== [2/5] Swap de 2GB (droplet de 1GB) ==="
if [ ! -f /swapfile ]; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
    swapon /swapfile
    grep -q "^/swapfile" /etc/fstab || echo "/swapfile none swap sw 0 0" >> /etc/fstab
    echo "Swap de 2GB creado"
else
    echo "Swap ya existente"
fi

echo "=== [3/5] Instalando Docker ==="
if ! command -v docker >/dev/null 2>&1; then
    curl -fsSL https://get.docker.com | sh
else
    echo "Docker ya instalado"
fi

echo "=== [4/5] Creando .env ==="
cat > .env <<EOF
TIKTOK_USERNAME=$TIKTOK_USERNAME
POLL_INTERVAL=$POLL_INTERVAL
RETENTION_DAYS=$RETENTION_DAYS
FFMPEG_PATH=ffmpeg
PLAYWRIGHT_HEADLESS=true

WEB_USERNAME=$WEB_USERNAME
WEB_PASSWORD=$WEB_PASSWORD
SECRET_KEY=$SECRET_KEY
EOF

echo "=== [5/5] Construyendo y arrancando contenedores ==="
docker compose up -d --build

echo ""
echo "=== LISTO ==="
echo "El recorder monitorea 24/7 a @$TIKTOK_USERNAME"
echo "La web esta en el puerto 8080 (no expuesto al internet)."
echo "Para verla:  ssh -L 8080:localhost:8080 root@TU_IP  y abre http://localhost:8080"
echo "Logs:          docker compose logs -f recorder"
echo "Detener:       docker compose down"
