# Frontend panelu: Next.js w trybie standalone.
#
# Budowanie w osobnym etapie, zeby obraz koncowy nie niosl node_modules ani
# zrodel - na serwerze z 3,8 GB RAM kazdy zaoszczedzony megabajt pamieci
# przydaje sie procesowi, a nie warstwom obrazu.
FROM node:24-slim AS deps
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci

FROM node:24-slim AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY frontend/ ./

# Adres API musi byc znany w chwili budowania: Next wkompilowuje zmienne
# NEXT_PUBLIC_* w kod wysylany do przegladarki. Zmiana adresu = przebudowa.
# Pusty NEXT_PUBLIC_API_URL = przegladarka wola /api pod wlasnym originem,
# a serwer Next przekazuje to dalej na API_INTERNAL_URL (rewrite w
# next.config.ts). Dzieki temu ciasteczko sesji jest zawsze same-site.
ARG NEXT_PUBLIC_API_URL=
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ARG API_INTERNAL_URL=http://api:8000
ENV API_INTERNAL_URL=$API_INTERNAL_URL
ENV NEXT_TELEMETRY_DISABLED=1

RUN npm run build

FROM node:24-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000

RUN useradd --create-home --uid 10001 panel
COPY --from=build --chown=panel:panel /app/.next/standalone ./
COPY --from=build --chown=panel:panel /app/.next/static ./.next/static
COPY --from=build --chown=panel:panel /app/public ./public

USER panel
EXPOSE 3000
CMD ["node", "server.js"]
