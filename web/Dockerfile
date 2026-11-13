# Browser build of the Expo app (the "preview" engine: same documents as
# Couchbase Lite, synced over the backend's REST /api/sync). Prebuilt into
# dist.tgz by `npm run export:web:docker` in ../mobile so this image needs no
# Node toolchain. ADD unpacks the archive.
FROM nginx:1.27.3-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
ADD dist.tgz /usr/share/nginx/html/
# The story that opens the demo: https://<host>:5154/story
COPY story.html /usr/share/nginx/html/story.html
# The merchandising console: https://<host>:5154/merch
COPY merch.html /usr/share/nginx/html/merch.html
# Voice ordering (drive-thru lane, phone line): https://<host>:5154/lane
COPY lane.html /usr/share/nginx/html/lane.html
# Self-checkout (vision agent, attendant): https://<host>:5154/checkout
COPY checkout.html /usr/share/nginx/html/checkout.html
# Files copied from a checkout may be mode 600; nginx workers run as the nginx user.
RUN chmod -R a+rX /usr/share/nginx/html
# HTTPS and the sign-in page in front of everything above (see nginx.conf):
# demo-gate.sh runs before nginx starts, creates a self-signed certificate if
# none is mounted at /etc/nginx/tls, and derives the sign-in check from
# DEMO_USERNAME / DEMO_PASSWORD.
RUN command -v openssl >/dev/null || apk add --no-cache openssl
COPY signin.html /usr/share/nginx/html/signin.html
COPY demo-gate.sh /docker-entrypoint.d/15-demo-gate.sh
RUN chmod 644 /usr/share/nginx/html/signin.html && chmod 755 /docker-entrypoint.d/15-demo-gate.sh
ENV DEMO_USERNAME=demo \
    DEMO_PASSWORD=Couchbase123!
EXPOSE 443
