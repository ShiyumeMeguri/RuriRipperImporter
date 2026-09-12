"""Unity asset domain: parsing, resolution and decoding of Unity's own data.

Deliberately empty of imports -- every module here is imported by name
(``from ruri_pybridge.unity import mesh_decoder``) so that pulling in the YAML
parser never drags numpy, and pulling in one decoder never drags the rest.
"""
