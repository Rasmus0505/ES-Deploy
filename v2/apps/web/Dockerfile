FROM node:20-alpine AS builder
WORKDIR /app
COPY v2/apps/web/package*.json ./
RUN npm install
COPY v2/apps/web ./
RUN npm run build

FROM node:20-alpine
WORKDIR /app
ENV NODE_ENV=production
COPY --from=builder /app .
EXPOSE 8080
CMD ["sh", "-c", "next start -p ${PORT:-8080}"]
