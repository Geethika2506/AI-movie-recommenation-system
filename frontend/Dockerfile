FROM node:22-alpine AS build

WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./

ARG VITE_API_URL=http://localhost:8000/api
ENV VITE_API_URL=${VITE_API_URL}

RUN npm run build

FROM nginx:1.29-alpine

COPY --from=build /app/frontend/dist /usr/share/nginx/html

RUN printf '%s\n' \
    'server {' \
    '  listen 80;' \
    '  server_name _;' \
    '  root /usr/share/nginx/html;' \
    '  index index.html;' \
    '  location / {' \
    '    try_files $uri $uri/ /index.html;' \
    '  }' \
    '}' \
    > /etc/nginx/conf.d/default.conf

EXPOSE 80
