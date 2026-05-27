#!/usr/bin/env bash
# Install passwordless sudo rules for webui service/network control.
set -euo pipefail

RUN_USER="${RUN_USER:-$USER}"
SUDOERS_FILE="/etc/sudoers.d/delatometry-webui"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with: sudo $0"
  exit 1
fi

SYSTEMCTL="$(command -v systemctl)"
if [ -z "$SYSTEMCTL" ]; then
  echo "ERROR: systemctl not found"
  exit 1
fi
echo "Using systemctl: $SYSTEMCTL"

# Also allow /bin/systemctl if it differs (sudo matches exact paths).
SYSTEMCTL_EXTRA=""
if [ "$SYSTEMCTL" != "/bin/systemctl" ] && [ -x /bin/systemctl ]; then
  SYSTEMCTL_EXTRA="/bin/systemctl"
fi

install -m 0440 /dev/stdin "$SUDOERS_FILE" <<EOF
# Delatometry webui — systemd + NetworkManager (user: ${RUN_USER})
${RUN_USER} ALL=(root) NOPASSWD: \\
  ${SYSTEMCTL} start delatometry-*, \\
  ${SYSTEMCTL} stop delatometry-*, \\
  ${SYSTEMCTL} restart delatometry-*, \\
  ${SYSTEMCTL} enable delatometry-*, \\
  ${SYSTEMCTL} disable delatometry-*, \\
  ${SYSTEMCTL} is-active delatometry-*, \\
  ${SYSTEMCTL} is-enabled delatometry-*, \\
  ${SYSTEMCTL} show delatometry-*, \\
  ${SYSTEMCTL} start mariadb.service, \\
  ${SYSTEMCTL} stop mariadb.service, \\
  ${SYSTEMCTL} restart mariadb.service, \\
  ${SYSTEMCTL} start mysql.service, \\
  ${SYSTEMCTL} stop mysql.service, \\
  ${SYSTEMCTL} restart mysql.service, \\
  ${SYSTEMCTL} start pigpiod.service, \\
  ${SYSTEMCTL} stop pigpiod.service, \\
  ${SYSTEMCTL} restart pigpiod.service, \\
  /usr/bin/tee /etc/default/delatometry, \\
  /usr/bin/tee /etc/delatometry/hotspot-dnsmasq.conf, \\
  /usr/bin/cat /etc/default/delatometry, \\
  /usr/bin/nmcli, \\
  ${SYSTEMCTL} enable delatometry-hotspot-dnsmasq.service, \\
  ${SYSTEMCTL} disable delatometry-hotspot-dnsmasq.service, \\
  ${SYSTEMCTL} start delatometry-hotspot-dnsmasq.service, \\
  ${SYSTEMCTL} stop delatometry-hotspot-dnsmasq.service, \\
  ${SYSTEMCTL} enable delatometry-vpn.service, \\
  ${SYSTEMCTL} disable delatometry-vpn.service, \\
  ${SYSTEMCTL} start delatometry-vpn.service, \\
  ${SYSTEMCTL} stop delatometry-vpn.service, \\
  /usr/bin/tee /etc/delatometry/vpn.json, \\
  /usr/bin/cp, \\
  /usr/bin/chmod 600 /etc/delatometry/openvpn/client.ovpn, \\
  /usr/bin/chmod 600 /etc/delatometry/openvpn/auth.txt, \\
  /usr/bin/chmod 644 /etc/delatometry/vpn.json, \\
  /usr/bin/mkdir -p /etc/delatometry/openvpn, \\
  /usr/bin/rm -f /etc/delatometry/openvpn/auth.txt, \\
  /usr/bin/openvpn, \\
  /usr/bin/pkill, \\
  /usr/bin/kill, \\
  /usr/bin/zerotier-cli, \\
  ${SYSTEMCTL} enable zerotier-one.service, \\
  ${SYSTEMCTL} start zerotier-one.service
EOF

if [ -n "$SYSTEMCTL_EXTRA" ]; then
  install -m 0440 /dev/stdin "${SUDOERS_FILE}.bin" <<EOF
${RUN_USER} ALL=(root) NOPASSWD: \\
  ${SYSTEMCTL_EXTRA} start delatometry-*, \\
  ${SYSTEMCTL_EXTRA} stop delatometry-*, \\
  ${SYSTEMCTL_EXTRA} restart delatometry-*, \\
  ${SYSTEMCTL_EXTRA} start mariadb.service, \\
  ${SYSTEMCTL_EXTRA} stop mariadb.service, \\
  ${SYSTEMCTL_EXTRA} restart mariadb.service, \\
  ${SYSTEMCTL_EXTRA} start pigpiod.service, \\
  ${SYSTEMCTL_EXTRA} stop pigpiod.service, \\
  ${SYSTEMCTL_EXTRA} restart pigpiod.service
EOF
  visudo -cf "${SUDOERS_FILE}.bin"
fi

visudo -cf "$SUDOERS_FILE"
echo "Installed $SUDOERS_FILE for user ${RUN_USER}"
