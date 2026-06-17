# vpn-infra

Эксперимент по обходу DPI-блокировок: WireGuard-VPN, чей UDP-трафик заворачивается в «безликий» TCP через **udp2raw (faketcp)**, чтобы для систем глубокой инспекции пакетов соединение выглядело как обычный TCP-поток.

Вся инфраструктура описана кодом: **Terraform** поднимает виртуальные машины в **Proxmox VE**, **Ansible** настраивает на них VPN-стек.

> ⚠️ **Дисклеймер.** Проект создан в исследовательских и образовательных целях — для изучения сетей и техник обфускации транспорта. Используйте только в законных целях и на инфраструктуре, которой владеете или имеете право управлять.

---

## Идея

DPI-системы умеют детектировать handshake WireGuard по сигнатуре UDP-пакетов и блокировать соединение. Один из способов обхода — обернуть UDP-трафик WireGuard в TCP при помощи [udp2raw](https://github.com/wangyu-/udp2raw) в режиме `faketcp`: снаружи это неотличимо от обычного TCP-соединения.

Цель эксперимента — собрать рабочий стенд такого обхода, полностью автоматизировать его через IaC и проверить подход на практике.

### Что показал эксперимент

В этой версии **намеренно не используется рандомизация размеров пакетов**. Из-за этого пакеты в туннеле имеют постоянный размер, и продвинутый DPI в теории способен среагировать на такую статистическую аномалию и заблокировать поток. Это осознанный компромисс: цель — проверить сам транспорт обфускации, а не построить решение, устойчивое к статистическому анализу. Рандомизатор размеров — логичный следующий шаг (см. [Roadmap](#roadmap)).

---

## Архитектура

Два узла:

| Узел        | Роль                                                                 |
|-------------|---------------------------------------------------------------------|
| `vpn-entry` | Точка входа. `udp2raw` в режиме **client** — заворачивает UDP в faketcp и шлёт на exit. |
| `vpn-exit`  | Точка выхода. `udp2raw` в режиме **server** + WireGuard-сервер ([wg-easy](https://github.com/wg-easy/wg-easy)) в Docker. Выход в интернет. |

```
                faketcp-туннель (выглядит как TCP)
  ┌──────────┐   udp2raw client  →  udp2raw server   ┌──────────┐
  │ vpn-entry│ ═══════════════════════════════════►  │ vpn-exit │ ──► Internet
  └──────────┘                                        └──────────┘
       ▲                                              wg-easy (WireGuard
       │ WireGuard UDP                                 сервер в Docker)
   WG-клиент
```

- Клиент WireGuard подключается к `vpn-entry`.
- На участке `entry → exit` UDP-трафик WireGuard инкапсулируется в faketcp (`udp2raw`).
- На `vpn-exit` трафик распаковывается и попадает в WireGuard-сервер `wg-easy`, который выпускает его в интернет.
- Из-за двойной инкапсуляции MTU WireGuard снижен (`1330`, см. `group_vars`).

---

## Технологический стек

- **Terraform** (`bpg/proxmox`) — провижининг VM из cloud-init шаблона.
- **Ansible** — конфигурация узлов: роли, шаблоны systemd, секреты в Ansible Vault.
- **udp2raw** — обфускация транспорта (faketcp).
- **WireGuard** через **wg-easy** в **Docker** — VPN-сервер с веб-панелью.
- **Proxmox VE** — платформа виртуализации.

---

## Структура репозитория

```
vpn-infra/
├── terraform/                 # Провижининг VM в Proxmox
│   ├── providers.tf           # Провайдер bpg/proxmox
│   ├── main.tf                # VM vpn-entry / vpn-exit + генерация Ansible inventory
│   ├── variables.tf
│   ├── outputs.tf             # IP-адреса созданных VM
│   └── inventory.tmpl         # Шаблон inventory для Ansible
│
└── ansible/                   # Конфигурация узлов
    ├── ansible.cfg
    ├── vpn.yml                # Главный playbook
    ├── inventory/hosts.yml    # Генерируется Terraform'ом
    ├── group_vars/vpn_nodes/  # Общие переменные + vault
    ├── host_vars/             # Параметры конкретных узлов (client/server режим)
    └── roles/
        ├── docker/            # Установка Docker + развёртывание wg-easy
        └── udp2raw/           # Установка udp2raw + systemd-юнит
```

Terraform после `apply` сам рендерит `ansible/inventory/hosts.yml` из `inventory.tmpl`, так что Ansible сразу видит созданные VM.

---

## Предварительные требования

- Нода/кластер **Proxmox VE** с API-токеном.
- Готовый **cloud-init шаблон** VM (Ubuntu/Debian) с QEMU Guest Agent.
- Локально установленные **Terraform** (≥ 1.x) и **Ansible**.
- SSH-ключ для пользователя `ansible`.

---

## Развёртывание

### 1. Terraform — поднять VM

```bash
cd terraform

# Заполнить terraform.tfvars (см. variables.tf):
#   proxmox_url, proxmox_api_token, template_id, node_name
# и положить публичный SSH-ключ в файл ./list-ssh-keys

terraform init       # инициализация провайдеров (один раз)
terraform validate   # проверка синтаксиса
terraform plan       # предпросмотр
terraform apply      # создание VM + генерация Ansible inventory
```

После `apply` в `outputs` будут IP-адреса узлов, а inventory для Ansible сгенерируется автоматически.

### 2. Ansible — настроить VPN

```bash
cd ../ansible

# Подготовить секреты:
#   - vault-password.txt (пароль от Ansible Vault)
#   - зашифровать vault-файлы: ansible-vault encrypt group_vars/vpn_nodes/vault.yml ...

ansible-playbook vpn.yml --syntax-check   # проверка синтаксиса
ansible-playbook vpn.yml --check          # сухой прогон без изменений
ansible-playbook vpn.yml                  # реальный запуск
```

Playbook:
1. ставит Docker и поднимает `wg-easy` на `vpn-exit`;
2. ставит `udp2raw` на оба узла (client на entry, server на exit) и регистрирует systemd-сервис.

### 3. Подключение

Веб-панель wg-easy доступна на `vpn-exit:51821` (по умолчанию слушает локально — пробрасывайте через SSH-туннель). Там создаётся конфиг WireGuard для клиента, который подключается к `vpn-entry`.

---

## Секреты

Чувствительные данные **не хранятся в репозитории** (см. `.gitignore`):

- `terraform.tfvars`, `*.tfstate`, `list-ssh-keys` — Terraform;
- `vault-password.txt`, зашифрованные `vault.yml` — Ansible Vault (пароль udp2raw, хеш пароля wg-easy);
- сгенерированный `ansible/inventory/hosts.yml`.

---

## Roadmap

- [ ] Добавить **рандомизацию размеров пакетов** для устойчивости к статистическому DPI.
- [ ] CI-проверки (`terraform fmt/validate`, `ansible-lint`).
- [ ] Автоматическая ротация ключей udp2raw / WireGuard.

---

## Технические решения

- Связка **Terraform → Ansible**: состояние (IP-адреса созданных VM) передаётся из Terraform в Ansible inventory через шаблон `inventory.tmpl`.
- Идемпотентная конфигурация узлов ролями Ansible: systemd-юниты из Jinja2-шаблонов, handlers, разные режимы udp2raw (client/server) через `host_vars`.
- Управление секретами: Ansible Vault, разделение `vars`/`vault`, строгий `.gitignore` под IaC.
- Сетевые тонкости двойной инкапсуляции: faketcp-обфускация, подбор MTU, маршрутизация трафика WireGuard поверх udp2raw.
