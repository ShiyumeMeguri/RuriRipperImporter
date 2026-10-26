"""Fetching the RipperHook bin folder, so nobody downloads and unpacks it by hand.

The build workflow publishes the pure build as the asset of the rolling ``latest``
release; a workflow artifact would do the same job but GitHub serves artifacts only
to a signed-in account. Each build lands in a folder named after its own sha256, next
to the others under the workspace: a loaded CLR assembly cannot be replaced in place,
so an update is a NEW folder and the old one is dropped once nothing holds it.

One :class:`Job` is one check-and-install. It runs on a thread and only ever writes
its own fields; the host polls it from its main thread and applies the result, because
a host's settings are not safe to touch from here.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import urllib.request
import zipfile

from . import workspace

REPOSITORY = "FractalTools/Ruri.RipperHook"
RELEASE_TAG = "latest"
ASSET_NAME = "RipperHook-PureRelease.zip"
DLL_NAME = "Ruri.RipperHook.dll"
FOLDER_NAME_LENGTH = 16
CHUNK_BYTES = 1 << 20
TIMEOUT_SECONDS = 30

_RELEASE_URL = "https://api.github.com/repos/{0}/releases/tags/{1}".format(REPOSITORY, RELEASE_TAG)


def managed_root():
    return workspace.subdir("RipperHook", create=False)


def is_managed(path):
    if not path:
        return False
    root = os.path.normcase(os.path.abspath(managed_root()))
    return os.path.normcase(os.path.abspath(path)).startswith(root + os.sep)


def _request(url):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "RuriRipperImporter"}), timeout=TIMEOUT_SECONDS)


def latest():
    """The folder name and download URL of the newest published build."""
    with _request(_RELEASE_URL) as response:
        release = json.load(response)
    asset = next((a for a in release["assets"] if a["name"] == ASSET_NAME), None)
    if asset is None:
        raise RuntimeError("The '{0}' release of {1} has no {2}.".format(RELEASE_TAG, REPOSITORY, ASSET_NAME))
    algorithm, _, digest = asset["digest"].partition(":")
    if algorithm != "sha256":
        raise RuntimeError("Release asset digest is not sha256: {0}".format(asset["digest"]))
    return digest, asset["browser_download_url"]


def folder_for(digest):
    return os.path.join(managed_root(), digest[:FOLDER_NAME_LENGTH])


def _download(url, digest, target):
    hasher = hashlib.sha256()
    with _request(url) as response, open(target, "wb") as output:
        while True:
            chunk = response.read(CHUNK_BYTES)
            if not chunk:
                break
            hasher.update(chunk)
            output.write(chunk)
    if hasher.hexdigest() != digest:
        raise RuntimeError("The downloaded RipperHook archive does not match its published sha256.")


def _drop_others(keep, in_use):
    root = managed_root()
    if not os.path.isdir(root):
        return
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if path != keep and path != in_use:
            shutil.rmtree(path, ignore_errors=True)


class Job:
    """One check-and-install. Read ``message`` for what it is doing; ``finished``
    turns true last, after ``folder`` (the usable bin folder), ``updated`` (it
    fetched a build this machine did not have) or ``error`` is set."""

    def __init__(self, in_use):
        self.message = "Checking for a RipperHook update ..."
        self.finished = False
        self.error = ""
        self.folder = ""
        self.updated = False
        self._in_use = in_use
        threading.Thread(target=self._run, name="RuriRipperBinUpdate", daemon=True).start()

    def _run(self):
        try:
            digest, url = latest()
            folder = folder_for(digest)
            if not os.path.isfile(os.path.join(folder, DLL_NAME)):
                self._install(digest, url, folder)
                self.updated = True
            _drop_others(folder, self._in_use)
            self.folder = folder
            self.message = "RipperHook is up to date ({0}).".format(os.path.basename(folder))
        except Exception as exc:
            self.error = "{0}: {1}".format(type(exc).__name__, exc)
            self.message = "RipperHook update failed -- " + self.error
        finally:
            self.finished = True

    def _install(self, digest, url, folder):
        root = managed_root()
        os.makedirs(root, exist_ok=True)
        archive = folder + ".zip"
        staging = folder + ".partial"
        shutil.rmtree(staging, ignore_errors=True)
        self.message = "Downloading RipperHook ..."
        _download(url, digest, archive)
        self.message = "Unpacking RipperHook ..."
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(staging)
        os.remove(archive)
        if not os.path.isfile(os.path.join(staging, DLL_NAME)):
            raise RuntimeError("The RipperHook archive has no {0} at its top level.".format(DLL_NAME))
        shutil.rmtree(folder, ignore_errors=True)
        os.replace(staging, folder)
