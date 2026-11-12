# Studio ↔ comfy-mac contracts (confer a4c0d508)

The table lives with the Mac workers: `~/code/comfy-mac/docs/studio/contracts.md` (10 rows, file:line on both sides),
checked from both repos' source by comfy-mac's `test_contracts.py`. This page is Studio's column and the run that proves it.

| Contract | Studio side | Studio proof |
|---|---|---|
| `/mac/fetch` statuses + keys | `remote.py` `_fetch`, `_pushable`, `_show_fetch`, `_fetch_then_send` | `test_fit_mac` C3 (every status literal in mac_fetch.py), `test_entrypoints` FETCH=1 (11 scenarios incl. push-shared 423) |
| `HEAD/PUT /mac/models` | `remote.py` `_push_file` (201 done, 423 wait, 202/416 re-HEAD, stall → fail) | `test_fit_mac` R1, `test_entrypoints` push-*, live `test_push_live`, `test_ph3_live` |
| `/mac/info` | `remote.py` `caps`, `pick`; `dispatch.py` (queue_remaining); `web/app.js` `cardMem`, te_for | `test_pair`, `test_pair_live` |
| Model share | `modelshare.py` `_send_file`, `_allowed_ips`, 403 | `test_model_share`, `test_share_big`, `test_share_resolve`, `test_fit_mac` C5/C12 |
| Workers | `remote.py` `load_workers` | comfy-mac `test_contracts` 7 |
| Gate / respell | `remote.py` `gate`, `_spelled`, `respell` | `test_mac_choices` (261 rows) |
| `/mac/te` | never called; only te_for is read | `test_fit_pair` W7 |
| Job lifecycle, put-back, avoid | `remote.py` history/cancel/forget; `__init__.py` `_put_back`; `dispatch.py` `unfit` (no size rule) | `test_fit_pair` W8, `test_fit_return`, `test_dispatch_fit` |
| Folders | the answer's `folder` + `LOCAL_MODEL` = `modelshare.local_model` | `test_fit_return` |

Run of 2026-11-12 (studio main f5811a4 + this page), `~/code/comfy-mac/.venv/bin/python tests/<name>.py`:
```
test_dispatch_fit: 3 PASS, 0 FAIL -> all passed
test_entrypoints: 103 PASS, 0 FAIL -> all passed
test_fit_mac: 66 PASS, 0 FAIL -> all passed
test_fit_pair: 6 PASS, 0 FAIL -> all passed
test_fit_return: 22 PASS, 0 FAIL -> all passed
test_mac_choices: 262 PASS, 0 FAIL -> all passed
test_marker: 7 PASS, 0 FAIL -> all passed
test_model_share: 30 PASS, 0 FAIL -> all passed
test_pair: 25 PASS, 0 FAIL -> all passed
test_pair_live: 5 PASS, 0 FAIL -> all passed
test_prune: 9 PASS, 0 FAIL -> 85 cases, 1702 switch nodes before pruning, 1 kept (None into a required input); all passed
test_remote: 0 PASS, 0 FAIL -> 
test_revise: 13 PASS, 0 FAIL -> all passed
test_share_big: 7 PASS, 0 FAIL -> all passed
test_share_resolve: 9 PASS, 0 FAIL -> all passed
test_entrypoints FETCH=1 LIVE=0: 1093 PASS, 0 FAIL -> all passed
```
Not run here: the live tests that render on the cards (`test_remote`, `test_run_route`, `test_revise`, `test_push_live`,
`test_ph3_live`, `test_fetch_live`) while comfy-mac's render matrix holds them. Not provable live yet: the PC side of the
model share and allow-list, until the PC runs studio ≥ 68b02d8.
