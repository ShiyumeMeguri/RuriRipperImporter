"""Process-only bounded Qt icons; encoded raster bytes are not retained."""
from PySide6 import QtCore, QtGui

_icons = {}
MAX_PREVIEWS = 20
MAX_BYTES = 2 * 1024 * 1024


def publish(key, encoded):
    if not encoded or len(encoded) > MAX_BYTES:
        return False
    if key not in _icons and len(_icons) >= MAX_PREVIEWS:
        return False
    buffer = QtCore.QBuffer()
    buffer.setData(encoded)
    buffer.open(QtCore.QIODevice.ReadOnly)
    reader = QtGui.QImageReader(buffer)
    size = reader.size()
    if not size.isValid() or size.width() * size.height() > 128 * 1024 * 1024:
        return False
    reader.setScaledSize(size.scaled(256, 256, QtCore.Qt.KeepAspectRatio))
    image = reader.read()
    if image.isNull():
        return False
    image = image.scaled(256, 256, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
    _icons[key] = QtGui.QIcon(QtGui.QPixmap.fromImage(image))
    return True


def release(keys):
    for key in keys:
        _icons.pop(key, None)


def icon(key):
    return _icons.get(key)
