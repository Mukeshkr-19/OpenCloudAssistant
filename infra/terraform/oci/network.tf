resource "oci_core_vcn" "opencloud" {
  compartment_id = var.compartment_ocid
  cidr_block     = var.vcn_cidr
  display_name   = "${var.instance_display_name}-vcn"
  dns_label      = "opencloudvcn"
  freeform_tags  = local.common_tags
}

resource "oci_core_internet_gateway" "opencloud" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.opencloud.id
  display_name   = "${var.instance_display_name}-internet-gateway"
  enabled        = true
  freeform_tags  = local.common_tags
}

resource "oci_core_route_table" "public" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.opencloud.id
  display_name   = "${var.instance_display_name}-public-routes"
  freeform_tags  = local.common_tags

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.opencloud.id
  }
}

resource "oci_core_security_list" "assistant" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.opencloud.id
  display_name   = "${var.instance_display_name}-security"
  freeform_tags  = local.common_tags

  egress_security_rules {
    protocol         = "all"
    destination      = "0.0.0.0/0"
    destination_type = "CIDR_BLOCK"
    stateless        = false
  }

  ingress_security_rules {
    protocol    = "6"
    source      = var.ssh_allowed_cidr
    source_type = "CIDR_BLOCK"
    stateless   = false

    tcp_options {
      min = 22
      max = 22
    }
  }
}

resource "oci_core_subnet" "public" {
  compartment_id             = var.compartment_ocid
  vcn_id                     = oci_core_vcn.opencloud.id
  cidr_block                 = var.subnet_cidr
  display_name               = "${var.instance_display_name}-public-subnet"
  dns_label                  = "assistant"
  prohibit_public_ip_on_vnic = false
  route_table_id             = oci_core_route_table.public.id
  security_list_ids          = [oci_core_security_list.assistant.id]
  freeform_tags              = local.common_tags
}
