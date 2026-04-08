sudo apt update && \
sudo apt install -y labwc lightdm lightdm-gtk-greeter accountsservice && \
sudo systemctl set-default graphical.target && \
sudo cp /etc/lightdm/lightdm.conf /etc/lightdm/lightdm.conf.bak && \
sudo tee /etc/lightdm/lightdm.conf >/dev/null <<'EOF'
[LightDM]

[Seat:*]
greeter-session=lightdm-gtk-greeter
user-session=labwc
autologin-session=labwc
autologin-user=pi
autologin-user-timeout=0

[XDMCPServer]

[VNCServer]
EOF
mkdir -p /home/pi/.config/labwc && \
cat > /home/pi/.config/labwc/autostart <<'EOF'
#!/bin/sh
/usr/bin/chromium --ozone-platform=wayland --kiosk --app=http://localhost
EOF
chmod +x /home/pi/.config/labwc/autostart && \
sudo systemctl reset-failed lightdm && \
sudo reboot