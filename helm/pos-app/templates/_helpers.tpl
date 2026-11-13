{{- define "pos.labels" -}}
app.kubernetes.io/part-of: pos-app
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "pos.selector" -}}
app.kubernetes.io/part-of: pos-app
app.kubernetes.io/component: {{ . }}
{{- end -}}

{{- define "pos.image" -}}
{{- $reg := .root.Values.global.imageRegistry -}}
{{- if $reg -}}{{ printf "%s/%s:%s" $reg .image.repository (toString .image.tag) }}{{- else -}}{{ printf "%s:%s" .image.repository (toString .image.tag) }}{{- end -}}
{{- end -}}

{{/* A secret value: the explicit value, else the one already stored, else random. */}}
{{- define "pos.secretValue" -}}
{{- $existing := lookup "v1" "Secret" .root.Release.Namespace "pos-app-secrets" -}}
{{- if .value -}}{{ .value }}
{{- else if and $existing (index $existing.data .key) -}}{{ index $existing.data .key | b64dec }}
{{- else -}}{{ randAlphaNum 24 }}{{- end -}}
{{- end -}}

{{/* Init container: wait until couchbase-init has created the bucket, collections and indexes. */}}
{{- define "pos.waitForCouchbase" -}}
- name: wait-for-couchbase
  image: {{ .Values.curlImage }}
  env:
    - name: CB_USERNAME
      valueFrom: { secretKeyRef: { name: pos-app-secrets, key: COUCHBASE_USERNAME } }
    - name: CB_PASSWORD
      valueFrom: { secretKeyRef: { name: pos-app-secrets, key: COUCHBASE_PASSWORD } }
  command:
    - sh
    - -c
    - |
      echo "Waiting for bucket {{ .Values.couchbase.bucket }} and its indexes ..."
      until curl -s -u "$CB_USERNAME:$CB_PASSWORD" http://couchbase:8093/query/service \
          --data-urlencode "statement=SELECT RAW COUNT(*) FROM system:indexes WHERE bucket_id='{{ .Values.couchbase.bucket }}' AND state='online'" \
          | tr -d ' \n' | grep -Eq '"results":\[(1[2-9]|[2-9][0-9])\]'; do sleep 5; done
      echo "Couchbase is ready"
{{- end -}}
