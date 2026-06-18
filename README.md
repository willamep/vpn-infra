# vpn-infra

An infrastructure project for automated deployment of a WireGuard-over-fakeTCP infrastructure using Terraform and Ansible. An experiment exploring DPI bypass methods by encapsulating WireGuard traffic in fakeTCP.

The project was debugged on a local Proxmox stand, so it ships with Terraform for that provider. If you wish, you can rewrite it for your own provider or use only the Ansible playbook.

> 🇷🇺 Русская версия — [README_RUS.md](README_RUS.md).

## Idea

Modern DPI systems are good at spotting the signatures of various bypass methods and blocking them; this method will sooner or later be blocked just the same. Whatever traffic obfuscation we apply, it will differ from the original signature — which is exactly why there are precedents of VLESS+REALITY being blocked.

**Typical Web traffic** has a structure like this:

```
IP
└── TCP
    └── TLS
        └── HTTP
```

First comes theTCP handshake:

```
SYN
SYN-ACK
ACK
```

Then the TLS handshake begins:

```
ClientHello
ServerHello...
```

And this TLS part is very recognizable: from it DPI can tell that the traffic is either legitimate web traffic or a disguised VPN — i.e. it keeps analyzing the signature, suspecting obfuscation.

**My idea** is to drop TLS, making the traffic less recognizable to DPI. Unlike solutions that masquerade as HTTPS, faketcp traffic does not imitate web protocols and looks like an arbitrary TCP exchange between two nodes. At the time of development, this kind of traffic was not subject to active filtering.

However, you can't simply wrap traffic into fake TCP. User traffic is carried by WireGuard, which runs over UDP. That WireGuard UDP traffic is then encapsulated into faketcp using the [udp2raw](https://github.com/wangyu-/udp2raw) tool.

```
IP
└── TCP (fake)
    └── encrypted WireGuard UDP
```

### Limitations

Because WireGuard adds its own data and udp2raw adds faketcp on top, the packet size grows, so to let the traffic pass through every hop you have to lower the MTU to ~1300 — which increases the transmission overhead and reduces the channel throughput.

> In tests on a 1 Gbit/s link, the best result was 135 Mbit/s download and 50 Mbit/s upload.

The bypass works only until a DPI system takes an interest in analyzing this kind of traffic, because the method has 2 key problems:

1. Identical packet size

   WG normalizes all packets to the same configured size, and udp2raw also adds a roughly constant size on top. In server-to-server communication it is unlikely that all packets would be the same size. You can add packet-size randomization, but that leads to an even greater MTU reduction.
2. The faketcp signature

   It differs from the behavior of ordinary TCP, so it can potentially be used by DPI as a signature marker.

## Architecture

Two nodes:

| Node        | Role                                                                                                                                       |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `vpn-entry` | Entry point. `udp2raw` in **client** mode — wraps UDP into faketcp and sends it to the exit.                                              |
| `vpn-exit`  | Exit point. `udp2raw` in **server** mode + a WireGuard server ([wg-easy](https://github.com/wg-easy/wg-easy)) in Docker. Egress to the internet. |

```
               faketcp tunnel (looks like TCP)
┌──────────┐    udp2raw client → udp2raw server   ┌──────────┐
│ vpn-entry│ ═══════════════════════════════════► │ vpn-exit │ ──► Internet
└──────────┘                                      └──────────┘
   ▲                                            wg-easy (WireGuard
   │  WireGuard UDP                              server in Docker)
WG client
```

1. The WireGuard client connects to `vpn-entry`.
2. On the `entry → exit` leg, WireGuard's UDP traffic is encapsulated into faketcp (`udp2raw`).
3. On `vpn-exit` the traffic is unwrapped and reaches the `wg-easy` WireGuard server, which routes it out to the internet.

> Because of the double encapsulation, the WireGuard MTU is lowered (see the value in `group_vars`).

## Firewall

Both nodes run an **nftables** firewall with a default `drop` policy (the `firewall` role, loaded by a dedicated `vpn-firewall.service` unit) to minimize the attack surface and stay closed to DPI scanners.

| Node        | Open to the world                              | Open to the peer node only                                |
| ----------- | ---------------------------------------------- | ---------------------------------------------------------- |
| both        | TCP `22` (SSH)                                 | —                                                          |
| `vpn-entry` | UDP WireGuard port (clients connect here)      | everything else — only from `vpn-exit`                     |
| `vpn-exit`  | — (SSH only)                                   | everything else, including the faketcp port — only from `vpn-entry` |

It uses its **own** table `inet vpn_firewall`, and `flush ruleset` is never executed — so Docker's tables (wg-easy port publishing, `FORWARD`, `DOCKER-USER`) stay untouched; for the same reason the stock `nftables.service` is disabled. ICMP is left open for Path MTU Discovery (the WireGuard-over-faketcp MTU is tuned by hand), and `ct state invalid drop` also suppresses the kernel `RST` for faketcp packets, replacing udp2raw's own `-a` rule.

## Tech stack

- **Terraform** (`bpg/proxmox`) — provisions VMs from a cloud-init template.
- **Ansible** — node configuration: roles, systemd templates, secrets in Ansible Vault.
- **udp2raw** — transport obfuscation (faketcp).
- **WireGuard** via **wg-easy** in **Docker** — VPN server with a web UI.

## Repository layout

```
vpn-infra/
├── terraform/                 # VM provisioning on Proxmox
│   ├── providers.tf           # bpg/proxmox provider
│   ├── main.tf                # vpn-entry / vpn-exit VMs + Ansible inventory generation
│   ├── variables.tf
│   ├── outputs.tf             # IP addresses of the created VMs
│   └── inventory.tmpl         # Inventory template for Ansible
│
└── ansible/                   # Node configuration
    ├── ansible.cfg
    ├── vpn.yml                # Main playbook
    ├── inventory/hosts.yml    # Generated by Terraform
    ├── group_vars/vpn_nodes/  # Shared variables + vault
    ├── host_vars/             # Per-node parameters (client/server mode)
    └── roles/
        ├── docker/            # Docker install + wg-easy deployment
        ├── udp2raw/           # udp2raw install + systemd unit
        └── firewall/          # nftables firewall (own table + loader unit)
```

After `apply`, Terraform renders `ansible/inventory/hosts.yml` from `inventory.tmpl` itself, so Ansible immediately gets the data of the created VMs.

## Deployment

### 0. Prerequisites

- A **Proxmox VE** node/cluster with an API token.
- A ready **cloud-init template** VM (Ubuntu/Debian) with the QEMU Guest Agent.
- **Terraform** (≥ 1.x) and **Ansible** installed locally.
- An SSH key for the `ansible` user.

### 1. Terraform — provision the VMs

```bash
cd terraform

# Fill in terraform.tfvars (see variables.tf):
#   proxmox_url, proxmox_api_token, template_id, node_name
# and put the public SSH key into the ./list-ssh-keys file

terraform init       # initialize providers (once)
terraform validate   # syntax check
terraform plan       # preview
terraform apply      # create VMs + generate the Ansible inventory
```

After `apply`, the node IP addresses are available in `outputs`, and the Ansible inventory is generated automatically.

### 2. Ansible — configure the VPN

```bash
cd ../ansible

# Prepare the secrets:
#   - vault-password.txt (the Ansible Vault password)
#   - encrypt the vault files: ansible-vault encrypt group_vars/vpn_nodes/vault.yml ...

ansible-playbook vpn.yml --syntax-check   # syntax check
ansible-playbook vpn.yml --check          # dry run, no changes
ansible-playbook vpn.yml                  # real run
```

The playbook:

1. installs Docker and brings up `wg-easy` on `vpn-exit`;
2. installs `udp2raw` on both nodes (client on entry, server on exit) and registers the systemd service;
3. applies the nftables firewall on both nodes (see [Firewall](#firewall)).

### 3. Connecting

The wg-easy web UI is available on `localhost:51820` because of the firewall rules, or on `vpn-exit-ip:51821` if the firewall role was applied. There you create a WireGuard config for the client, which connects to `vpn-entry`.

## Secrets

Sensitive data is **not stored in the repository** (see `.gitignore`):

- `terraform.tfvars`, `*.tfstate`, `list-ssh-keys` — Terraform;
- `vault-password.txt`, encrypted `vault.yml` — Ansible Vault (udp2raw password, wg-easy password hash);
- the generated `ansible/inventory/hosts.yml`.

## Technical highlights

- **Terraform → Ansible** wiring: state (the IP addresses of the created VMs) is passed from Terraform into the Ansible inventory through the `inventory.tmpl` template.
- Idempotent node configuration via Ansible roles: systemd units from Jinja2 templates, handlers, and different udp2raw modes (client/server) selected through `host_vars`.
- Secret management: Ansible Vault, a `vars`/`vault` split, and a strict `.gitignore` tailored for IaC.
- The networking details of double encapsulation: faketcp obfuscation, MTU tuning, and routing WireGuard traffic over udp2raw.
