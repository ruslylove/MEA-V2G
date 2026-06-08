#!/usr/bin/env python3
"""
Reconfigure vSECC to direct MEA CSMS connection on new subnet.

Requires: vSECC reachable at 192.168.1.166 (add alias first):
  sudo ip addr add 192.168.1.200/24 dev enp3s0

What it does:
  1. Authenticates with vSECC REST API
  2. Lists all variables (prints network + OCPP backend ones)
  3. Sets CSMS URL → wss://ocpp.measandbox.com:2930/EV/Srv/JSON/1.6/rddQC4000001
  4. Sets security profile → 0 (TLS without client cert)
  5. Sets static IP → 192.168.111.166/24 (or DHCP if preferred)
  6. Restarts vSECC

Usage:
  python3 tests/system/reconfigure_vsecc.py [--dhcp] [--ip 192.168.111.166]
"""

import sys
import json
import time
import argparse
import requests

VSECC_OLD = "http://192.168.1.166/api"
VSECC_NEW = "http://192.168.111.166/api"
CP_ID     = "rddQC4000001"
CSMS_URL  = f"wss://ocpp.measandbox.com:2930/EV/Srv/JSON/1.6/{CP_ID}"

KNOWN_VARIDS = {
    "csms_url":         "28f51fe0",
    "identity":         "c9d0e1f2",
    "url_identity":     "7e8f9a0b",
    "security_profile": "e5f6a7b8",
    "backend_on":       "cb9c8312",
}


def login(base):
    r = requests.post(f"{base}/login",
                      json={"name": "admin", "password": "admin"}, timeout=10)
    r.raise_for_status()
    token = r.text.strip().strip('"')
    return token


def headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def get_var(base, token, var_id):
    r = requests.get(f"{base}/variables/{var_id}", headers=headers(token), timeout=10)
    return r.json() if r.ok else None


def set_var(base, token, var_id, current_obj, value):
    body = dict(current_obj)
    body["value"] = value
    r = requests.put(f"{base}/variables/{var_id}",
                     headers=headers(token),
                     data=json.dumps(body), timeout=10)
    ok = r.ok
    print(f"  SET {var_id} = {repr(value)}  →  HTTP {r.status_code}")
    return ok


def list_all_vars(base, token):
    r = requests.get(f"{base}/variables", headers=headers(token), timeout=10)
    if not r.ok:
        return []
    return r.json() if isinstance(r.json(), list) else r.json().get("variables", [])


def restart(base, token):
    r = requests.post(f"{base}/system/restart",
                      headers=headers(token), data='"vsecc"', timeout=10)
    print(f"  Restart →  HTTP {r.status_code}")
    return r.ok


def find_network_vars(all_vars):
    """Filter variables related to network and OCPP backend."""
    keywords = ["ip", "network", "static", "dhcp", "gateway", "netmask", "subnet",
                "csms", "url", "backend", "ocpp", "security", "identity", "profile"]
    found = []
    for v in all_vars:
        name = (v.get("name") or v.get("varName") or v.get("varId") or "").lower()
        desc = (v.get("description") or "").lower()
        if any(kw in name or kw in desc for kw in keywords):
            found.append(v)
    return found


def configure_csms(base, token):
    """Set CSMS URL, identity, and security profile."""
    print("\n── OCPP Backend configuration ──────────────────────")

    # CSMS URL
    v = get_var(base, token, KNOWN_VARIDS["csms_url"])
    if v:
        print(f"  Current URL: {v.get('value')}")
        set_var(base, token, KNOWN_VARIDS["csms_url"], v, CSMS_URL)
    else:
        print(f"  WARNING: csms_url var ({KNOWN_VARIDS['csms_url']}) not found")

    # Identity
    v = get_var(base, token, KNOWN_VARIDS["identity"])
    if v:
        print(f"  Current identity: {v.get('value')}")
        set_var(base, token, KNOWN_VARIDS["identity"], v, CP_ID)

    # Security profile (0 = no TLS cert, 1 = basic auth + TLS, 2 = mutual TLS)
    v = get_var(base, token, KNOWN_VARIDS["security_profile"])
    if v:
        print(f"  Current security_profile: {v.get('value')}")
        set_var(base, token, KNOWN_VARIDS["security_profile"], v, "0")

    # Ensure backend is ON
    v = get_var(base, token, KNOWN_VARIDS["backend_on"])
    if v and str(v.get("value")).lower() in ("false", "0", "off"):
        set_var(base, token, KNOWN_VARIDS["backend_on"], v, "true")
    elif v:
        print(f"  backend_on: {v.get('value')} (already on)")


def configure_network(base, token, new_ip="192.168.111.166", use_dhcp=False):
    """Try to set static IP. If varIds differ, print all matching vars."""
    print("\n── Network configuration ────────────────────────────")
    all_vars = list_all_vars(base, token)
    net_vars = find_network_vars(all_vars)

    if not net_vars:
        print("  No network/IP variables found via discovery. Printing all variable names:")
        for v in all_vars[:40]:
            vid = v.get("varId") or v.get("id") or "?"
            name = v.get("name") or v.get("varName") or "?"
            val = v.get("value", "")
            print(f"    {vid:12s}  {name:30s}  = {repr(val)[:40]}")
        return

    for v in net_vars:
        vid  = v.get("varId") or v.get("id") or "?"
        name = v.get("name") or v.get("varName") or "?"
        val  = v.get("value", "")
        print(f"  {vid:12s}  {name:30s}  = {repr(val)[:50]}")

    # Heuristic: find the static IP address variable
    for v in net_vars:
        name = (v.get("name") or v.get("varName") or "").lower()
        val  = str(v.get("value", ""))
        # Look for a var whose value looks like 192.168.1.166
        if "192.168.1.166" in val or ("ip" in name and "address" in name and "192.168." in val):
            vid = v.get("varId") or v.get("id")
            print(f"\n  Found IP variable: {vid} = {val}")
            if use_dhcp:
                # Look for DHCP enable var
                pass
            else:
                set_var(base, token, vid, v, new_ip)
                print(f"  → IP will be {new_ip}/24 after restart")
                break
    else:
        print(f"\n  Could not auto-detect IP variable.")
        print(f"  Set IP manually via vSECC web UI, or use DHCP.")


def verify_new(new_ip, token_old=None):
    """After restart, check if vSECC is reachable at new IP."""
    base = f"http://{new_ip}/api"
    print(f"\n── Verifying new IP {new_ip} ─────────────────────────")
    for attempt in range(12):
        try:
            token = login(base)
            v = get_var(base, token, KNOWN_VARIDS["csms_url"])
            url = v.get("value") if v else "?"
            print(f"  vSECC reachable at {new_ip}  |  CSMS URL = {url}")
            return True
        except Exception as e:
            print(f"  Attempt {attempt+1}/12 — not yet ({e})")
            time.sleep(5)
    print("  ERROR: vSECC not reachable at new IP after restart")
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dhcp", action="store_true", help="Use DHCP instead of static IP")
    parser.add_argument("--ip", default="192.168.111.166", help="New static IP for vSECC")
    parser.add_argument("--no-restart", action="store_true", help="Skip restart")
    args = parser.parse_args()

    print(f"Connecting to vSECC at {VSECC_OLD} ...")
    try:
        token = login(VSECC_OLD)
        print(f"  Authenticated (token len={len(token)})")
    except Exception as e:
        print(f"FAIL: {e}")
        print(f"\nIs the alias added?  Run:")
        print(f"  sudo ip addr add 192.168.1.200/24 dev enp3s0")
        sys.exit(1)

    configure_csms(VSECC_OLD, token)
    configure_network(VSECC_OLD, token, new_ip=args.ip, use_dhcp=args.dhcp)

    if args.no_restart:
        print("\n--no-restart: skipping restart. Apply manually.")
        return

    print("\n── Restarting vSECC ─────────────────────────────────")
    restart(VSECC_OLD, token)
    print("  Waiting 15 s for boot ...")
    time.sleep(15)

    ok = verify_new(args.ip)
    if ok:
        print(f"\nDONE. vSECC is at {args.ip}, CSMS URL set to direct MEA.")
        print(f"You can remove the alias: sudo ip addr del 192.168.1.200/24 dev enp3s0")
    else:
        print(f"\nRecheck: vSECC may still be at {VSECC_OLD} (IP change requires manual UI config)")
        print(f"If the IP didn't change, update VSECC_BASE in test_mea_section1.py to 192.168.1.166")
        print(f"and keep the alias: sudo ip addr add 192.168.1.200/24 dev enp3s0")


if __name__ == "__main__":
    main()
