# Oracle Cloud Setup

This guide creates the Ubuntu ARM64 host used by the validated Open Cloud Assistant v0.1.0 deployment path.

Oracle changes its console UI over time, so labels may move slightly. The architecture choices matter more than the exact screen layout.

## Current Oracle Free Tier facts

Oracle's official Free Tier documentation currently states:

- Always Free compute resources must be provisioned in the tenancy's **home region**.
- For Always Free tenancies, Ampere A1 (`VM.Standard.A1.Flex`) capacity is equivalent to **2 OCPUs and 12 GB of memory** across A1 instances.
- Current Always Free A1 usage is expressed as 1,500 OCPU-hours and 9,000 GB-hours per month.
- "Out of host capacity" can occur; Oracle recommends trying another availability domain where available or retrying later.
- A public IP is needed for direct internet SSH unless you use a bastion/private-access design.

Official references:

- https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm
- https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm
- https://docs.oracle.com/en-us/iaas/Content/Compute/Tasks/launchinginstance.htm
- https://docs.oracle.com/en-us/iaas/Content/Network/Tasks/assign-public-ip-instance-launch.htm

## 1. Create/sign in to Oracle Cloud

Create an OCI account or sign in to an existing tenancy.

During account creation, choose the **home region** carefully. The Always Free compute allocation is tied to that home region. Oracle may request a phone number and payment card for identity verification; check Oracle's current terms before proceeding.

## 2. Create a VCN with internet connectivity

For a first server, Oracle recommends creating a Virtual Cloud Network (VCN) before the instance.

In the OCI Console:

1. Open **Networking → Virtual Cloud Networks**.
2. Start the VCN wizard.
3. Choose **Create VCN with Internet Connectivity**.
4. Create the public/private subnet layout with the generated internet gateway and routes.

You will place the assistant VM in the **public subnet** for direct SSH access.

## 3. Create the compute instance

Open **Compute → Instances → Create instance**.

Recommended values:

| Field | Value |
|---|---|
| Name | `open-cloud-assistant` or any name you prefer |
| Image | Canonical Ubuntu 24.04 |
| Shape | `VM.Standard.A1.Flex` |
| OCPUs | 2 |
| Memory | 12 GB |
| VCN | the VCN you created |
| Subnet | public subnet |
| Public IPv4 | enabled |
| Boot volume | 50 GB/default suitable size |

The 2 OCPU / 12 GB recommendation uses the documented Always Free A1 allocation for an Always Free tenancy. If you use another paid/free allocation, choose resources that match your account rather than assuming every tenancy is identical.

### If A1 is unavailable

If OCI reports **Out of host capacity**, do not rewrite Open Cloud Assistant around a different architecture. Capacity is an OCI allocation issue. Try another availability domain if your region has one, retry later, or use another Ubuntu ARM64 VPS.

## 4. Add an SSH key

OCI can generate a key pair or accept your existing public key.

### Option A — let OCI generate it

Download the private key immediately and store it somewhere you control. Do not upload it to GitHub, paste it into chat, or leave it in a shared folder.

### Option B — use your own key

Upload the **public** key only. Keep the private key on your local computer.

## 5. Network security

You need SSH access on TCP 22. Prefer restricting the source to your own public IP/CIDR rather than allowing the entire internet when practical.

For the core assistant you do **not** need to expose:

- Hermes API port `8642`;
- Vellum runtime ports;
- Fleet internal state;
- any database port.

Browser preview is deliberately localhost-bound. Telegram/Discord gateway operation should not be implemented by opening the Hermes API to the public internet.

## 6. Record the public IP

After the instance becomes **Running**, copy its public IPv4 address from the instance details page.

OCI can assign an ephemeral public IP. If you later replace the instance or its IP assignment changes, update the SSH target you use locally.

## 7. Connect from macOS/Linux

OCI Ubuntu images use username `ubuntu`.

```bash
chmod 400 ~/Downloads/your-private-key.key
ssh -i ~/Downloads/your-private-key.key ubuntu@YOUR_PUBLIC_IP
```

Oracle's SSH documentation also uses `chmod 400` and the `ubuntu` username for Ubuntu images.

Official SSH reference:

https://docs.oracle.com/en-us/iaas/Content/Compute/Tasks/connect-to-linux-instance.htm

## 8. Connect from Windows

Recent Windows versions include OpenSSH in PowerShell:

```powershell
ssh -i C:\Users\YOUR_NAME\Downloads\your-private-key.key ubuntu@YOUR_PUBLIC_IP
```

If Windows rejects private-key permissions, move the key into your user profile and restrict its ACL so other local users cannot read it.

## 9. Verify Ubuntu

Inside the server:

```bash
cat /etc/os-release
uname -m
```

The validated public path is Ubuntu 24.04 on `aarch64`/ARM64.

## 10. Continue with Open Cloud Assistant

Install base prerequisites:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git xz-utils unzip python3 python3-venv python3-pip sudo dbus-user-session procps
```

Then continue at **Clone Open Cloud Assistant** in [COMPLETE_SETUP_GUIDE.md](COMPLETE_SETUP_GUIDE.md).

## Optional: safer remote access later

Once the initial deployment works, you may choose a private overlay network such as Tailscale or an OCI Bastion and remove broad public SSH exposure. That is an operational choice, not a requirement of the core source tree.

Whatever remote-access method you use, keep the assistant API itself private unless you deliberately add an authenticated reverse proxy/tunnel and understand the exposure.
