# OCI Terraform Deployment

This Terraform root module provisions the cloud-host layer for Open Cloud
Assistant on Oracle Cloud Infrastructure.

It is intentionally small and infrastructure-focused.

## What it creates

Terraform manages:

- one OCI VCN;
- one public subnet;
- one Internet Gateway;
- one route table;
- one security list;
- SSH ingress from one caller-supplied CIDR;
- outbound Internet access;
- one Canonical Ubuntu 24.04 compute instance;
- an SSH public key;
- cloud-init bootstrap metadata.

No OCI credentials are stored in this directory.

## Authentication

Use an existing OCI CLI or SDK configuration profile or supported OCI provider
environment variables.

The default Terraform provider profile is:

    DEFAULT

Do not commit API private keys, fingerprints, authentication tokens, real
terraform.tfvars files, or Terraform state.

## Configure

Copy the example:

    cp terraform.tfvars.example terraform.tfvars

Edit the required values:

    tenancy_ocid
    compartment_ocid
    region
    ssh_public_key_path
    ssh_allowed_cidr

Use a narrow SSH source such as your trusted public IPv4 address with a `/32`
mask instead of exposing SSH to the entire Internet.

## Validate

    terraform init
    terraform fmt -check -recursive
    terraform validate

## Plan

    terraform plan

Review every resource before applying.

## Apply

    terraform apply

After creation:

    terraform output ssh_command
    terraform output cloud_init_status_command

## Optional automatic OpenCloud installation

By default:

    auto_install_opencloud = false

Cloud-init prepares the host but does not run the product installer.

To test fully automated handoff:

    auto_install_opencloud = true

Cloud-init then clones the selected public repository reference and runs:

    OPEN_CLOUD_CHANNELS=cli ./setup.sh --install

Provider credentials and personal assistant configuration are still not
embedded into Terraform or cloud-init.

## Destroy

Review the plan before destruction:

    terraform plan -destroy

Then:

    terraform destroy

Terraform state can contain infrastructure identifiers and must remain outside
public Git.

## Validation boundary

Repository CI performs formatting, provider initialization, and Terraform
configuration validation on hosted x86_64 and ARM64 Ubuntu runners.

CI does not use OCI credentials and does not create cloud resources.

A successful `terraform validate` is configuration proof, not evidence that a
specific OCI tenancy has successfully applied the plan.
