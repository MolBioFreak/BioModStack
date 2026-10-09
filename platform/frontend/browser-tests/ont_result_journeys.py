"""Real DOM/native artifact journeys, called by ont_acceptance in its namespace."""
import json
import subprocess
import sys


def run_result_journey(here, root, case):
    job_id = case['job_id']
    expression = r"""(async () => {
      const wait = ms => new Promise(r => setTimeout(r, ms));
      const button = text => [...document.querySelectorAll('button')].find(b => b.textContent.trim() === text && !b.disabled);
      const roots = () => { const found=[document]; for(let i=0;i<found.length;i++) for(const e of found[i].querySelectorAll('*')) if(e.shadowRoot) found.push(e.shadowRoot); return found; };
      const snapshot = () => ({text:document.body.innerText, shadowText:roots().map(r=>r.textContent).join('\n'), canvases:roots().flatMap(r=>[...r.querySelectorAll('canvas')]).map(c=>({width:c.width,height:c.height})), tracks:roots().flatMap(r=>[...r.querySelectorAll('.igv-track-label')]).map(e=>e.textContent)});
      await wait(2000);
      const response=await fetch('/api/jobs/JOB/ngs-artifacts');
      const catalog=await response.json();
      const downloads=[];
      for(const a of catalog.artifacts.filter(a=>a.state==='present' && a.url)) {
        const res=await fetch(a.url); const bytes=await res.arrayBuffer();
        const sha256=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(n=>n.toString(16).padStart(2,'0')).join('');
        downloads.push({artifact_id:a.artifact_id,filename:a.filename,status:res.status,size_bytes:bytes.byteLength,sha256,declared_size:a.size_bytes,declared_sha256:a.sha256});
      }
      const report=snapshot();
      let opened=null,reopened=null;
      if(catalog.igv?.length) {
        const open=button('Open IGV'); if(!open) throw Error('Open IGV unavailable'); open.click();
        for(let n=0;n<200;n++){await wait(100); if(roots().some(r=>r.querySelector('.igv-track-label')) && !document.body.innerText.includes('Loading IGV')) break;}
        await wait(2000); opened=snapshot();
        opened.exportControls=roots().flatMap(r=>[...r.querySelectorAll('[title]')]).map(e=>e.getAttribute('title')).filter(t=>/svg|export|save/i.test(t));
        const svg=roots().flatMap(r=>[...r.querySelectorAll('[title]')]).find(e=>/^Save Image$/i.test(e.getAttribute('title')));
        if(svg){svg.click();await wait(100);
          const item=roots().flatMap(r=>[...r.querySelectorAll('div')]).find(e=>e.textContent.trim()==='Save as SVG');
          if(item){item.click();await wait(500);opened.exportClicked=true;}
        }
        const close=[...document.querySelectorAll('button')].find(b=>b.getAttribute('aria-label')?.includes('Close IGV') || b.textContent.trim()==='Close');
        if(close) {close.click(); await wait(200);button('Open IGV')?.click();await wait(2500);reopened=snapshot();}
      }
      return {catalog_status:response.status,report,opened,reopened,downloads};
    })()""".replace('JOB', job_id)
    before_exports = set((root / 'downloads').glob('*.svg'))
    evidence = root / f'journey-{job_id}.json'
    subprocess.run([sys.executable, str(here / 'ont_cdp.py'), '--alignment-job', job_id,
                    '--url', f'http://127.0.0.1:18762/browser-tests/ont-suite.html?section=analyses&job_id={job_id}',
                    '--wait', '3', '--expression', expression, '--evidence', str(evidence)],
                   check=True, stdout=(root / f'journey-{job_id}.stdout').open('w'))
    data=json.loads(evidence.read_text())
    result=data['result']['result']['value']
    inventory=json.loads((root / f'imported-{job_id}.json').read_text())
    hashes={a['sha256'] for a in inventory['artifacts']}
    for item in result['downloads']:
        assert item['status']==200 and item['size_bytes']==item['declared_size'] and item['sha256']==item['declared_sha256'] and item['sha256'] in hashes, item
    missing=[s for s in case.get('expected_text',[]) if ''.join(s.split()) not in ''.join(result['report']['text'].split())]
    import hashlib
    import xml.etree.ElementTree as ET
    exports=[]
    for path in set((root / 'downloads').glob('*.svg')) - before_exports:
        payload=path.read_bytes()
        assert ET.fromstring(payload).tag.endswith('svg') and len(payload)>1000
        destination=path.with_name(f'{job_id}-{path.name}')
        path.rename(destination)
        exports.append({'path':str(destination),'size_bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest()})
    assert exports, f'No native IGV SVG export for {job_id}'
    return {'exports':exports,'job_id':job_id,'download_count':len(result['downloads']),'missing_text':missing,
            'igv_tracks':result['opened']['tracks'] if result['opened'] else [],
            'igv_canvas_count':len(result['opened']['canvases']) if result['opened'] else 0,
            'reopened':bool(result['reopened'] and result['reopened']['tracks']),
            'evidence':str(evidence)}


def run_pooled_release(here, root):
    """Explicit DOM release with native prepare, cancel/review and SQL readback."""
    import sqlite3
    from ont_result_fixtures import POOLED_JOB
    rows=[]
    for target in ['local', 'selected']:
        expression=r"""(async()=>{
          const wait=ms=>new Promise(r=>setTimeout(r,ms));
          const panel=()=>document.querySelector('[data-testid="pooled-assignment-review-panel"]');
          const button=text=>[...document.querySelectorAll('button')].find(b=>b.textContent.trim()===text && !b.disabled);
          const set=(e,value)=>{Object.getOwnPropertyDescriptor(e instanceof HTMLSelectElement?HTMLSelectElement.prototype:HTMLInputElement.prototype,'value').set.call(e,value);e.dispatchEvent(new Event('change',{bubbles:true}));e.dispatchEvent(new Event('input',{bubbles:true}));};
          for(let i=0;i<100&&!panel()?.querySelector('input[type=checkbox]');i++)await wait(100);
          const initial=panel().innerText;
          const initialChecked=panel().querySelectorAll('input[type=checkbox]:checked').length;
          const initialReleaseDisabled=[...panel().querySelectorAll('button')].find(b=>b.textContent.trim()==='Release selected targets').disabled;
          panel().querySelector('input[aria-label="Explicitly select target-a"]').click();
          const label=[...panel().querySelectorAll('label')].find(l=>l.textContent.includes('Optional name prefix'));
          set(label.querySelector('input'),'native-TARGET-draft'); await wait(100);
          set(panel().querySelector('[aria-label="Successful remote results"]'),'TARGET'==='local'?'manual':'automatic');
          if('TARGET'==='selected')button('Vast · synthetic-never-contact').click();
          await wait(200);button('Release selected targets').click();
          let review=null,cancelled=null;
          if('TARGET'==='selected'){
            for(let i=0;i<200&&!button('Approve and submit');i++)await wait(100);
            review=document.body.innerText;
            if(!button('Approve and submit'))return {initial,initialChecked,initialReleaseDisabled,review,error:'no admissible preview'};
            button('Cancel').click();await wait(200);
            cancelled={text:panel().innerText,name:label.querySelector('input').value,checked:panel().querySelectorAll('input[type=checkbox]:checked').length};
            button('Release selected targets').click();
            for(let i=0;i<100&&!button('Approve and submit');i++)await wait(100);
            button('Approve and submit').click();
          }
          for(let i=0;i<200&&!panel().querySelector('[data-testid="pooled-assignment-release-result"]');i++)await wait(100);
          const result=panel().querySelector('[data-testid="pooled-assignment-release-result"]')?.innerText;
          const ids=[...panel().querySelectorAll('[data-testid="pooled-assignment-release-result"] li')].map(e=>e.textContent);
          const jobs=[];for(const id of ids){const r=await fetch('/api/jobs/'+id);jobs.push({status:r.status,job:await r.json()});}
          return {initial,initialChecked,initialReleaseDisabled,review,cancelled,result,jobs,final:panel().innerText,name:label.querySelector('input').value};
        })()""".replace('TARGET',target)
        evidence=root/f'pooled-release-{target}.json'
        subprocess.run([sys.executable,str(here/'ont_cdp.py'),'--alignment-job',POOLED_JOB,'--url',
            f'http://127.0.0.1:18762/browser-tests/ont-suite.html?section=analyses&job_id={POOLED_JOB}',
            '--wait','3','--expression',expression,'--evidence',str(evidence)],check=True,stdout=(root/f'pooled-release-{target}.stdout').open('w'))
        data=json.loads(evidence.read_text())['result']['result']['value']
        with sqlite3.connect(root/'jobs.db') as db:
            db.row_factory=sqlite3.Row
            sql=[dict(row) for row in db.execute("SELECT id,name,execution_target_id,provenance,parent_job_id,params,status FROM jobs WHERE name LIKE 'native-%-draft%'")]
            count=db.execute('SELECT count(*) FROM ngs_pooled_assignment_releases').fetchone()[0]
        row={'target':target,'browser':data,'sql_jobs':sql,'release_count':count}
        rows.append(row)
        (root/'pooled-release-readback.json').write_text(json.dumps(rows,indent=2))
    for index, row in enumerate(rows, 1):
        assert row['release_count'] == index and len(row['sql_jobs']) == index, row
        assert row['browser']['initialChecked'] == 0 and row['browser']['initialReleaseDisabled'], row
        assert len(row['browser']['jobs']) == 1 and row['browser']['jobs'][0]['status'] == 200, row
        job = row['browser']['jobs'][0]['job']
        assert job['execution_target_id'] == (None if row['target'] == 'local' else 'scratch-selected-target'), row
        assert job['execution_policy']['remote_result_policy'] == ('manual' if row['target'] == 'local' else 'automatic'), row
        assert job.get('remote_attempt_id') is None, row
        if row['target'] == 'selected':
            assert row['browser']['cancelled']['checked'] == 1 and row['browser']['cancelled']['name'] == 'native-selected-draft', row
    return rows


def run_saved_continuations(here, root, releases):
    result=[]
    for release in releases:
        for saved in release['browser']['jobs']:
            job=saved['job']
            expression=r"""(async()=>{
              const wait=ms=>new Promise(r=>setTimeout(r,ms));
              let reuse;
              for(let i=0;i<100;i++){reuse=[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Reuse Params'&&!b.disabled);if(reuse)break;await wait(100);}
              if(!reuse)throw Error('Saved child could not be reopened');
              reuse.click();await wait(1000);
              return {text:document.body.innerText, placement:[...document.querySelectorAll('[aria-label="Execution target"] button[aria-pressed="true"]')].map(e=>e.textContent.trim()),
                policy:document.querySelector('[aria-label="Successful remote results"]')?.value,
                inputs:[...document.querySelectorAll('input')].map(e=>({value:e.value,type:e.type})),url:location.href};
            })()"""
            evidence=root/f"reopen-{job['id']}.json"
            subprocess.run([sys.executable,str(here/'ont_cdp.py'),'--url',
                f"http://127.0.0.1:18762/browser-tests/ont-suite.html?section=analyses&job_id={job['id']}",
                '--wait','3','--expression',expression,'--evidence',str(evidence)],check=True,stdout=(root/f"reopen-{job['id']}.stdout").open('w'))
            received=json.loads(evidence.read_text())['result']['result']['value']
            expected_policy=job['execution_policy']['remote_result_policy']
            expected_target='Local' if job['execution_target_id'] is None else 'Vast · synthetic-never-contact'
            assert received['policy']==expected_policy, received
            assert expected_target in received['placement'], received
            assert any(i['value']==job['name'] for i in received['inputs']),received
            result.append({'job_id':job['id'],'placement':received['placement'],'policy':received['policy'],'evidence':str(evidence)})
    return result
