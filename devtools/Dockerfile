FROM docker.io/library/archlinux:base

RUN pacman -Syu --noconfirm --needed \
	python python-pip nodejs npm git openssh \
	podman sops age \
	&& pacman -Scc --noconfirm

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:/opt/devtools/node_modules/.bin:${PATH}"

WORKDIR /opt/devtools
COPY requirements.txt requirements.yml ./
RUN pip install --no-cache-dir -r requirements.txt \
	&& ansible-galaxy collection install -r requirements.yml

COPY package.json package-lock.json ./
RUN npm ci

WORKDIR /repo
