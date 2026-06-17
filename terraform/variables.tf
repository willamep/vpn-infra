variable "proxmox_url" {
  description = "Proxmox API URL, e.g. https://192.168.1.10:8006/"
  type        = string
}

variable "proxmox_api_token" {
  description = "API token: user@pam!token_name=secret"
  type        = string
  sensitive   = true
}

variable "template_id" {
  description = "VM ID of the Proxmox template"
  type        = number
}

variable "node_name" {
  description = "Proxmox node name"
  type        = string
  default     = "pve"
}
