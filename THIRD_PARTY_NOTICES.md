# Third-Party Notices

BAM's own code is MIT-licensed (see `LICENSE`). This file inventories the
third-party components BAM depends on, with licenses verified from the
installed distributions.

## Python dependencies

| Package | Version | License | Source | Role |
|---|---|---|---|---|
| httpx | 0.28.1 | BSD-3-Clause | https://github.com/encode/httpx | HTTP client (the only networking dependency) |
| PyYAML | 6.0.3 | MIT | https://pyyaml.org/ | YAML configuration parsing |
| pytest | 8.4.2 | MIT | https://pytest.org | Dev/test framework only |

All three are permissive licenses compatible with MIT. No copyleft
dependencies are used at runtime.

### BSD-3-Clause (httpx) — required notice

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the above copyright notice and this
permission notice appear in all copies. See the httpx repository for the full
license text and copyright holders.

## Standard library

BAM uses the Python 3.14 standard library (sqlite3, html.parser,
xml.etree.ElementTree, subprocess, ipaddress, urllib, webbrowser, argparse,
json, re, hashlib, datetime, tempfile, shutil, pathlib, contextlib, argparse).
Python itself is licensed under the
[PSF License](https://docs.python.org/3/license.html).

## Packaged EXE builds

The Windows bundle is produced with
[PyInstaller](https://pyinstaller.org) (GPL with a special exception that
permits distributing the produced bundles under any license of the packaged
application). PyInstaller is used as a build tool only; it is not a dependency
of BAM at runtime.

## What MIT on this repository does NOT cover

- The separately maintained external service projects BAM drives through
  subprocess adapters (PDF→Excel, Excel Cleaner, QA Agent) — each keeps its
  own license and ownership.
- External websites, their content, and data you collect with BAM.
- Google News RSS and whois.com — Google/whois.com's respective terms apply
  to those sources.
- Trademarks, logos, personal data, and generated outputs.
