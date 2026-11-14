#!/bin/bash
set -euo pipefail

DOMAIN="yamlfixit.stewillis.com"
CONF_SRC="/home/ste/yamlfixit/nginx-yamlfixit.stewillis.com.conf"
CONF_AVAIL="/etc/nginx/sites-available/yamlfixit.stewillis.com"
CONF_ENABLED="/etc/nginx/sites-enabled/yamlfixit.stewillis.com"

if [[ $EUID -ne 0 ]]; then
  echo "Please run with: sudo bash $0"
  exit 1
fi

echo "==> 1. Checking DNS resolution for ${DOMAIN}..."
RESOLVED=$(getent hosts "${DOMAIN}" | awk '{print $1}' | head -1 || true)
SERVER_IP=$(hostname -I | awk '{print $1}')

if [[ -z "${RESOLVED}" ]]; then
  echo "[!] DNS for ${DOMAIN} is not active yet."
  echo "    Make sure you added an A record for stewillis.com:"
  echo "    Host / Subdomain: yamlfixit"
  echo "    Points to: ${SERVER_IP}"
  echo "    DNS propagation usually takes 5-15 minutes. Try again shortly."
  exit 1
fi
echo "[OK] DNS resolves ${DOMAIN} -> ${RESOLVED}"

echo "==> 2. Setting up Nginx virtual host for ${DOMAIN}..."
if [[ ! -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]]; then
  cat > "${CONF_AVAIL}" <<TMP_EOF
server {
    listen 80;
    server_name ${DOMAIN};

    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }

    location / {
        proxy_pass http://127.0.0.1:8076;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
TMP_EOF
  ln -sfn "${CONF_AVAIL}" "${CONF_ENABLED}"
  nginx -t
  systemctl reload nginx

  echo "==> 3. Requesting Let's Encrypt SSL certificate for ${DOMAIN}..."
  certbot --nginx -d "${DOMAIN}" --non-interactive --agree-tos -m stewillis9262@gmail.com --redirect
fi

echo "==> 4. Applying full HTTPS Nginx config..."
cp "${CONF_SRC}" "${CONF_AVAIL}"
ln -sfn "${CONF_AVAIL}" "${CONF_ENABLED}"

nginx -t
systemctl reload nginx

echo
echo "============================================================"
echo "[SUCCESS] YAML FixIt Studio is live with SSL at:"
echo "          https://${DOMAIN}"
echo "============================================================"
