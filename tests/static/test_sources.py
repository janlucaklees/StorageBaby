"""Pointer folders: a service whose definition lives in its own repository.

`hosts/<host>/services/<name>/source.yml` names a repository, a branch and a folder in
it; the host fetches that repository on every deploy tick and the folder is the service
contract. Nothing of the service is in this repository, and nothing about the *contract*
can be checked here for a private repository -- the fixture under tests/fixtures/sources
is what runs the external path through every other static test, and the host asserts the
rest at converge time.
"""

import re

import pytest

from conftest import FIXTURE_SOURCES, pointers

SOURCE_KEYS = {"repo", "ref", "path"}
# What a pointer folder may hold: the pointer and a note. Nothing else belongs beside it --
# a `quadlet/` here would be a service half in this repository and half in its own, and a
# credential here would be the host's business in the service's folder (the token is in
# hosts/<host>/secrets/sources.sops.yaml).
POINTER_FILES = {"source.yml", "README.md"}
NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
# https anywhere; plain http only to the loopback, which is the throwaway git server the
# test VM runs. A token over plain http to anything else would be a token on the wire.
REPO_RE = re.compile(r"^(https://\S+|http://127\.0\.0\.1(:\d+)?/\S+)$")
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
PATH_RE = re.compile(r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$")

POINTERS = pointers()
ids = [f"{owner}/{name}" for owner, name, _, _ in POINTERS]


@pytest.mark.parametrize("owner,name,folder,source", POINTERS, ids=ids)
def test_pointer_contract(owner, name, folder, source):
    assert set(source) == SOURCE_KEYS, f"{name}: source.yml must have exactly {sorted(SOURCE_KEYS)}, has {sorted(source)}"
    assert NAME_RE.match(name), f"{name}: not a valid service name"
    assert REPO_RE.match(source["repo"]), f"{name}: repo must be https://, or http://127.0.0.1 for a test host"
    assert REF_RE.match(source["ref"]), f"{name}: ref {source['ref']!r} is not a branch name"
    assert PATH_RE.match(source["path"]), f"{name}: path {source['path']!r} must be a relative folder"
    assert not (folder / "service.yml").exists(), f"{name}: a folder is a pointer or a service, never both"
    extra = {p.name for p in folder.iterdir()} - POINTER_FILES
    assert not extra, f"{name}: a pointer folder holds only {sorted(POINTER_FILES)}, found {sorted(extra)}"


def test_a_pointer_on_a_test_host_has_its_repository_in_the_fixture_tree():
    """What a test host points at exists under tests/fixtures/sources, so the static suite
    renders it and Molecule converges it; a pointer with no tree would be a placement no
    test ever sees."""
    for owner, name, _, source in POINTERS:
        if owner.startswith("test-"):
            folder = FIXTURE_SOURCES / name / source["path"]
            assert (folder / "service.yml").exists(), f"{owner}/{name}: no fixture at {folder}"


def test_test_host_copies_of_a_pointer_are_identical():
    """Like the other fixtures: a real folder per test host, byte-identical `source.yml`."""
    by_name: dict[str, set[bytes]] = {}
    for owner, name, folder, _ in POINTERS:
        if owner.startswith("test-"):
            by_name.setdefault(name, set()).add((folder / "source.yml").read_bytes())
    for name, texts in by_name.items():
        assert len(texts) == 1, f"{name}: the test hosts' source.yml files differ"
