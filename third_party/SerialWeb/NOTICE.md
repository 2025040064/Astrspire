# SerialWeb source adaptation

- Upstream: https://github.com/Conductance-lab/SerialWeb
- Commit: ff84c5d43dde6b6c49cddc1b7b01f9c55e3ece1a
- Author: Conductance-lab (电导实验室).
- License: GNU Affero General Public License v3.0; full unmodified text in LICENSE.
- Reference: index.html, normalizeEncodingLabel/decodeBytes/encodeText and RX multibyte/line-boundary helpers; excerpts retained in reference.js, not executed.
- Adaptation date: 2026-10-26.
- Python port: src/qx_stc_isp/vofa/serialweb.py. Desktop integration: transmit.py and gui.py.
- Changes: Python incremental codecs replace browser TextDecoder; GBK retained; explicit size bounds and strict lossless-send validation retained; no browser UI, analytics, Web Serial auto-connect, blur-release, automatic reply or automatic TX restoration imported.

This is an attributed source port, not a claim of independent authorship or full SerialWeb compatibility. Distributing a combined derivative must satisfy the upstream AGPL terms, including applicable corresponding-source obligations. Do not describe this integration as proprietary-only. The local source archive includes this notice, the complete license, and the adapted source; no publication is performed automatically.
