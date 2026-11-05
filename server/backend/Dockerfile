FROM node:24-bookworm-slim AS build
WORKDIR /app
COPY backend/package.json backend/package-lock.json ./
RUN npm ci
COPY backend/tsconfig.json ./
COPY backend/src ./src
RUN npm run build && npm prune --omit=dev

FROM node:24-bookworm-slim
ENV NODE_ENV=production PORT=5001 USER_DATA_PATH=/app/data/users.json
WORKDIR /app
COPY --from=build /app/package.json ./
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/dist ./dist
# dist/ruleset/methodCatalog.js가 세 단계 위에서 읽는다(개발·빌드와 같은 상대 위치).
COPY contracts/method-catalog.json /contracts/method-catalog.json
RUN mkdir -p /app/data && chown node:node /app/data
USER node
EXPOSE 5001
CMD ["node", "dist/index.js"]
