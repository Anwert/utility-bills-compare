"""Contracts for the utility-bills comparison pipeline.

Import paths that later tickets should keep stable:

- ``ubc.schemas`` — validated records, city config, reference apartment, site JSON
- ``ubc.adapter`` — adapter interface and registry
- ``ubc.fetch`` — content layer (network or saved fixtures)
- ``ubc.load`` — read the JSON files in ``data/``
- ``ubc.calculate`` — monthly cell bill from normalized records
- ``ubc.policy`` — which source is published, the 30% hold, alert intents
- ``ubc.alerts`` — open and close ``data-alert`` GitHub issues
"""

__version__ = "0.1.0"
