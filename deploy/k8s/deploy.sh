#!/usr/bin/env bash
# Deploy the Couchbase Agent Operations Manager ("agent-ops") and the Couchbase
# ShopSmart demo ("pos-app") with Helm onto the single-node K3s cluster on
# mother (Rancher-managed), without a container registry:
#
#   1. builds the 5 images here with Docker, for mother's CPU architecture
#        AOM:   couchbase-aom-operations-manager, couchbase-aom-sample-mcp-servers, couchbase-aom-ui
#        demo:  pos-backend, pos-web
#   2. copies them to mother and imports them into K3s's containerd
#      (asks for your sudo password on mother once)
#   3. copies both charts to mother and runs `helm upgrade --install` there
#      (installs helm into ~/.local/bin on mother if it's missing)
#
# Usage, from this repository on your Mac:
#   AOM_ADMIN_PASSWORD='<12+ chars>' ./deploy/k8s/deploy.sh
#
# mother already runs AOM (agent-ops) and the cruise demos (royal-app, princess-app). To
# add this demo next to them, deploy only it, with the current AOM admin password:
#   AOM_ADMIN_PASSWORD='<current AOM password>' ONLY=pos ./deploy/k8s/deploy.sh
#
# Another node: TARGET=hal (ehind@192.168.111.5, deploy/k8s/values-hal.yaml, AOM already running there):
#   AOM_ADMIN_PASSWORD='<current AOM password>' TARGET=hal ONLY=pos ./deploy/k8s/deploy.sh
#
# Environment:
#   TARGET              "mother" (default) or "hal": picks MOTHER and VALUES below
#   MOTHER              ssh target              (default ehind@192.168.111.4; "local" = run on the node itself)
#   VALUES              chart values for the node (default deploy/k8s/values-<TARGET>.yaml)
#   AOM_DIR             AOM repository checkout (default ~/github-couchbaselabs/couchbase-agent-operations-manager)
#   AOM_ADMIN_PASSWORD  AOM dashboard admin password. On a fresh AOM the demo backend sets it as the
#                       first-login password; on an existing AOM it must be the current one. Prompted if unset.
#   ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY   optional LLM keys for AOM (else labelled stubs)
#   ONLY                "aom" or "pos" to deploy just one of them
#   SKIP_BUILD=1        reuse the images already on mother (set TAG to the tag to deploy)
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
ROOT=$(pwd)

TARGET="${TARGET:-mother}"
case "$TARGET" in
  mother) DEFAULT_HOST=ehind@192.168.111.4 ;;
  hal)    DEFAULT_HOST=ehind@192.168.111.5 ;;
  *)      echo "TARGET must be mother or hal (or set MOTHER and VALUES yourself)"; DEFAULT_HOST=ehind@192.168.111.4 ;;
esac
MOTHER="${MOTHER:-$DEFAULT_HOST}"
VALUES="${VALUES:-deploy/k8s/values-$TARGET.yaml}"
[ -f "$VALUES" ] || { echo "values file $VALUES not found"; exit 1; }
AOM_DIR="${AOM_DIR:-$HOME/github-couchbaselabs/couchbase-agent-operations-manager}"
TAG="${TAG:-$(date +%Y%m%d-%H%M%S)}"
ONLY="${ONLY:-}"
AOM_NS=agent-ops
SHOPSMART_NS=pos-app
REMOTE_DIR='~/pos-deploy'

say() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
remote() { if [ "$MOTHER" = local ]; then bash -lc "$1"; else ssh "$MOTHER" "bash -lc $(printf '%q' "$1")"; fi; }
remote_tty() { if [ "$MOTHER" = local ]; then bash -lc "$1"; else ssh -t "$MOTHER" "bash -lc $(printf '%q' "$1")"; fi; }

want() { [ -z "$ONLY" ] || [ "$ONLY" = "$1" ]; }

if want aom || want pos; then :; else echo "ONLY must be aom or pos"; exit 1; fi
# The checkout may be nested one level down (…/couchbase-agent-operations-manager/couchbase-agent-operations-manager).
if [ ! -d "$AOM_DIR/helm" ] && [ -d "$AOM_DIR/couchbase-agent-operations-manager/helm" ]; then
  AOM_DIR="$AOM_DIR/couchbase-agent-operations-manager"
fi
if want aom && [ ! -d "$AOM_DIR/helm/couchbase-agent-operations-manager" ]; then
  echo "AOM checkout not found at $AOM_DIR (set AOM_DIR)"; exit 1
fi
if want pos && [ -z "${AOM_ADMIN_PASSWORD:-}" ]; then
  read -r -s -p "AOM admin password (12+ characters): " AOM_ADMIN_PASSWORD; echo
fi

say "Checking $TARGET ($MOTHER)"
ARCH=$(remote 'uname -m')
case "$ARCH" in x86_64|amd64) PLATFORM=linux/amd64 ;; aarch64|arm64) PLATFORM=linux/arm64 ;; *) echo "unsupported arch $ARCH"; exit 1 ;; esac
remote 'kubectl get nodes -o wide' || { echo "kubectl doesn't work on mother"; exit 1; }
echo "Platform $PLATFORM, image tag $TAG"

IMAGES=()
build() { # name context
  say "Building $1:$TAG ($PLATFORM)"
  docker buildx build --platform "$PLATFORM" -t "$1:$TAG" --load "$2"
  IMAGES+=("$1:$TAG")
}

if [ -z "${SKIP_BUILD:-}" ]; then
  if want aom; then
    build couchbase-aom-operations-manager "$AOM_DIR/operations-manager"
    build couchbase-aom-sample-mcp-servers "$AOM_DIR/sample-mcp-servers"
    build couchbase-aom-ui "$AOM_DIR/ui"
  fi
  if want pos; then
    build pos-backend "$ROOT/backend"
    build pos-web "$ROOT/web"
  fi
  say "Copying ${#IMAGES[@]} images to mother and importing them into K3s"
  if [ "$MOTHER" = local ]; then
    docker save "${IMAGES[@]}" | sudo k3s ctr images import -
  else
    docker save "${IMAGES[@]}" | gzip -1 | ssh "$MOTHER" "cat > /tmp/pos-images.tar.gz"
    remote_tty 'gunzip -f /tmp/pos-images.tar.gz && sudo k3s ctr images import /tmp/pos-images.tar && rm -f /tmp/pos-images.tar'
  fi
fi

say "Copying the Helm charts to mother"
# Chart copies of the repo's init scripts and dump generator.
cp couchbase-init/init.sh sync-gateway/db-config.json databricks/generate_mock_dump.py helm/pos-app/files/
STAGE=$(mktemp -d)
mkdir -p "$STAGE/charts"
cp -R helm/pos-app "$STAGE/charts/pos-app"
cp "$VALUES" "$STAGE/pos-values.yaml"
want aom && cp -R "$AOM_DIR/helm/couchbase-agent-operations-manager" "$STAGE/charts/agent-ops"
if [ "$MOTHER" = local ]; then
  mkdir -p ~/pos-deploy && cp -R "$STAGE"/. ~/pos-deploy/
else
  remote "mkdir -p $REMOTE_DIR"
  tar -C "$STAGE" -czf - . | ssh "$MOTHER" "tar -C $REMOTE_DIR -xzf -"
fi
rm -rf "$STAGE"

# Secrets go into mode-600 values files on mother, never onto a command line.
yaml_str() { python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"; }
{
  echo "operationsManager:"
  echo "  providerApiKeys:"
  echo "    anthropic: $(yaml_str "${ANTHROPIC_API_KEY:-}")"
  echo "    openai: $(yaml_str "${OPENAI_API_KEY:-}")"
  echo "    gemini: $(yaml_str "${GEMINI_API_KEY:-}")"
} | remote "umask 077; cat > $REMOTE_DIR/aom-secrets.yaml"
if want pos; then
  printf 'aom:\n  adminPassword: %s\n' "$(yaml_str "$AOM_ADMIN_PASSWORD")" \
    | remote "umask 077; cat > $REMOTE_DIR/pos-secrets.yaml"
fi

if [ "$MOTHER" = local ]; then NODE_IP=$(hostname -I | awk '{print $1}'); else NODE_IP="${MOTHER#*@}"; fi

say "Installing with Helm on mother"
remote_tty "
set -euo pipefail
cd $REMOTE_DIR
export PATH=\$HOME/.local/bin:\$PATH
if [ ! -f \$HOME/.kube/config ] && [ -z \"\${KUBECONFIG:-}\" ] && [ -r /etc/rancher/k3s/k3s.yaml ]; then export KUBECONFIG=/etc/rancher/k3s/k3s.yaml; fi
if ! command -v helm >/dev/null; then
  echo 'Installing helm into ~/.local/bin ...'
  mkdir -p \$HOME/.local/bin
  curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | HELM_INSTALL_DIR=\$HOME/.local/bin USE_SUDO=false bash
fi
if [ '$ONLY' != pos ]; then
  echo '--- agent-ops (Couchbase Agent Operations Manager) ---'
  helm upgrade --install aom charts/agent-ops -n $AOM_NS --create-namespace \
    -f charts/agent-ops/values-k3s.yaml -f aom-secrets.yaml \
    --set operationsManager.image.repository=couchbase-aom-operations-manager --set operationsManager.image.tag=$TAG \
    --set sampleMcpServers.image.repository=couchbase-aom-sample-mcp-servers --set sampleMcpServers.image.tag=$TAG \
    --set ui.image.repository=couchbase-aom-ui --set ui.image.tag=$TAG \
    --timeout 25m
  # No LoadBalancer implementation on this node: publish the dashboard on the node IP.
  kubectl -n $AOM_NS patch svc aom-ui --type merge -p '{\"spec\":{\"externalIPs\":[\"$NODE_IP\"]}}' || true
fi
if [ '$ONLY' != aom ]; then
  echo '--- pos-app (Couchbase ShopSmart demo) ---'
  helm upgrade --install pos-app charts/pos-app -n $SHOPSMART_NS --create-namespace \
    -f pos-values.yaml -f pos-secrets.yaml \
    --set backend.image.tag=$TAG --set web.image.tag=$TAG \
    --timeout 25m
fi
kubectl get pods,svc -n $AOM_NS 2>/dev/null || true
kubectl get pods,svc -n $SHOPSMART_NS 2>/dev/null || true
"

HOST="${MOTHER#*@}"; [ "$MOTHER" = local ] && HOST=$(remote "hostname -I | awk '{print \$1}'")
port() { python3 -c 'import re,sys; t=open(sys.argv[1]).read(); m=re.search(sys.argv[2]+r":\s*\n\s*service:.*?port:\s*(\d+)", t, re.S); print(m.group(1) if m else sys.argv[3])' "$VALUES" "$1" "$2"; }
WEB_PORT=$(port web 5154); API_PORT=$(port backend 8204); SG_PORT=$(port syncGateway 4992)
say "Done"
cat <<MSG
  AOM dashboard:        https://$HOST:3111   (admin / your AOM admin password)
  ShopSmart app:        https://$HOST:$WEB_PORT   (the story: https://$HOST:$WEB_PORT/story)
                        (sign in: demo / Couchbase123! unless web.auth is overridden; self-signed certificate)
  Voice ordering:       https://$HOST:$WEB_PORT/lane
  Self-checkout:        https://$HOST:$WEB_PORT/checkout
  Backend status:       http://$HOST:$API_PORT/api/status   (aom.registration should reach "registered")
  Couchbase Lite:       ws://$HOST:$SG_PORT/pos

First start takes several minutes (Couchbase init, the 100 MiB export, KB publishing). Watch with:
  ssh $MOTHER kubectl -n $SHOPSMART_NS logs deploy/backend -f
MSG
