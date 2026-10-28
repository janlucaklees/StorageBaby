#!/usr/bin/env python3
"""imgcfg.py <ref:tag> [...]  -> User/Entrypoint/Cmd/Ports/selected Env, from the registry."""
import json, subprocess, sys, urllib.parse

ACC = ",".join([
 "application/vnd.oci.image.index.v1+json",
 "application/vnd.docker.distribution.manifest.list.v2+json",
 "application/vnd.oci.image.manifest.v1+json",
 "application/vnd.docker.distribution.manifest.v2+json",
])

def curl(url, tok=None, accept=None):
    cmd = ["curl", "-sL"]
    if tok: cmd += ["-H", f"Authorization: Bearer {tok}"]
    if accept: cmd += ["-H", f"Accept: {accept}"]
    cmd.append(url)
    return subprocess.run(cmd, capture_output=True, text=True).stdout

def split(ref):
    host, _, rest = ref.partition("/")
    if "." not in host:            # no registry given
        host, rest = "docker.io", ref
    repo, _, tag = rest.rpartition(":")
    if "/" not in repo and host == "docker.io":
        repo = "library/" + repo
    areg = "registry-1.docker.io" if host == "docker.io" else host
    return areg, host, repo, tag

def token(areg, host, repo):
    if host == "docker.io":
        u = f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repo}:pull"
    else:
        u = f"https://{host}/token?service={host}&scope=repository:{urllib.parse.quote(repo)}:pull"
    try:    return json.loads(curl(u)).get("token")
    except Exception: return None

def cfg(ref):
    areg, host, repo, tag = split(ref)
    tok = token(areg, host, repo)
    man = json.loads(curl(f"https://{areg}/v2/{repo}/manifests/{tag}", tok, ACC))
    if "manifests" in man:
        kids = man["manifests"]
        pick = next((m for m in kids if (m.get("platform") or {}).get("architecture") == "amd64"
                     and (m.get("platform") or {}).get("os") == "linux"), kids[0])
        man = json.loads(curl(f"https://{areg}/v2/{repo}/manifests/{pick['digest']}", tok, ACC))
    blob = json.loads(curl(f"https://{areg}/v2/{repo}/blobs/{man['config']['digest']}", tok))
    return blob.get("config", {}) or {}

for ref in sys.argv[1:]:
    print(f"##### {ref}")
    try:
        c = cfg(ref)
        print("  User:      ", repr(c.get("User")))
        print("  Entrypoint:", c.get("Entrypoint"))
        print("  Cmd:       ", c.get("Cmd"))
        print("  Ports:     ", sorted((c.get("ExposedPorts") or {}).keys()))
        # Env is filtered to the keys that have mattered so far -- add yours here when a
        # service's version lives in its environment, or this prints no Env at all.
        for e in (c.get("Env") or []):
            if any(k in e.upper() for k in ("PHP_VERSION", "NEXTCLOUD_VERSION", "NODE_VERSION", "APP_VERSION")):
                print("  Env:       ", e)
    except Exception as ex:
        print("  ERROR:", type(ex).__name__, ex)
