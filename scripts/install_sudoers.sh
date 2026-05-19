#!/usr/bin/env bash
# Install passwordless sudo rules for webui service/network control.
set -euo pipefail

RUN_USER="${RUN_USER:-$USER}"
SUDOERS_FILE="/etc/sudoers.d/delatometry-webui"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with: sudo $0"
  exit 1
fi

install -m 0440 /dev/stdin "$SUDOERS_FILE" <<EOF
# Delatometry webui — systemd + NetworkManager (edit user if needed)
${RUN_USER} ALL=(root) NOPASSWD: /bin/systemctl start delatometry-*, \\
  /bin/systemctl stop delatometry-*, \\
  /bin/systemctl restart delatometry-*, \\
  /bin/systemctl enable delatometry-*, \\
  /bin/systemctl disable delatometry-*, \\
  /bin/systemctl is-active delatometry-*, \\
  /bin/systemctl is-enabled delatometry-*, \\
  /bin/systemctl show delatometry-*, \\
  /usr/bin/tee /etc/default/delatometry, \\
  /usr/bin/cat /etc/default/delatometry, \\
  /usr/bin/nmcli
EOF

visudo -cf "$SUDOERS_FILE"
echo "Installed $SUDOERS_FILE for user ${RUN_USER}"
