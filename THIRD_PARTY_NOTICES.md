# Third-party notices

Chronikwerk is distributed under the MIT License (`LICENSE`). The production image and a
default installation also contain the third-party Python packages below, unmodified, each
under its own licence. The table lists the licence each package declares in its metadata.

It covers exactly the runtime set pinned in `requirements/signing.lock`, which the production
image installs (`requirements/base.lock` is a subset). It was verified on 2026-11-01 from
those pinned versions with `importlib.metadata`. Regenerate it whenever the locks change.

Notes:

- `certifi` is MPL-2.0. Its unmodified source is available from the project URL below.
- `pyphen` is offered under GPL-2.0-or-later, LGPL-2.1-or-later or MPL-1.1, at the
  recipient's choice. Chronikwerk uses it unmodified as a separate library.
- The production image also contains Debian base-image packages, among them
  `fonts-dejavu-core`. Their licences ship inside the image under `/usr/share/doc/*/copyright`.
- The npm packages in `package.json` are development tools only. The shipped admin bundle is
  compiled from this repository's own TypeScript sources.

| Package | Version | Declared licence | Project URL |
| --- | --- | --- | --- |
| annotated-doc | 0.0.5 | MIT | https://github.com/fastapi/annotated-doc |
| annotated-types | 0.8.0 | MIT | https://github.com/annotated-types/annotated-types |
| anyio | 4.15.1 | MIT | https://github.com/agronholm/anyio |
| asn1crypto | 1.5.1 | MIT License | https://github.com/wbond/asn1crypto |
| brotli | 1.2.1 | MIT | https://github.com/google/brotli |
| certifi | 2026.10.23 | Mozilla Public License 2.0 (MPL 2.0) | https://github.com/certifi/python-certifi |
| cffi | 2.1.1 | MIT-0 | https://github.com/python-cffi/cffi |
| charset-normalizer | 3.5.1 | MIT | https://github.com/jawah/charset_normalizer |
| click | 8.5.0 | BSD-3-Clause | https://github.com/pallets/click/ |
| cryptography | 50.0.1 | Apache-2.0 OR BSD-3-Clause | https://github.com/pyca/cryptography |
| cssselect2 | 0.10.1 | BSD License | https://github.com/Kozea/cssselect2/ |
| fastapi | 0.141.1 | MIT | https://github.com/fastapi/fastapi |
| fonttools | 4.64.0 | MIT | http://github.com/fonttools/fonttools |
| h11 | 0.16.0 | MIT License | https://github.com/python-hyper/h11 |
| httpcore | 1.0.9 | BSD-3-Clause | https://www.encode.io/httpcore/ |
| httptools | 0.8.0 | MIT | https://github.com/MagicStack/httptools |
| httpx | 0.28.1 | BSD License | https://github.com/encode/httpx |
| idna | 3.19 | BSD-3-Clause | https://github.com/kjd/idna |
| Jinja2 | 3.1.6 | BSD License | https://github.com/pallets/jinja/ |
| lxml | 6.1.3 | BSD-3-Clause | https://github.com/lxml/lxml |
| MarkupSafe | 3.0.3 | BSD-3-Clause | https://github.com/pallets/markupsafe/ |
| oscrypto | 1.3.0 | MIT License | https://github.com/wbond/oscrypto |
| pillow | 13.0.0 | MIT-CMU | https://python-pillow.github.io |
| prometheus_client | 0.26.0 | Apache-2.0 AND BSD-2-Clause | https://github.com/prometheus/client_python |
| pycparser | 3.0 | BSD-3-Clause | https://github.com/eliben/pycparser |
| pydantic | 2.13.5 | MIT | https://github.com/pydantic/pydantic |
| pydantic-settings | 2.15.0 | MIT | https://github.com/pydantic/pydantic-settings |
| pydantic_core | 2.46.5 | MIT | https://github.com/pydantic |
| pydyf | 0.12.1 | BSD License | https://github.com/CourtBouillon/pydyf |
| pyHanko | 0.34.1 | MIT | https://github.com/MatthiasValvekens/pyHanko |
| pyhanko-certvalidator | 0.30.2 | MIT | https://github.com/MatthiasValvekens/pyHanko/tree/master/pkgs/pyhanko-certvalidator |
| pyphen | 0.18.1 | GNU General Public License v2 or later (GPLv2+); GNU Lesser General Public License v2 or later (LGPLv2+); Mozilla Public License 1.1 (MPL 1.1) | https://github.com/Kozea/Pyphen |
| python-dotenv | 1.2.3 | BSD-3-Clause | https://github.com/theskumar/python-dotenv |
| PyYAML | 6.0.3 | MIT License | https://github.com/yaml/pyyaml |
| requests | 2.34.2 | Apache Software License | https://github.com/psf/requests |
| starlette | 1.6.0 | BSD-3-Clause | https://github.com/Kludex/starlette |
| structlog | 25.5.0 | MIT OR Apache-2.0 | https://github.com/hynek/structlog |
| tinycss2 | 1.5.1 | BSD License | https://github.com/Kozea/tinycss2/ |
| tinyhtml5 | 2.1.0 | MIT License | https://github.com/CourtBouillon/tinyhtml5 |
| typing-inspection | 0.4.4 | MIT | https://github.com/pydantic/typing-inspection |
| typing_extensions | 4.16.0 | PSF-2.0 | https://github.com/python/typing_extensions |
| tzlocal | 5.4.4 | MIT | https://github.com/regebro/tzlocal |
| uritools | 6.1.3 | MIT | https://github.com/tkem/uritools/ |
| urllib3 | 2.8.0 | MIT | https://github.com/urllib3/urllib3 |
| uvicorn | 0.52.4 | BSD-3-Clause | https://uvicorn.dev/ |
| uvloop | 0.22.1 | Apache Software License; MIT License | https://github.com/MagicStack/uvloop |
| watchfiles | 1.2.0 | MIT License | https://github.com/samuelcolvin/watchfiles |
| weasyprint | 70.0 | BSD License | https://github.com/Kozea/WeasyPrint |
| webencodings | 0.6.1 | BSD License | https://github.com/CourtBouillon/webencodings |
| websockets | 17.1 | BSD-3-Clause | https://github.com/python-websockets/websockets |
| zopfli | 0.4.3 | Apache Software License | https://github.com/fonttools/py-zopfli |
