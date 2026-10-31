#!/bin/sh
# The CloudRun platform terminates outbound TLS for api.weixin.qq.com with a
# certificate it mounts at /app/cert/certificate.crt. Trust that certificate in
# addition to the public CA bundle; TLS verification stays enabled.
set -e
if [ -r /app/cert/certificate.crt ]; then
  if cat /etc/ssl/certs/ca-certificates.crt /app/cert/certificate.crt > /tmp/ca-bundle.crt 2>/dev/null; then
    SSL_CERT_FILE=/tmp/ca-bundle.crt
    REQUESTS_CA_BUNDLE=/tmp/ca-bundle.crt
    export SSL_CERT_FILE REQUESTS_CA_BUNDLE
  fi
fi
exec python -m backend.server --host 0.0.0.0 --port 8000 --db /data/rates.sqlite3
