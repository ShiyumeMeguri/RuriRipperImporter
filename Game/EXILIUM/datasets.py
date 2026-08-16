"""What this game publishes, and nothing more.

Every row below is computed on the hook side and arrives columnar, already
searchable under its own handle. Nothing here parses a byte of the game: this
title states its models by name in protobuf config tables, hides every address
behind a hash of the asset path, and packs most of its archives inside other
archives -- three facts that live in ``Ruri.RipperHook.EXILIUM`` and nowhere else.

Arguments go by NAME and none of them says where the game is installed: the
session is opened on an install and the hook declares which folders under it hold
content, so a caller states WHAT it wants and never WHERE.
"""

from __future__ import annotations

from ...RuriRipperPyBridge.session import cabmap_state

LANGUAGE = "exilium.roster.language"
CAST = "exilium.roster.cast"
SCENES = "exilium.scene.list"
SELECT = "exilium.asset.select"
ROLE_MESHES = "exilium.model.meshes"
CATALOGS = "exilium.catalog.catalogs"
ARCHIVES = "exilium.vfs.archives"

# The two casts the game publishes. A panel states WHICH cast it wants, never how
# one is read.
CHARACTERS = "characters"
MODELS = "models"


def _table(dataset_id, **args):
    return cabmap_state.BRIDGE.game_data(dataset_id, **args)


def _rows(dataset_id, **args):
    table = _table(dataset_id, **args)
    return [{name: table.cell(index, name) for name in table.names}
            for index in range(len(table))]


def language_for_locale(locale):
    """The text package a host locale reads through. Which packages exist, and
    which locale lands on which, are the game's own facts."""
    rows = _rows(LANGUAGE, locale=str(locale or ""))
    return rows[0]["language"] if rows else ""


def cast(kind, language):
    """One cast as the table itself, so the list searches through the same C#
    engine every other list here does.

    ``characters`` are the units the game lets you field, under the name its own
    text package gives them; ``models`` is every model the config declares, each
    carrying the address the catalog knows it by and whether the loaded map holds
    it."""
    return _table(CAST, cast=kind, language=language)


def scenes():
    """Every scene the game ships, under the path its own catalog states."""
    return _table(SCENES)


def role_meshes(asset_text):
    """The meshes one character prefab wears, which its renderers do not carry:
    (transform, path, name, lod, container, cab), already resolved against the
    loaded map. Empty for a prefab that keeps no such list."""
    return _rows(ROLE_MESHES, assetText=str(asset_text or ""))


def cabs_for(addresses):
    """The loaded map's rows for a batch of addresses. A row with an empty cab
    means the catalog knows the address but this install carries nothing for it --
    which is the difference between "the game has no such thing" and "you have not
    downloaded it", and the panels say which."""
    addresses = [str(address) for address in addresses if address]
    if not addresses:
        return []
    return _rows(SELECT, address=addresses)
