"""BigBoy: generate independent expectations, then exercise the deployed UI.

D=$HOME/.cache/ont-gap-close/final-batch
S=scripts/accept_barcode_squiggle.py
O=$HOME/.cache/ont-gap-close/squiggle-handoff
~/.local/share/bms-api-test-venv/bin/python "$S" expected "$D/managed-retry2/child.json" "$D/source-readback.json" "$O/expected.json"
uv run --offline --with playwright python "$S" browser "$O/expected.json" "$D/session.jar" "$O/browser"
"""
import argparse
import hashlib
import http.cookiejar
import json
import re
import shutil
from pathlib import Path
from urllib.parse import urlparse


def digest(values):
    return hashlib.sha256(json.dumps([round(float(x), 2) for x in values], separators=(',', ':')).encode()).hexdigest()


def expected(args):
    import pod5
    import pysam

    child, parent = [json.loads(path.read_text()) for path in (args.child, args.parent)]
    params = child['params']
    assert child['status'] == 'completed' and params['source_ont_job_id'] == parent['id']
    windows = {}
    members = set()
    with pysam.AlignmentFile(params['bam_path'], 'rb', check_sq=False) as bam:
        for read in bam:
            if read.is_secondary or read.is_supplementary:
                continue
            members.add(read.query_name)
            if not read.has_tag('mv'):
                continue
            stride, *moves = read.get_tag('mv')
            trim = read.get_tag('ts') if read.has_tag('ts') else 0
            boundaries = [trim + i * stride for i, move in enumerate(moves) if move]
            assert len(boundaries) == len(read.query_sequence)
            end = boundaries[120] if len(boundaries) > 120 else (read.get_tag('ns') if read.has_tag('ns') else trim + len(moves)*stride)
            windows[read.query_name] = boundaries[0], end, read.query_sequence[:120]
    traces = {}
    for file in Path(parent['params']['pod5_dir']).rglob('*.pod5'):
        with pod5.Reader(file) as reader:
            for rec in reader.reads(selection=set(reader.read_ids) & windows.keys()):
                rid = str(rec.read_id)
                start, end, bases = windows[rid]
                traces[rid] = dict(sample_offset=start, sha256=digest(rec.signal_pa[start:end]), bases=bases, pod5_file=file.name)
    assert traces and windows.keys() == traces.keys()
    args.output.write_text(json.dumps(dict(child=child['id'], barcode=params['source_barcode_unit'], members=sorted(members), traces=traces)))
    print(f'{len(members)} barcode reads; {len(traces)} independent POD5 windows')


def browser(args):
    from playwright.sync_api import TimeoutError as PlaywrightTimeout, sync_playwright, expect

    oracle = json.loads(args.expected.read_text())
    job = oracle['child']
    origin = 'http://127.0.0.1:18082'
    base = f'/api/jobs/{job}/ngs-squiggle/reads'
    args.output.mkdir(exist_ok=False)
    with sync_playwright() as pw:
        chrome = pw.chromium.launch(executable_path=shutil.which('google-chrome'))
        context = chrome.new_context(viewport={'width': 1600, 'height': 1100})
        jar = http.cookiejar.MozillaCookieJar(str(args.jar))
        jar.load(ignore_discard=True)
        context.add_cookies([dict(name=c.name, value=c.value, domain=c.domain, path=c.path, secure=c.secure) for c in jar])
        version = context.request.get(origin + '/api/version').json()
        receipt = dict(version=version, job=job, barcode=oracle['barcode'])
        for path in ('report', 'IGV'):
            # Separate contexts of page JS: no query cache from the first handoff.
            page = context.new_page()
            requests = []
            page.on('request', lambda r: requests.append(urlparse(r.url).path) if '/ngs-squiggle/' in r.url else None)
            page.goto(f'{origin}/ngs?section=analyses&job_id={job}', wait_until='domcontentloaded')
            report = page.get_by_role('region', name='Plasmid summary')
            selector = page.get_by_label('Squiggle read', exact=True)
            if path == 'report':
                report.get_by_role('button', name='Open squiggles').click()
                expect(selector).to_have_value('')
                ids = selector.locator('option').evaluate_all('(os) => os.map(o=>o.value).filter(Boolean)')
                assert ids and set(ids) <= set(oracle['members']) and set(requests) == {base}
                rid = next(r for r in reversed(ids) if r in oracle['traces'])
                action = lambda: selector.select_option(rid)
            else:
                report.get_by_role('button', name='View in IGV').click()
                viewport = page.locator('.igv-viewport[data-track-type="alignment"]').first
                viewport.locator('canvas').first.wait_for()
                viewport.scroll_into_view_if_needed()
                box = viewport.bounding_box()
                assert box
                selected = page.get_by_role('button', name=re.compile('^Open squiggles for '))
                for y in range(62, int(min(box['height'], 260)), 14):
                    viewport.click(position={'x': int(box['width'] / 2), 'y': y})
                    try:
                        selected.wait_for(timeout=900)
                        break
                    except PlaywrightTimeout:
                        page.keyboard.press('Escape')
                expect(selected).to_be_visible()
                rid = selected.inner_text().removeprefix('Open squiggles for ')
                assert rid in oracle['traces'] and not requests
                # Confirm the separately painted native IGV popup agrees with the action.
                expect(page.get_by_title(rid, exact=True).filter(has_text="Read Name").first).to_be_visible()
                page.screenshot(path=str(args.output/'igv-selected.png'))
                action = selected.click
            with page.expect_response(lambda r: urlparse(r.url).path == base + '/' + rid and r.ok) as response:
                action()
            data = response.value.json()
            ref = oracle['traces'][rid]
            assert data['read_id'] == rid and data['sample_offset'] == ref['sample_offset'] and data['pod5_file'] == ref['pod5_file']
            assert data['signal_pa'] and digest(data['signal_pa']) == ref['sha256']
            expect(selector).to_have_value(rid)
            expect(page.get_by_label('Called bases')).to_have_text(ref['bases'])
            plot = page.get_by_label('Squiggle viewer').locator('.js-plotly-plot')
            expect(plot).to_be_visible()
            assert plot.evaluate('(el) => Array.from(el.data[0].y)') == data['signal_pa']
            assert set(requests) == ({base, base+'/'+rid} if path == 'report' else {base+'/'+rid})
            page.screenshot(path=str(args.output/(path+'-trace.png')))
            receipt[path] = dict(read=rid, samples=len(data['signal_pa']), sha256=ref['sha256'], requests=requests)
            page.close()
        (args.output/'receipt.json').write_text(json.dumps(receipt, indent=2))
        print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='mode', required=True)
    for mode, names in [('expected', ('child', 'parent', 'output')), ('browser', ('expected', 'jar', 'output'))]:
        sub = commands.add_parser(mode)
        for name in names:
            sub.add_argument(name, type=Path)
    args = parser.parse_args()
    (expected if args.mode == 'expected' else browser)(args)
