"""The agent browser (Plan 7 U4c): web_search / web_open / part_show on top of the parts
directory's hardened fetcher (mises.catalog.importer.fetch). Never a real web browser: https
GETs only, no cookies, no forms, no logins, no POSTs, no JavaScript. What the user sees is a
stored, readable snapshot of each page; what the model sees is the same text inside one
nonce-delimited untrusted block.
"""
