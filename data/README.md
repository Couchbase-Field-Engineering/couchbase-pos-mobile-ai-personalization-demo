# Databricks daily export drop folder

`dt=YYYY-MM-DD/` partitions written by the Databricks job (or locally by
`databricks/generate_mock_dump.py`): JSON Lines `part-*.json` files, `_SUCCESS`,
and `_manifest.json`. The backend mounts this folder read-only at
`/data/databricks-dump` and loads the newest complete partition on startup and
whenever a newer one appears.
