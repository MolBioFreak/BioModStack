import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, afterEach, expect, it } from 'vitest';
import { IndependentProvisionPanel } from '../../src/components/dashboard/IndependentProvisionPanel';
import { api, type ExecutionTarget } from '../../src/lib/api';

let container: HTMLDivElement;
let root: Root;
let client: QueryClient;
let gets: string[];
const adapter = api.defaults.adapter;
const ready: ExecutionTarget = { id:'vast:1', provider:'vast', provider_instance_id:'1', name:'Worker',
  state:'ready', active:true, host:'host', port:22, username:'root', remote_root:'/remote',
  host_key_sha256:'a'.repeat(64), capabilities:{}, pricing:{}, last_error:null, last_seen_at:null, activated_at:null };
let target: ExecutionTarget;
const wait = () => new Promise(resolve => setTimeout(resolve, 25));
const render = async () => { await act(async () => {
  root.render(<QueryClientProvider client={client}><IndependentProvisionPanel target={target} onChanged={() => {}} /></QueryClientProvider>);
  await wait();
}); };
const disclose = async (open: boolean) => { await act(async () => {
  const details = container.querySelector('details')!;
  details.open = open;
  details.dispatchEvent(new Event('toggle'));
  await wait();
}); };
beforeEach(() => {
  target = {...ready}; gets = [];
  container = document.createElement('div'); document.body.append(container); root = createRoot(container);
  client = new QueryClient({defaultOptions:{queries:{retry:false, staleTime:Infinity}}});
  api.defaults.adapter = async config => {
    gets.push(String(config.url));
    const data = config.url?.endsWith('/runtime-inventory') ? null : [];
    return {data,status:200,statusText:'OK',headers:{},config};
  };
});
afterEach(async () => {
  await act(async () => root.unmount());
  client.clear(); container.remove(); api.defaults.adapter = adapter;
});

it('does zero collapsed inventory GETs and keeps one attachment query across ten progress updates', async () => {
  target = {...target, preload:{operation_id:'operation', source_revision:'a'.repeat(40), source_tree:'b'.repeat(40),
    request_sha256:'c'.repeat(64), phase:'checking', message:'Planning', started_at:'2026-09-30T12:00:00',
    updated_at:'2026-09-30T12:00:00', artifact_summary:{total_count:0,verified_count:0,total_bytes:0,verified_bytes:0},cached_artifact_count:0}};
  await render();
  expect(gets.filter(url => url.endsWith('/runtime-inventory'))).toHaveLength(0);
  await disclose(true);
  expect(gets.filter(url => url.endsWith('/runtime-inventory'))).toHaveLength(1);
  for (let n=0; n<10; n++) {
    target = {...target, preload:{...target.preload!,phase:n%2===0?'transferring':'verifying',
      sequence:n,updated_at:`2026-09-30T12:00:${String(n).padStart(2,'0')}`},
      capabilities:{readiness:{free_bytes:n}, scheduling:{inventory_fresh:n%2===0}}};
    await render();
  }
  expect(gets.filter(url => url.endsWith('/runtime-inventory'))).toHaveLength(1);
  expect(client.getQueryCache().findAll({queryKey:['managed-runtime-inventory']})).toHaveLength(1);
  target = {...target,preload:{...target.preload!,phase:'source_download_ready'}};
  await render();
  await act(async () => { await wait(); });
  expect(gets.filter(url => url.endsWith('/runtime-inventory'))).toHaveLength(2);
  await disclose(false);
  expect(container.querySelector('[aria-label="Managed runtime inventory"]')).toBeNull();
  target = {...target, activated_at:'2026-09-30T12:00:00'};
  await render();
  expect(gets.filter(url => url.endsWith('/runtime-inventory'))).toHaveLength(2);
  await disclose(true);
  expect(gets.filter(url => url.endsWith('/runtime-inventory'))).toHaveLength(3);
});

it('retains scope choice on observation changes and resets on backend generation change', async () => {
  await render();
  await act(async () => {
    const select = container.querySelector<HTMLSelectElement>('[aria-label="Provision scope"]')!;
    select.value='image'; select.dispatchEvent(new Event('change',{bubbles:true})); await wait();
  });
  target = {...target,capabilities:{readiness:{free_bytes:123},scheduling:{inventory_fresh:false}}};
  await render();
  expect(container.querySelector<HTMLSelectElement>('[aria-label="Provision scope"]')!.value).toBe('image');
  target = {...target,capabilities:{container_backend:'udocker'}};
  await render();
  expect(container.querySelector<HTMLSelectElement>('[aria-label="Provision scope"]')!.value).toBe('workflow');
});
