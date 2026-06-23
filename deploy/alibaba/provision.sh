#!/usr/bin/env bash
# Chorus — Alibaba Cloud ECS provisioning + deployment script.
#
# This script provisions an ECS instance on Alibaba Cloud (ap-southeast-1,
# Singapore) and deploys the Chorus backend using the Alibaba Cloud CLI
# (aliyun) and systemd. It demonstrates direct use of Alibaba Cloud ECS
# and Qwen Cloud (DashScope / Alibaba Cloud Model Studio).
#
# Usage:
#   ./provision.sh            — provision ECS + deploy Chorus
#   ./provision.sh deploy     — deploy/update on an already-running instance
#
# Prereqs:
#   - aliyun CLI installed and configured (aliyun configure)
#   - SSH key pair created in the ECS console
#   - .env at repo root with QWEN_API_KEY set (Alibaba Cloud Model Studio)
#
# Live instance: 43.106.15.59 (i-t4nifs3uru6sgp7fbhl9, guardianity-demo)
set -euo pipefail

REGION="ap-southeast-1"
INSTANCE_TYPE="ecs.e-c1m2.large"      # 2 vCPU · 4 GiB
IMAGE_ID="ubuntu_22_04_x64_20G_alibase_20240926.vhd"
SECURITY_GROUP="sg-t4nicfn340fmdbjo2rp1"
INSTANCE_NAME="guardianity-demo"
APP_PORT="8002"
NGINX_PORT="8080"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REMOTE_PATH="/opt/chorus"
MODE="${1:-provision}"

# ── helpers ──────────────────────────────────────────────────────────────────

require_aliyun() {
  if ! command -v aliyun &>/dev/null; then
    echo "ERROR: aliyun CLI not found. Install from https://www.alibabacloud.com/help/en/cli/install" >&2
    exit 1
  fi
}

require_env() {
  if [ ! -f "$REPO_ROOT/.env" ]; then
    echo "ERROR: $REPO_ROOT/.env not found. cp .env.example .env and set QWEN_API_KEY." >&2
    exit 1
  fi
}

# ── provision: create ECS instance ───────────────────────────────────────────

provision_ecs() {
  require_aliyun
  echo ">> Creating ECS instance ($INSTANCE_TYPE) in $REGION …"

  # Create the instance using the Alibaba Cloud ECS API via aliyun CLI.
  INSTANCE_ID=$(aliyun ecs CreateInstance \
    --RegionId "$REGION" \
    --InstanceName "$INSTANCE_NAME" \
    --InstanceType "$INSTANCE_TYPE" \
    --ImageId "$IMAGE_ID" \
    --SecurityGroupId "$SECURITY_GROUP" \
    --InternetMaxBandwidthOut 10 \
    --InternetChargeType PayByTraffic \
    --SystemDisk.Category cloud_essd \
    --SystemDisk.Size 40 \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['InstanceId'])")

  echo "   InstanceId: $INSTANCE_ID"

  # Allocate a public IP for the instance.
  PUBLIC_IP=$(aliyun ecs AllocatePublicIpAddress \
    --RegionId "$REGION" \
    --InstanceId "$INSTANCE_ID" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['IpAddress'])")

  echo "   PublicIp: $PUBLIC_IP"

  # Start the instance.
  aliyun ecs StartInstance --RegionId "$REGION" --InstanceId "$INSTANCE_ID"
  echo "   Starting… (wait ~60s for SSH to be ready)"

  # Open inbound ports in the security group via the ECS Security Group API.
  echo ">> Opening ports in security group $SECURITY_GROUP …"

  aliyun ecs AuthorizeSecurityGroup \
    --RegionId "$REGION" \
    --SecurityGroupId "$SECURITY_GROUP" \
    --IpProtocol tcp --PortRange 22/22 \
    --SourceCidrIp 0.0.0.0/0 \
    --Description "SSH"

  aliyun ecs AuthorizeSecurityGroup \
    --RegionId "$REGION" \
    --SecurityGroupId "$SECURITY_GROUP" \
    --IpProtocol tcp --PortRange 80/80 \
    --SourceCidrIp 0.0.0.0/0 \
    --Description "HTTP"

  aliyun ecs AuthorizeSecurityGroup \
    --RegionId "$REGION" \
    --SecurityGroupId "$SECURITY_GROUP" \
    --IpProtocol tcp --PortRange "$NGINX_PORT/$NGINX_PORT" \
    --SourceCidrIp 0.0.0.0/0 \
    --Description "Chorus Track3 AgentSociety"

  echo "   Ports open: 22, 80, $NGINX_PORT"
  echo ">> Instance ready. SSH: ssh root@$PUBLIC_IP"
  echo "   Run: ./provision.sh deploy root@$PUBLIC_IP"
}

# ── deploy: copy app + configure systemd + nginx ─────────────────────────────

deploy() {
  require_env
  local TARGET="${1:-root@43.106.15.59}"
  echo ">> Deploying Chorus to $TARGET …"

  # Sync the repo (excluding venv, data, secrets).
  rsync -az \
    --exclude='.venv' --exclude='__pycache__' --exclude='*.pyc' \
    --exclude='.env' --exclude='.env.*' --exclude='*.disk' --exclude='.git' \
    "$REPO_ROOT/" "$TARGET:$REMOTE_PATH/"

  ssh "$TARGET" bash <<REMOTE
set -euo pipefail

# Install Python deps in a venv.
cd $REMOTE_PATH
python3 -m venv .venv
.venv/bin/pip install -q -r engine/requirements.txt

# Write the systemd service — uses Qwen Cloud (DashScope) via QWEN_API_KEY in .env.
cat > /etc/systemd/system/chorus.service <<SERVICE
[Unit]
Description=Chorus — Track 3 Agent Society
After=network.target

[Service]
WorkingDirectory=$REMOTE_PATH
Environment=PYTHONPATH=$REMOTE_PATH
ExecStart=$REMOTE_PATH/.venv/bin/uvicorn chorus.app:get_app \\
          --factory --host 0.0.0.0 --port $APP_PORT
Restart=always
RestartSec=5
EnvironmentFile=$REMOTE_PATH/.env
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable chorus
systemctl restart chorus

echo "   chorus.service: \$(systemctl is-active chorus)"
echo "   Health: \$(curl -s http://localhost:$APP_PORT/api/health)"
REMOTE

  echo ">> Done. Chorus live at http://$TARGET:$NGINX_PORT"
  echo "   Proof: curl http://$TARGET:$NGINX_PORT/api/health"
}

# ── main ─────────────────────────────────────────────────────────────────────

case "$MODE" in
  provision) provision_ecs ;;
  deploy)    deploy "${2:-root@43.106.15.59}" ;;
  *)
    echo "Usage: $0 [provision|deploy [user@host]]" >&2
    exit 1 ;;
esac
