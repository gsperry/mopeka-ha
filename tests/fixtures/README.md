# Test fixtures

API responses used by the tests. None of them contain real account data.

| File | Source |
|---|---|
| `retail_sync.json` | Recorded from a real Co-Pilot account's `PUT /api/devices/retail` response (November 2026). MAC addresses replaced; no other values changed. |
| `retail_sync_no_new_samples.json` | Recorded from the same account when no new readings had arrived since `lastUpdatedAt`. MAC addresses replaced. |
| `legacy_devices.json`, `legacy_data_shadow.json` | Synthetic. The shape follows the upstream `mopeka-api` client's parsing of the older Tank Check gateway; no real response was available to record. |

To record new fixtures from your own account, run
`uv run scripts/live_check.py --record tests/fixtures` and review the output before
committing it. The script replaces addresses, names, gateway IDs, and any fields it does
not recognize.
