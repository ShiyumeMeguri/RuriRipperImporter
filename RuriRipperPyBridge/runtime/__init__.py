"""Process plumbing: dependency bootstrap, CoreCLR/pythonnet bridge, settings.

Imports nothing eagerly on purpose: ``bootstrap`` must be importable in a host
whose interpreter has no numpy (and no pythonnet) yet, since installing those is
exactly what it does.
"""
