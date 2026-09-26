# Pinned so a rebuild is reproducible; bump deliberately.
FROM docker.io/library/archlinux:base-20260920.0.596911

RUN pacman -Syu --noconfirm --needed \
	python python-pip nodejs npm git openssh \
	podman sops age \
	libvirt libvirt-python python-lxml qemu-img cdrtools \
	gcc make curl \
	&& pacman -Scc --noconfirm

# snapraid, for `tests/static/test_storage.py`: it runs a real `snapraid sync` and
# `snapraid status` against the rendered `snapraid.conf` and a stub tree, which is the
# only way to say the file *parses* rather than merely looks right.
#
# Built from upstream's source tarball and not from a package: snapraid is in neither
# Arch's official repositories nor Chaotic-AUR, upstream ships no Arch package, and
# `makepkg` would mean `base-devel` and a build user inside a tooling image. The URL and
# the sha256 are the ones carried by the AUR recipe this repo pins in
# `ansible/roles/storage/defaults/main.yml` (`snapraid_aur_commit`), so the image and a
# host build the same release -- bump the two together. `sha256sum -c` is what makes the
# pin a pin: a changed tarball fails the build instead of quietly testing something else.
#
# Every step is joined with `&&` and not with `;`: prettier formats this file as shell,
# and it rewrites a `; \` continuation into separate lines -- which in a Dockerfile turns
# one RUN into one RUN plus a dozen invalid instructions.
ARG SNAPRAID_VERSION=14.9
ARG SNAPRAID_SHA256=40c216979d9d9853248060497341f74feaa07c8ae15927b6b14972c4f9d143d5
RUN curl -fsSLo /tmp/snapraid.tar.gz \
	"https://github.com/amadvance/snapraid/releases/download/v${SNAPRAID_VERSION}/snapraid-${SNAPRAID_VERSION}.tar.gz" \
	&& echo "${SNAPRAID_SHA256}  /tmp/snapraid.tar.gz" | sha256sum -c - \
	&& tar -C /tmp -xzf /tmp/snapraid.tar.gz \
	&& cd "/tmp/snapraid-${SNAPRAID_VERSION}" \
	&& ./configure --prefix=/usr \
	&& make -j"$(nproc)" \
	&& make install \
	&& cd / \
	&& rm -rf /tmp/snapraid.tar.gz "/tmp/snapraid-${SNAPRAID_VERSION}" \
	&& snapraid --version

RUN python -m venv --system-site-packages /opt/venv
ENV PATH="/opt/venv/bin:/opt/devtools/node_modules/.bin:${PATH}"

WORKDIR /opt/devtools
COPY requirements.txt requirements.yml ./
RUN pip install --no-cache-dir -r requirements.txt \
	&& ansible-galaxy collection install -r requirements.yml

COPY package.json package-lock.json ./
RUN npm ci

WORKDIR /repo
