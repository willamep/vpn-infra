output "vm_ips" {
  value = {
    for name, vm in proxmox_virtual_environment_vm.vpn_vm : name => {
      ipv4 = vm.ipv4_addresses[1][0]
      ipv6 = vm.ipv6_addresses[1][0]
    }
  }
}