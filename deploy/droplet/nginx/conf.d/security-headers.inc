# Included inside every HTTPS server block. nginx does NOT inherit add_header
# from the http block once a server block sets any add_header of its own
# (each vhost sets HSTS), so these must be repeated here. (5 Sep 2026)
add_header X-Frame-Options "SAMEORIGIN" always;
add_header X-Content-Type-Options "nosniff" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
