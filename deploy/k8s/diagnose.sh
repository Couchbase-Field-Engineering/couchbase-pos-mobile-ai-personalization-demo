#!/usr/bin/env bash
# Read-only health check of the AOM (agent-ops) and demo (pos-app) releases on
# the K3s node. Changes nothing. Run it from your Mac and keep the report:
#
#   ssh ehind@192.168.111.4 'bash -s' < deploy/k8s/diagnose.sh > deploy/k8s/diagnose-report.txt 2>&1
#
# or on the node itself:  bash deploy/k8s/diagnose.sh
export PATH="$HOME/.local/bin:$PATH"
if [ ! -f "$HOME/.kube/config" ] && [ -z "${KUBECONFIG:-}" ] && [ -r /etc/rancher/k3s/k3s.yaml ]; then export KUBECONFIG=/etc/rancher/k3s/k3s.yaml; fi
A=agent-ops; R=pos-app
h() { printf '\n===== %s =====\n' "$*"; }

h "date / node"
date -u; uptime; kubectl get nodes -o wide; kubectl top nodes 2>&1 | head -5; free -h 2>/dev/null; df -h / 2>/dev/null | tail -1

h "pods (restarts, status)"
kubectl get pods -n $A -o wide; kubectl get pods -n $R -o wide
kubectl top pods -n $A 2>&1; kubectl top pods -n $R 2>&1

h "last terminations (OOMKilled? Error?)"
for ns in $A $R; do
  kubectl get pods -n $ns -o jsonpath='{range .items[*]}{.metadata.name}{"  "}{range .status.containerStatuses[*]}{.name}={.restartCount} last:{.lastState.terminated.reason}@{.lastState.terminated.finishedAt} {end}{"\n"}{end}'
done

h "services"
kubectl get svc -n $A; kubectl get svc -n $R

h "helm releases"
helm list -A 2>&1 | grep -E "NAME|aom|pos" || true

h "recent warning events"
kubectl get events -n $A --field-selector type=Warning --sort-by=.lastTimestamp 2>&1 | tail -15
kubectl get events -n $R --field-selector type=Warning --sort-by=.lastTimestamp 2>&1 | tail -15

h "backend /api/status (aom section)"
kubectl -n $R exec deploy/backend -c backend -- python -c "
import json, urllib.request
d = json.load(urllib.request.urlopen('http://localhost:8200/api/status', timeout=15))
print(json.dumps({'load_state': d.get('load', {}).get('state'), 'export_date': d.get('load', {}).get('export_date'),
                  'kb': d.get('load', {}).get('kb'), 'aom': {k: v for k, v in d.get('aom', {}).items()}}, indent=2, default=str)[:4000])
" 2>&1

h "backend -> AOM (in-cluster)"
kubectl -n $R exec deploy/backend -c backend -- python -c "
import httpx, os
u = os.environ.get('AOM_URL')
print('AOM_URL =', u)
for p in ('/api/health', '/v1/agent/approvals/x'):
    try:
        r = httpx.get(u + p, verify=False, timeout=10, headers={'Authorization': 'Bearer ' + (os.environ.get('AOM_AGENT_API_KEY') or 'none')})
        print(p, r.status_code, r.text[:300])
    except Exception as e:
        print(p, 'ERROR', type(e).__name__, e)
" 2>&1

h "AOM -> backend MCP (in-cluster)"
OM=$(kubectl -n $A get pod -l app.kubernetes.io/component=operations-manager -o name 2>/dev/null | head -1)
[ -z "$OM" ] && OM=$(kubectl -n $A get pods -o name | grep operations-manager | head -1)
echo "operations-manager pod: $OM"
kubectl -n $A exec "$OM" -- python -c "
import urllib.request
for u in ('http://backend.pos-app.svc.cluster.local:8200/api/health',):
    try:
        print(u, urllib.request.urlopen(u, timeout=10).status)
    except Exception as e:
        print(u, 'ERROR', type(e).__name__, e)
" 2>&1

h "backend log: AOM-related lines (last 400 lines)"
kubectl -n $R logs deploy/backend -c backend --tail=400 2>&1 | grep -iE "aom|registr|knowledge|rag app|error|traceback|exception|warn" | grep -v "GET /api/health" | tail -40
h "backend log: previous container (if it restarted)"
kubectl -n $R logs deploy/backend -c backend --previous --tail=60 2>&1 | tail -30

h "operations-manager log (errors, last 400 lines)"
kubectl -n $A logs "$OM" --tail=400 2>&1 | grep -iE "error|traceback|exception|warn|couchbase|timeout" | tail -40
h "operations-manager log: previous container"
kubectl -n $A logs "$OM" --previous --tail=40 2>&1 | tail -25

h "AOM couchbase"
CB=$(kubectl -n $A get pods -o name | grep couchbase | grep -v init | head -1)
echo "$CB"; kubectl -n $A logs "$CB" --tail=20 2>&1 | tail -10

h "pos-app couchbase / sync-gateway / web / daily export"
kubectl -n $R logs statefulset/couchbase --tail=10 2>&1 | tail -5
kubectl -n $R logs deploy/sync-gateway --tail=10 2>&1 | tail -5
kubectl -n $R get cronjob,jobs 2>&1
echo; echo "diagnose.sh finished"
