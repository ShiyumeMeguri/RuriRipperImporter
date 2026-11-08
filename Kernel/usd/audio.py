"""An audio clip as one layer and one Ogg Vorbis file.

The statement hands a clip over as a standard Ogg Vorbis file and the facts its sound bank states about it. The layer is a
record of those facts (``records``: ``ruri:`` attributes of basic types on one prim, ``ruri:audioFile`` naming the file); the
file sits in the layer's folder under the layer's own name, and it is the one file both formats of the layer refer to.
"""

from __future__ import annotations

import os

from . import records

#: The extension of the file a clip's layer refers to.
FILE_EXTENSION = ".ogg"


def file_name(layer_path):
    """The name of the Ogg file of the layer at ``layer_path``: the layer's own name, without its format."""
    return os.path.splitext(os.path.basename(layer_path))[0] + FILE_EXTENSION


def record_of(clip, file):
    """What the layer of ``clip`` states, as the record ``records`` writes; ``file`` is the name of its Ogg file."""
    return {
        "sourceKey": clip.key,
        "sourceName": clip.name,
        "audioChannels": clip.channels,
        "audioFrequency": clip.frequency,
        "audioSamples": clip.samples,
        "audioDuration": clip.duration,
        "audioSerializedLength": clip.serialized_length,
        "audioFormat": clip.format,
        "audioFile": file,
    }


def author(layer_path, clip, ogg):
    """Write ``clip`` (``statement.AudioClip``) as the layer at ``layer_path``, and its Ogg file ``ogg`` beside it. The
    file is the bytes the statement hands over, and must be as long as the statement said it would be. Returns the counts of
    what the layer holds, the name of the file and its size."""
    if len(ogg) != clip.bytes:
        raise ValueError("audio clip {0} was stated as {1} bytes of Ogg and arrived as {2}.".format(clip.key, clip.bytes, len(ogg)))
    name = file_name(layer_path)
    os.makedirs(os.path.dirname(layer_path), exist_ok=True)
    with open(os.path.join(os.path.dirname(layer_path), name), "wb") as handle:
        handle.write(ogg)
    counts = records.author(layer_path, record_of(clip, name), "Audio clip {0}".format(clip.name))
    return dict(counts, file=name, bytes=len(ogg))
