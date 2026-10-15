FROM node:24-bookworm-slim AS build
WORKDIR /app
RUN npm install --global pnpm@11.1.2
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
# nginx가 같은 origin의 /api를 전달한다.
ENV VITE_API_BASE_URL=""
RUN pnpm build

FROM nginx:1.28-alpine
COPY infra/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
