# data "proxmox_virtual_environment_vm" "template" {
#   node_name = var.node_name
#   name      = var.template_name
# }

resource "proxmox_virtual_environment_vm" "vpn_vm" {
  for_each  = toset(["vpn-entry", "vpn-exit"])
  name      = each.value
  node_name = var.node_name

  clone {
    vm_id = var.template_id
  }

  agent {
    enabled = true  # QEMU Agent
  }

  cpu {
    cores = 2
    type  = "x86-64-v2-AES"
  }

  memory {
    dedicated = 1540
  }

  network_device {
    bridge = "vmbr0"
    model  = "virtio"
  }

  disk {
    datastore_id = "local-lvm"
    interface    = "scsi0"
    size         = 20
  }

  initialization {
    ip_config {
      ipv4 {
        address = "dhcp"
      }
      ipv6 {
        address = "dhcp"
      }
    }
    user_account {
      username = "ansible"
      keys     = [file("./list-ssh-keys")]
    }
  }
}

resource "local_file" "ansible_inventory" {
  content = templatefile("inventory.tmpl", {
    vms = [
      for i, vm in proxmox_virtual_environment_vm.vpn_vm : {
        name = vm.name
        ip   = vm.ipv4_addresses[1][0]
      }
    ]
  })
  filename = "../ansible/inventory/hosts.yml"
  file_permission      = "0640"
  directory_permission = "0755"
}