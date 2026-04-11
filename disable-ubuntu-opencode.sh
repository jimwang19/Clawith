#!/bin/bash
# 禁用 Ubuntu 上的 opencode serve 自动启动

echo "=== Step 1: Stop running opencode serve ==="
wsl -d Ubuntu -- bash -c "
  if pgrep -f 'opencode serve' > /dev/null; then
    echo 'Found running opencode serve, stopping...'
    pkill -f 'opencode serve'
    sleep 2
    if pgrep -f 'opencode serve' > /dev/null; then
      echo 'Force killing...'
      pkill -9 -f 'opencode serve'
    fi
    echo 'Stopped'
  else
    echo 'No running opencode serve found'
  fi
"

echo ""
echo "=== Step 2: Find and remove auto-start configurations ==="
wsl -d Ubuntu -- bash -c "
  # Check systemd
  if systemctl list-unit-files 2>/dev/null | grep -q opencode; then
    echo 'Found systemd service entries'
    systemctl list-unit-files | grep opencode
    systemctl disable opencode.service 2>/dev/null || true
    systemctl disable opencode-serve.service 2>/dev/null || true
  fi
  
  # Check if service files exist
  if [ -f /etc/systemd/system/opencode.service ]; then
    echo 'Removing /etc/systemd/system/opencode.service'
    sudo rm /etc/systemd/system/opencode.service
  fi
  if [ -f /etc/systemd/system/opencode-serve.service ]; then
    echo 'Removing /etc/systemd/system/opencode-serve.service'
    sudo rm /etc/systemd/system/opencode-serve.service
  fi
  
  # Reload systemd
  systemctl daemon-reload 2>/dev/null || true
"

echo ""
echo "=== Step 3: Check ~/.bashrc for opencode serve ==="
wsl -d Ubuntu -- bash -c "
  if grep -n 'opencode serve' ~/.bashrc 2>/dev/null; then
    echo 'Found in ~/.bashrc, removing...'
    sed -i '/opencode serve/d' ~/.bashrc
  else
    echo 'Not found in ~/.bashrc'
  fi
  
  if grep -n 'opencode serve' ~/.bash_profile 2>/dev/null; then
    echo 'Found in ~/.bash_profile, removing...'
    sed -i '/opencode serve/d' ~/.bash_profile
  fi
"

echo ""
echo "=== Step 4: Check cron jobs ==="
wsl -d Ubuntu -- bash -c "
  crontab -l 2>/dev/null | grep -i opencode && echo 'Found in crontab' || echo 'Not in user crontab'
  ls /etc/cron.d/*opencode* 2>/dev/null && echo 'Found cron.d entries' || echo 'No cron.d entries'
"

echo ""
echo "=== Step 5: Verify stopped ==="
wsl -d Ubuntu -- bash -c "
  if pgrep -af 'opencode serve'; then
    echo 'ERROR: opencode serve is still running'
    exit 1
  else
    echo 'SUCCESS: opencode serve is stopped'
  fi
"

echo ""
echo "Done!"
