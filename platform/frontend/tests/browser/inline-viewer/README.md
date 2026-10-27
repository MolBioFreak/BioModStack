# Synthetic Chromium probe

From `platform/frontend`, with the main frontend dependencies installed, Python `websockets` and Chrome available, start these in separate terminals:

```
node_modules/.bin/vite --config tests/browser/inline-viewer/vite.config.mjs --port 43731 --strictPort
python tests/browser/inline-viewer/redirect_server.py
google-chrome --headless=new --no-sandbox --disable-gpu --remote-debugging-port=19231 --user-data-dir=DISPOSABLE_PROFILE http://127.0.0.1:43731/tests/browser/inline-viewer/
python tests/browser/inline-viewer/probe.py
```

Use no logged-in browser or private data. The probe loads the real built viewer with two synthetic rows, observes canvases, physically clicks a cell, attempts fetch and image loads, and navigates the frame through a local redirect to a replacement which tries to spoof selection and listens for messages. It asserts the replacement ran but received no messages, and the selection did not change. It also deliberately runs code inside a fresh frame which self-navigates to the local server with a synthetic row, demonstrating the residual exfiltration path. It records parent network events and a bounded set of child network events; it does not observe every out-of-process iframe request from initial startup, establish absence of all outbound channels, or qualify hostile viewer code and other browsers. It is not production acceptance.
