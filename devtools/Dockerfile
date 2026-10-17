# Formatting only: a host repository has nothing to run but prettier. Cradle carries the
# Ansible, Molecule and pytest tooling.
FROM docker.io/library/node:22-alpine
WORKDIR /opt/devtools
COPY package.json package-lock.json ./
RUN npm ci
ENV PATH="/opt/devtools/node_modules/.bin:${PATH}"
WORKDIR /repo
