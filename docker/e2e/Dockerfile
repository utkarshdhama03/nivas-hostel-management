# End-to-end test runner (Q87): Node 24 + Playwright with ONLY Chromium (no Firefox/WebKit).
# Chromium and its system libraries are downloaded once at build time from Playwright's
# (Microsoft's) servers, as accepted under Q78. Must match @playwright/test in package.json.
FROM node:24.22.2-trixie-slim
RUN npx -y playwright@1.65.0 install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/* /root/.npm
WORKDIR /web
