provider "oci" {
  auth                = var.oci_auth
  region              = var.region
  config_file_profile = var.oci_config_profile
}
