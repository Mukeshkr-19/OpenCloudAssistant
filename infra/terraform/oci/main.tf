data "oci_identity_availability_domains" "available" {
  compartment_id = var.tenancy_ocid
}

data "oci_core_images" "ubuntu" {
  compartment_id           = var.compartment_ocid
  operating_system         = "Canonical Ubuntu"
  operating_system_version = "24.04"
  shape                    = var.instance_shape
  state                    = "AVAILABLE"
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"
}

locals {
  selected_availability_domain = (
    var.availability_domain != ""
    ? var.availability_domain
    : data.oci_identity_availability_domains.available.availability_domains[
      var.availability_domain_index
    ].name
  )

  selected_image_id = data.oci_core_images.ubuntu.images[0].id

  common_tags = merge(
    {
      Project   = "OpenCloudAssistant"
      ManagedBy = "Terraform"
    },
    var.freeform_tags
  )
}
