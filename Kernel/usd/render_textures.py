"""A render texture asset as one record layer.

The statement hands a render texture over as the descriptor the source serializes. The layer is a record of those facts
(``records``: ``ruri:`` attributes of basic types on one prim); a camera or an image that targets the texture states the
texture's key, and the reader of the layer builds the texture from the descriptor.
"""

from __future__ import annotations

from . import records


def record_of(texture):
    """What the layer of ``texture`` (``statement.RenderTexture``) states, as the record ``records`` writes."""
    return {
        "sourceKey": texture.key,
        "sourceName": texture.name,
        "width": texture.width,
        "height": texture.height,
        "volumeDepth": texture.volume_depth,
        "dimension": texture.dimension,
        "antiAliasing": texture.anti_aliasing,
        "mipCount": texture.mip_count,
        "colorFormat": texture.color_format,
        "depthStencilFormat": texture.depth_stencil_format,
        "shadowSamplingMode": texture.shadow_sampling_mode,
        "mipMap": texture.mip_map,
        "generateMips": texture.generate_mips,
        "srgb": texture.srgb,
        "useDynamicScale": texture.dynamic_scale,
        "bindMultisampled": texture.bind_ms,
        "filterMode": texture.filter_mode,
        "aniso": texture.aniso,
        "mipBias": texture.mip_bias,
        "wrapU": texture.wrap_u,
        "wrapV": texture.wrap_v,
        "wrapW": texture.wrap_w,
    }


def author(layer_path, texture):
    """Write ``texture`` (``statement.RenderTexture``) as the layer at ``layer_path``. Returns the counts of what the layer holds."""
    return records.author(layer_path, record_of(texture), "Render texture {0}".format(texture.name))
