#!/usr/bin/env bash
# Make 192.168.1.200/24 alias + NAT for vSECC permanent.
# Run once with: sudo bash setup_network.sh
set -e

# ── 1. Secondary IP via NetworkManager ───────────────────────────────────────
echo "[1/3] Adding 192.168.1.200/24 to 'Wired connection 1'..."
nmcli connection modify "Wired connection 1" +ipv4.addresses "192.168.1.200/24"
nmcli connection up "Wired connection 1"
echo "      Done. IP will be re-applied on every reconnect/boot."

# ── 2. IP forwarding ─────────────────────────────────────────────────────────
echo "[2/3] Enabling net.ipv4.ip_forward permanently..."
sed -i 's/^#\s*net\.ipv4\.ip_forward=1/net.ipv4.ip_forward=1/' /etc/sysctl.conf
# If the line doesn't exist at all, append it
grep -q '^net\.ipv4\.ip_forward=1' /etc/sysctl.conf \
  || echo 'net.ipv4.ip_forward=1' >> /etc/sysctl.conf
sysctl -w net.ipv4.ip_forward=1
echo "      Done."

# ── 3. nftables NAT masquerade ───────────────────────────────────────────────
echo "[3/3] Writing nftables NAT rules to /etc/nftables.conf..."
cat > /etc/nftables.conf << 'EOF'
#!/usr/sbin/nft -f

flush ruleset

table inet filter {
    chain input {
        type filter hook input priority filter; policy accept;
    }
    chain forward {
        type filter hook forward priority filter; policy accept;
    }
    chain output {
        type filter hook output priority filter; policy accept;
    }
}

# NAT: masquerade 192.168.1.x (vSECC subnet) → internet via enp3s0
table ip nat {
    chain postrouting {
        type nat hook postrouting priority 100;
        ip saddr 192.168.1.0/24 oif enp3s0 masquerade;
    }
}
EOF

systemctl enable nftables
systemctl restart nftables
echo "      Done. nftables service enabled and started."

echo ""
echo "All done. Verify with:"
echo "  ip addr show enp3s0          # should show both .111.185 and .1.200"
echo "  sysctl net.ipv4.ip_forward   # should be 1"
echo "  nft list ruleset             # should show nat table"
