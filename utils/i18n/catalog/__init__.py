"""Per-domain slices of the translation catalog.

Each module here exposes a ``CATALOG`` dict (key -> {lang -> template}); they are
merged into the single ``utils.i18n._CATALOG`` at package import. Split by area
purely for navigation — adding a new area's strings no longer means editing one
900-line file. See ``utils/i18n/__init__.py`` for the merge and the ``t`` lookup.
"""
