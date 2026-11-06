# BioJS synthetic Chromium probe

After building the BioJS package and installing frontend dependencies, start these separately from `platform/frontend`:

```
node_modules/.bin/vite --config tests/browser/biojs/vite.config.mjs --port 43733 --strictPort
google-chrome --headless=new --no-sandbox --disable-gpu --remote-debugging-port=19233 --user-data-dir=DISPOSABLE_PROFILE http://127.0.0.1:43733/tests/browser/biojs/
python tests/browser/biojs/probe.py
```

Use synthetic data and a disposable browser only. Probe requires Python websockets. It physically clicks the rendered canvas and checks residue and gap anchors, tests fetch and image CSP blocking and reports parent requests plus a limited set of frame network events. It does not fully monitor out-of-process frame startup, test adversarial navigation, or prove absence of other exfiltration channels.
