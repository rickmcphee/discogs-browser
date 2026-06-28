FROM node:20-alpine AS builder

WORKDIR /app
COPY package*.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM nginx:alpine

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

ENV APP_VERSION="1.43"

EXPOSE 80

CMD sh -c 'echo "Discogs Browser frontend v${APP_VERSION} starting" && exec nginx -g "daemon off;"'
