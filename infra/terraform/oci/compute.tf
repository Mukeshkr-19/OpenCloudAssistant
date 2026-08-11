resource "oci_core_instance" "opencloud" {
  availability_domain = local.selected_availability_domain
  compartment_id      = var.compartment_ocid
  display_name        = var.instance_display_name
  shape               = var.instance_shape
  freeform_tags       = local.common_tags

  dynamic "shape_config" {
    for_each = can(regex("\\.Flex$", var.instance_shape)) ? [1] : []

    content {
      ocpus         = var.ocpus
      memory_in_gbs = var.memory_in_gbs
    }
  }

  create_vnic_details {
    subnet_id        = oci_core_subnet.public.id
    assign_public_ip = true
    display_name     = "${var.instance_display_name}-vnic"
    hostname_label   = var.hostname_label
  }

  source_details {
    source_type             = "image"
    source_id               = local.selected_image_id
    boot_volume_size_in_gbs = var.boot_volume_size_in_gbs
  }

  metadata = {
    ssh_authorized_keys = trimspace(
      file(pathexpand(var.ssh_public_key_path))
    )

    user_data = base64encode(
      templatefile(
        "${path.module}/cloud-init.yaml",
        {
          auto_install_opencloud = tostring(var.auto_install_opencloud)
          opencloud_repository   = var.opencloud_repository
          opencloud_ref          = var.opencloud_ref
        }
      )
    )
  }

  lifecycle {
    precondition {
      condition     = length(data.oci_core_images.ubuntu.images) > 0
      error_message = "No compatible Canonical Ubuntu 24.04 image was found for the selected OCI shape."
    }
  }
}
