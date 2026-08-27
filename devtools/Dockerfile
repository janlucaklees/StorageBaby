FROM node:22-alpine

WORKDIR /opt/devtools
COPY package.json package-lock.json ./
RUN npm ci

ENV PATH="/opt/devtools/node_modules/.bin:${PATH}"

WORKDIR /repo
