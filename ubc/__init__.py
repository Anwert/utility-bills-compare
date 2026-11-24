"""Contracts for the utility-bills comparison pipeline.

Import paths that later tickets should keep stable:

- ``ubc.schemas`` — validated records, city config, reference apartment, site JSON
- ``ubc.adapter`` — adapter interface and registry
- ``ubc.fetch`` — content layer (network or saved fixtures)
- ``ubc.load`` — read the JSON files in ``data/``
"""

__version__ = "0.1.0"
