variable "tenancy_ocid" {
  description = "OCI tenancy OCID. Supply through terraform.tfvars or TF_VAR_tenancy_ocid."
  type        = string

  validation {
    condition     = can(regex("^ocid1\\.tenancy\\.", var.tenancy_ocid))
    error_message = "tenancy_ocid must be an OCI tenancy OCID."
  }
}

variable "compartment_ocid" {
  description = "OCI compartment OCID where Open Cloud Assistant resources are created."
  type        = string

  validation {
    condition     = can(regex("^ocid1\\.(compartment|tenancy)\\.", var.compartment_ocid))
    error_message = "compartment_ocid must be an OCI compartment or root tenancy OCID."
  }
}

variable "region" {
  description = "OCI region identifier."
  type        = string
}

variable "oci_auth" {
  description = "OCI provider authentication mode."
  type        = string
  default     = "APIKey"
}

variable "oci_config_profile" {
  description = "Profile from the local OCI CLI or SDK config file."
  type        = string
  default     = "DEFAULT"
}

variable "availability_domain" {
  description = "Optional explicit availability domain. Empty selects one from OCI."
  type        = string
  default     = ""
}

variable "availability_domain_index" {
  description = "Availability domain index used when availability_domain is empty."
  type        = number
  default     = 0

  validation {
    condition     = var.availability_domain_index >= 0
    error_message = "availability_domain_index must be zero or greater."
  }
}

variable "instance_display_name" {
  description = "OCI display name for the assistant host."
  type        = string
  default     = "opencloud-assistant"
}

variable "hostname_label" {
  description = "DNS hostname label for the primary VNIC."
  type        = string
  default     = "opencloud"
}

variable "instance_shape" {
  description = "OCI compute shape. The default targets Ampere Flex capacity."
  type        = string
  default     = "VM.Standard.A1.Flex"
}

variable "ocpus" {
  description = "OCPUs requested when using a Flex shape."
  type        = number
  default     = 1

  validation {
    condition     = var.ocpus > 0
    error_message = "ocpus must be greater than zero."
  }
}

variable "memory_in_gbs" {
  description = "Memory requested when using a Flex shape."
  type        = number
  default     = 6

  validation {
    condition     = var.memory_in_gbs > 0
    error_message = "memory_in_gbs must be greater than zero."
  }
}

variable "boot_volume_size_in_gbs" {
  description = "Boot volume size."
  type        = number
  default     = 50

  validation {
    condition = (
      var.boot_volume_size_in_gbs >= 50 &&
      var.boot_volume_size_in_gbs <= 32768
    )
    error_message = "boot_volume_size_in_gbs must be between 50 and 32768."
  }
}

variable "ssh_public_key_path" {
  description = "Local path to the SSH public key injected into the Ubuntu instance."
  type        = string
}

variable "ssh_allowed_cidr" {
  description = "CIDR permitted to reach SSH. Prefer a single trusted address with /32."
  type        = string
}

variable "vcn_cidr" {
  description = "CIDR for the Open Cloud Assistant VCN."
  type        = string
  default     = "10.40.0.0/16"
}

variable "subnet_cidr" {
  description = "CIDR for the public assistant subnet."
  type        = string
  default     = "10.40.10.0/24"
}

variable "auto_install_opencloud" {
  description = "When true, cloud-init clones the public repository and runs the CLI installer."
  type        = bool
  default     = false
}

variable "opencloud_repository" {
  description = "Public repository cloned when auto_install_opencloud is enabled."
  type        = string
  default     = "https://github.com/Mukeshkr-19/OpenCloudAssistant.git"
}

variable "opencloud_ref" {
  description = "Git branch or tag used for optional automatic installation."
  type        = string
  default     = "main"
}

variable "freeform_tags" {
  description = "Additional OCI free-form tags."
  type        = map(string)
  default     = {}
}
