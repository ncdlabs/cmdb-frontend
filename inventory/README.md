# Sample inventory

This directory ships **generic demo CIs** (`example.com`, `10.0.0.0/24`) so the app runs out of the box.

Before deploying for real use, replace these files with your private CMDB (or sync from an external source of truth that is **not** committed here).

Do not commit:

- Real hostnames, customer domains, or personal usernames
- Public IPs, Tailscale (or other VPN) IPs, or home LAN addressing you care about
- Passwords, API tokens, private keys, kubeconfigs, or recovery codes
