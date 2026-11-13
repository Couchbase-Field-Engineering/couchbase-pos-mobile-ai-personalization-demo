FROM python:3.12-slim

# Corporate TLS-inspecting proxy support: scripts/setup-corporate-ca.sh exports
# the host's proxy root CA to certs/corporate-ca.crt (an empty placeholder
# otherwise) so pip can verify TLS through the proxy.
COPY certs/corporate-ca.crt /usr/local/share/ca-certificates/corporate-ca.crt
RUN set -eu; \
    if [ -s /usr/local/share/ca-certificates/corporate-ca.crt ]; then update-ca-certificates; \
    else rm -f /usr/local/share/ca-certificates/corporate-ca.crt; fi
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    PYTHONUNBUFFERED=1

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY tests ./tests

EXPOSE 8200
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8200"]
