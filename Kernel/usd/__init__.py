"""A statement written out as a tree of USD layers.

The statement is read in the ``usd`` basis (right-handed, Y up, metres, texture origin at the
lower left), so nothing here converts a coordinate: every number is written as it arrives.
What this package decides is how the statement is cut into layers and how the layers
refer to each other.

``package``    the cut: which nodes are reusable assets, which are scene, and the order they are
               written in
``scene``      the scene layer: the transform hierarchy, with a reference to an asset layer
               wherever the hierarchy draws something that was already written
``statics``    the layer of one mesh with the materials it is drawn with
``characters`` the layer of one rig: its skeleton, its skinned meshes, their blend shapes
``geometry``   a mesh, a skin and a blend shape as USD prims
``records``    a controller or any other record the source states about one asset, as the prims and ``ruri:`` attributes
               of one layer
``cinemachine`` the components of the camera package a scene's nodes carry, as records under those nodes of the scene layer
``audio``      an audio clip as one record layer and the Ogg Vorbis file beside it
``materials``  a material as a preview surface and the source's own properties, and its textures
``names``      legal prim and file names, unique where they are looked up
``layout``     where each kind of layer lives on disk and how one layer reaches another
``transforms`` a node's local transform as xform ops and as matrices

Only USD's core schemas are used (Xform, Mesh, GeomSubset, Material, Shader, Skeleton, SkelAnimation,
BlendShape, Scope); everything else the source says is a ``ruri:`` attribute of a basic type.

The USD bindings are imported by the modules that need them and by nothing above, so a host
without them loses this package and nothing else.
"""
