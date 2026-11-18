FROM node:20-alpine

WORKDIR /app

# NEXT_PUBLIC_* values are baked in at build time
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

COPY package*.json ./
RUN npm ci --no-audit --no-fund

COPY . .
RUN npm run build

# Run as the unprivileged "node" user (uid 1000); Next writes its cache under .next at runtime
RUN chown -R node:node /app
USER node

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1
EXPOSE 3000
CMD ["npm", "start"]
