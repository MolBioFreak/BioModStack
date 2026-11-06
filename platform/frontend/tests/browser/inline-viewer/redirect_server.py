from http.server import BaseHTTPRequestHandler, HTTPServer
seen = []
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/seen':
            self.send_response(200); self.end_headers(); self.wfile.write(('\n'.join(seen)).encode()); return
        if self.path.startswith('/leak?'):
            seen.append(self.path)
        if self.path == '/redirect':
            self.send_response(302); self.send_header('Location','/replacement'); self.end_headers(); return
        self.send_response(200); self.send_header('Content-Type','text/html'); self.end_headers()
        self.wfile.write(b"<script>parent.postMessage({probe:'replacement-ready'},'*');onmessage=e=>parent.postMessage({probe:'leak',data:e.data},'*');parent.postMessage({channel:'bms-jbrowse-inline-v1',token:'wrong',index:1,column:2},'*');</script>")
HTTPServer(('127.0.0.1',43732),Handler).serve_forever()
