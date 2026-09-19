# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Goran Ristic and contributors
"""Local album artwork, including albums not yet in the library index."""
import base64
import os

PREFIX = "liner-local:"
NAMES = ("cover.jpg", "cover.jpeg", "cover.png", "folder.jpg", "folder.png")

def inside(root, path):
    root, path = os.path.realpath(root), os.path.realpath(path)
    return path.startswith(root + os.sep)

def reference(root, track):
    if not track or not inside(root, track) or not os.path.isfile(track):
        return None
    directory = os.path.dirname(track)
    for name in NAMES:
        path = os.path.join(directory, name)
        if inside(root, path) and os.path.isfile(path):
            relative = os.path.relpath(path, root)
            return PREFIX + base64.urlsafe_b64encode(relative.encode()).decode()
    return None

def resolve(root, value):
    if not value.startswith(PREFIX):
        return None
    try:
        relative = base64.b64decode(value[len(PREFIX):], altchars=b"-_", validate=True).decode()
        if os.path.isabs(relative):
            return None
        path = os.path.join(root, relative)
        if os.path.basename(path) not in NAMES or not inside(root, path) or not os.path.isfile(path):
            return None
        return path
    except (ValueError, UnicodeError, OSError):
        return None
