"""Pinned AV2 lowercase renderer regression. Build av2 first; requires playwright.
Run: AV2_CHROME=/path/to/chrome python tests/alignment_av2_case_browser.py
Artifacts go to AV2_EVIDENCE_DIR (or a private TMPDIR child). No app service.
"""
import json
import os
from pathlib import Path
import tempfile
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[1]
out = Path(os.environ.get('AV2_EVIDENCE_DIR') or tempfile.mkdtemp(prefix='av2-case-', dir=os.environ['TMPDIR']))
out.mkdir(parents=True, exist_ok=True)
code = Path(os.environ.get('AV2_BUNDLE', str(root / 'alignment-viewers/av2/dist/viewer.js'))).read_text()
sequences = ['acgtacgtacgtacgtacgtacgt', 'acgtacgtacgttcgtacgtacgt', 'acgtacgtacgtacgtacgttcgt']
errors = []
requests = []
evidence = {}
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.environ.get('AV2_CHROME'), args=['--no-sandbox', '--enable-unsafe-swiftshader'])
    page = browser.new_page(viewport={'width': 1440, 'height': 913})
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('request', lambda request: requests.append(request.url))
    page.set_content('<script>window.messages=[];addEventListener("message",e=>messages.push(e.data))</script><iframe sandbox="allow-scripts" style="width:1400px;height:650px"></iframe>')
    for case in ['lower', 'upper', 'mixed']:
        projected = sequences if case == 'lower' else [s.upper() if case == 'upper' else ''.join(c.upper() if i % 2 else c for i, c in enumerate(s)) for s in sequences]
        payload = {'rows': [{'rowId': f'r{i}', 'label': f'row {i}', 'aligned': s} for i, s in enumerate(projected)], 'token': 'probe', 'viewerHeight': 553, 'state': {}, 'marks': []}
        # Same opaque sandbox/CSP as ViewerFrame; no network and blob workers only.
        csp = "default-src 'none'; script-src 'nonce-probe'; style-src 'unsafe-inline'; img-src data: blob:; connect-src 'none'; worker-src blob:; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
        html = '<!doctype html><meta http-equiv="Content-Security-Policy" content="' + csp + '"><style>html,body{margin:0;background:#fff;color:#1f2937;color-scheme:light;font-family:system-ui,sans-serif}</style><div id="root"></div><script id="payload" type="application/json">' + json.dumps(payload) + '</script><script nonce="probe">' + code.replace('</script', '<\\/script') + '</script>'
        page.locator('iframe').evaluate('(el,s)=>el.srcdoc=s', html)
        frame = page.frames[1]
        matrix = frame.locator('.content[style*="main-viewport"]')
        matrix.locator('.letters-with-specific-color').first.wait_for()
        page.wait_for_timeout(600)
        assert frame.locator('.sequence-logo-holder svg path').count() > 0, 'native logo is empty'
        assert frame.locator('select option').all_text_contents() == ['Default', 'Nucleotide', 'Purine'], 'wrong native alphabet'
        assert json.loads(frame.locator('#payload').text_content()) == payload, 'retained input changed'
        colors = matrix.locator('.letters-with-specific-color').evaluate_all('(els)=>els.map(e=>({text:e.textContent,color:getComputedStyle(e).color}))')
        assert len(colors) == 4 and all(c['color'] != 'rgb(204, 204, 204)' for c in colors), colors
        pixels = matrix.screenshot(path=str(out / f'{case}-matrix.png'))
        if case == 'lower': reference = pixels
        else: assert pixels == reference, 'case-equivalent native matrix pixels differ'
        page.screenshot(path=str(out / f'{case}.png'))
        # Trusted native hit testing retains row/column correspondence after folding.
        cell = matrix.locator('.av2-wheel-scroller')
        cell.click(position={'x': 75, 'y': 20})
        page.wait_for_timeout(100)
        selected = page.evaluate("messages.filter(m=>Number.isInteger(m.index)).at(-1)")
        assert selected and selected['index'] == 1 and selected['column'] == 12, selected
        state = {'zoomLevel': 18, 'colorScheme': 'Nucleotide', 'scrollX': 0, 'scrollY': 0}
        def control(marks):
            page.locator('iframe').evaluate('(el,data)=>el.contentWindow.postMessage(data,"*")', {'channel': 'bms-av2-inline-v1', 'token': 'probe', 'type': 'control', 'state': state, 'marks': marks})
        control([{'index': 1, 'start': 12, 'end': 12}])
        page.wait_for_timeout(500)
        marked = matrix.screenshot()
        control([])
        page.wait_for_timeout(500)
        assert matrix.screenshot() != marked, 'native highlight did not change matrix pixels'
        reported = page.evaluate("messages.filter(m=>m.type==='state').at(-1).state")
        assert reported == state, reported
        evidence[case] = {'colors': colors, 'selection': selected, 'matrixPixelsEqual': True, 'nativeMarkPixelsChanged': True, 'nativeState': reported}
    assert not errors, errors
    network = [url for url in requests if not url.startswith('blob:')]
    assert not network, network
    evidence.update(runtimeErrors=errors, networkRequests=network, blobWorkerRequests=len(requests), sandbox=page.locator('iframe').get_attribute('sandbox'))
    (out / 'evidence.json').write_text(json.dumps(evidence, indent=2))
    print(json.dumps({'passed': list(evidence)[:3], 'evidence': str(out / 'evidence.json')}))
    browser.close()
