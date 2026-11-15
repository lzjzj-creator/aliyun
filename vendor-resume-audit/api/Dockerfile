# 简历诊断服务（复用公开源码 resume-evidence-audit-api）— CloudBase 云托管用
FROM node:20-alpine

WORKDIR /app
ENV NODE_ENV=production

COPY package.json package-lock.json ./
RUN npm ci --omit=dev

COPY server.js model-adapter.js ./

ENV PORT=3000
EXPOSE 3000

CMD ["node", "server.js"]
