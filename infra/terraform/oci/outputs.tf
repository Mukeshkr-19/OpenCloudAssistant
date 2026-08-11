output "instance_id" {
  description = "OCI instance OCID."
  value       = oci_core_instance.opencloud.id
}

output "availability_domain" {
  description = "Availability domain selected for the instance."
  value       = local.selected_availability_domain
}

output "ubuntu_image_name" {
  description = "Canonical Ubuntu platform image selected by Terraform."
  value       = data.oci_core_images.ubuntu.images[0].display_name
}

output "public_ip" {
  description = "Public IP assigned to the assistant host."
  value       = oci_core_instance.opencloud.public_ip
}

output "private_ip" {
  description = "Private VCN address assigned to the assistant host."
  value       = oci_core_instance.opencloud.private_ip
}

output "ssh_command" {
  description = "Convenience SSH command for the Ubuntu host."
  value       = "ssh ubuntu@${oci_core_instance.opencloud.public_ip}"
}

output "cloud_init_status_command" {
  description = "Command to wait for cloud-init after SSH."
  value       = "ssh ubuntu@${oci_core_instance.opencloud.public_ip} cloud-init status --wait"
}

output "vcn_id" {
  description = "Created VCN OCID."
  value       = oci_core_vcn.opencloud.id
}

output "subnet_id" {
  description = "Created public subnet OCID."
  value       = oci_core_subnet.public.id
}
