import React from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { licensesToAccept, type ToolPlan } from './toolsInstallerPlan';

export interface ToolRow {
  tool: string;
  name: string;
  label: string;
  modules: string[];
  status: string;
  images: { filename: string; present: boolean }[];
  weights: { id: string; required: boolean; present: boolean }[];
  step?: string | null;
  reason_code?: string | null;
  message?: string | null;
  log_path?: string;
  upstream?: { homepage?: string };
  license?: { id?: string; url?: string } | null;
  notes?: string | null;
}

export interface ModuleRow {
  module: string;
  title: string;
  first_party: boolean;
  selected: boolean;
  state: string;
  tools: { tool: string; status: string }[];
}

export interface ToolsOverview {
  selection_made: boolean;
  container_runtime_present: boolean;
  modules: ModuleRow[];
  tools: ToolRow[];
}

const STATUS_TEXT: Record<string, string> = {
  'not-installed': 'Not installed',
  building: 'Building',
  failed: 'Failed',
  incomplete: 'Incomplete',
  'present-unverified': 'Present, not checked',
  ready: 'Ready',
  'ready-recipe-changed': 'Ready, recipe changed',
};

const MODULE_STATE_TEXT: Record<string, string> = {
  included: 'Included',
  ready: 'Ready',
  'needs-tools': 'Needs tools',
  'no-recipe-yet': 'No recipe yet',
};

const BUTTON = 'rounded-lg border border-[var(--border-primary)] px-3 py-1 text-sm disabled:opacity-50';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    cache: 'no-store',
    ...init,
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = (body as { detail?: { message?: string } | string }).detail;
    throw new Error(typeof detail === 'string' ? detail : detail?.message ?? `Request failed (${response.status})`);
  }
  return body as T;
}

export interface ToolsInstallerViewProps {
  overview: ToolsOverview;
  openTool: string | null;
  plan: ToolPlan | null;
  logLines: string[];
  busy: boolean;
  error: string | null;
  accepted: string[];
  adoptPath: string;
  onToggleModule: (module: string, selected: boolean) => void;
  onOpen: (tool: string | null) => void;
  onAccept: (licenseId: string, accepted: boolean) => void;
  onAdoptPath: (value: string) => void;
  onBuild: (tool: string) => void;
  onAdopt: (tool: string) => void;
  onVerify: (tool: string) => void;
  onRemove: (tool: string) => void;
}

export function ToolsInstallerView(props: ToolsInstallerViewProps) {
  const { overview, openTool, plan, logLines, busy, error, accepted } = props;
  const chosen = new Set(overview.modules.filter((row) => row.selected).map((row) => row.module));
  const shown = overview.selection_made
    ? overview.tools.filter((tool) => tool.modules.some((name) => chosen.has(name)))
    : overview.tools;
  const pending = plan ? licensesToAccept(plan) : [];
  const allAccepted = pending.every((license) => accepted.includes(license.id));

  return (
    <div className="mx-auto max-w-5xl p-6 text-[var(--text-primary)]">
      <h1 className="text-2xl font-bold">Tools</h1>
      <p className="mt-2 text-sm text-[var(--text-secondary)]">
        BioModStack does not ship the scientific tools it runs. Each one is built on this machine from its own
        upstream source, or you can point at an image you already built.
      </p>
      {!overview.container_runtime_present && (
        <p className="mt-4 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
          Apptainer is not installed on this machine. Install it before building any tool.
        </p>
      )}
      {error && (
        <p role="alert" className="mt-4 rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-sm">{error}</p>
      )}

      <h2 className="mt-6 text-lg font-semibold">What this install uses</h2>
      <ul className="mt-2 divide-y divide-[var(--border-primary)] rounded-lg border border-[var(--border-primary)]">
        {overview.modules.map((row) => (
          <li key={row.module} className="flex items-center gap-3 px-3 py-2 text-sm">
            <input
              type="checkbox"
              aria-label={row.title}
              checked={row.selected}
              disabled={row.first_party || busy}
              onChange={(event) => props.onToggleModule(row.module, event.target.checked)}
            />
            <span className="flex-1">{row.title}</span>
            <span className="text-[var(--text-secondary)]">{MODULE_STATE_TEXT[row.state] ?? row.state}</span>
          </li>
        ))}
      </ul>

      <h2 className="mt-6 text-lg font-semibold">Tools</h2>
      {shown.length === 0 && (
        <p className="mt-2 text-sm text-[var(--text-secondary)]">Nothing to build for the parts you chose.</p>
      )}
      <ul className="mt-2 space-y-2">
        {shown.map((tool) => {
          const open = openTool === tool.tool;
          const building = tool.status === 'building';
          return (
            <li key={tool.tool} className="rounded-lg border border-[var(--border-primary)]">
              <button
                type="button"
                className="flex w-full items-center gap-3 px-3 py-2 text-left text-sm"
                aria-expanded={open}
                onClick={() => props.onOpen(open ? null : tool.tool)}
              >
                <span className="flex-1 font-medium">{tool.name}</span>
                {tool.label === 'starting-point' && (
                  <span className="text-xs text-[var(--text-secondary)]">recipe is a starting point</span>
                )}
                <span data-tool-status={tool.status}>{STATUS_TEXT[tool.status] ?? tool.status}</span>
              </button>
              {open && (
                <div className="space-y-3 border-t border-[var(--border-primary)] px-3 py-3 text-sm">
                  {tool.notes && <p className="text-[var(--text-secondary)]">{tool.notes}</p>}
                  <p>
                    {tool.upstream?.homepage && (
                      <a href={tool.upstream.homepage} target="_blank" rel="noreferrer" className="underline">
                        Upstream source
                      </a>
                    )}
                    {tool.license?.id && (
                      <span className="ml-3 text-[var(--text-secondary)]">
                        Licence: {tool.license.url
                          ? <a href={tool.license.url} target="_blank" rel="noreferrer" className="underline">{tool.license.id}</a>
                          : tool.license.id}
                      </span>
                    )}
                  </p>
                  {tool.status === 'failed' && (
                    <p role="alert" className="rounded-lg border border-red-500/40 bg-red-500/10 p-2">
                      Failed at {tool.step ?? 'start'} ({tool.reason_code}): {tool.message}
                    </p>
                  )}
                  {plan && (
                    <ul className="space-y-1">
                      {plan.steps.map((step) => (
                        <li key={step.step}>
                          {step.step.replace(':', ' ')}
                          {step.approx_size_gb ? `, about ${step.approx_size_gb} GB` : ''}
                          {step.already_present ? ' (present)' : ''}
                          {step.method === 'manual' && !step.already_present && step.instructions
                            ? <span className="block text-[var(--text-secondary)]">{step.instructions}</span>
                            : null}
                        </li>
                      ))}
                      {plan.blockers.map((blocker) => (
                        <li key={blocker.reason_code} role="alert">{blocker.message}</li>
                      ))}
                    </ul>
                  )}
                  {pending.map((license) => (
                    <label key={license.id} className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        checked={accepted.includes(license.id)}
                        onChange={(event) => props.onAccept(license.id, event.target.checked)}
                      />
                      <span>
                        I have read and accept {license.url
                          ? <a href={license.url} target="_blank" rel="noreferrer" className="underline">{license.id}</a>
                          : license.id}
                      </span>
                    </label>
                  ))}
                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      className={BUTTON}
                      disabled={busy || building || !plan || plan.blockers.length > 0 || !allAccepted}
                      onClick={() => props.onBuild(tool.tool)}
                    >
                      {tool.status === 'not-installed' ? 'Build' : 'Build again'}
                    </button>
                    <button type="button" className={BUTTON} disabled={busy || building} onClick={() => props.onVerify(tool.tool)}>
                      Check
                    </button>
                    <button type="button" className={BUTTON} disabled={busy || building} onClick={() => props.onRemove(tool.tool)}>
                      Remove
                    </button>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <input
                      type="text"
                      aria-label="Path to an image you already built"
                      placeholder="/path/to/your-image.sif"
                      className="min-w-[18rem] flex-1 rounded-lg border border-[var(--border-primary)] bg-transparent px-2 py-1"
                      value={props.adoptPath}
                      onChange={(event) => props.onAdoptPath(event.target.value)}
                    />
                    <button
                      type="button"
                      className={BUTTON}
                      disabled={busy || building || !props.adoptPath.trim()}
                      onClick={() => props.onAdopt(tool.tool)}
                    >
                      Use existing image
                    </button>
                  </div>
                  {(building || tool.status === 'failed') && logLines.length > 0 && (
                    <details open={building}>
                      <summary className="cursor-pointer text-[var(--text-secondary)]">Build output</summary>
                      <pre className="mt-2 max-h-72 overflow-auto rounded-lg bg-[var(--bg-secondary)] p-2 text-xs">
                        {logLines.join('\n')}
                      </pre>
                    </details>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function ToolsInstaller() {
  const queryClient = useQueryClient();
  const [openTool, setOpenTool] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [accepted, setAccepted] = React.useState<string[]>([]);
  const [adoptPath, setAdoptPath] = React.useState('');

  const overviewQuery = useQuery({
    queryKey: ['tools-overview'],
    queryFn: () => request<ToolsOverview>('/api/tools'),
    refetchOnWindowFocus: false,
    // Poll only while something is building.
    refetchInterval: (query) => (query.state.data?.tools.some((tool) => tool.status === 'building') ? 3000 : false),
  });
  const openStatus = overviewQuery.data?.tools.find((tool) => tool.tool === openTool)?.status;
  const planQuery = useQuery({
    queryKey: ['tools-plan', openTool, openStatus],
    queryFn: () => request<ToolPlan>(`/api/tools/${openTool}/plan`),
    enabled: openTool !== null,
    refetchOnWindowFocus: false,
  });
  const logQuery = useQuery({
    queryKey: ['tools-log', openTool, openStatus],
    queryFn: () => request<{ lines: string[] }>(`/api/tools/${openTool}/log?tail=200`),
    enabled: openTool !== null && (openStatus === 'building' || openStatus === 'failed'),
    refetchOnWindowFocus: false,
    refetchInterval: openStatus === 'building' ? 3000 : false,
  });

  React.useEffect(() => {
    setAccepted([]);
    setAdoptPath('');
    setError(null);
  }, [openTool]);

  const act = React.useCallback(async (work: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'The request failed.');
    } finally {
      setBusy(false);
      await queryClient.invalidateQueries({ queryKey: ['tools-overview'] });
    }
  }, [queryClient]);

  if (overviewQuery.isLoading) {
    return <div className="flex min-h-[24rem] items-center justify-center text-sm text-[var(--text-secondary)]">Loading tools…</div>;
  }
  if (overviewQuery.isError || !overviewQuery.data) {
    return (
      <div className="mx-auto max-w-3xl p-6 text-sm text-[var(--text-primary)]">
        {overviewQuery.error instanceof Error ? overviewQuery.error.message : 'The tool list could not be loaded.'}
      </div>
    );
  }
  const overview = overviewQuery.data;
  const post = (path: string, body?: unknown) =>
    request(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) });

  return (
    <ToolsInstallerView
      overview={overview}
      openTool={openTool}
      plan={planQuery.data ?? null}
      logLines={logQuery.data?.lines ?? []}
      busy={busy}
      error={error}
      accepted={accepted}
      adoptPath={adoptPath}
      onOpen={setOpenTool}
      onAdoptPath={setAdoptPath}
      onAccept={(licenseId, value) =>
        setAccepted((current) => (value ? [...new Set([...current, licenseId])] : current.filter((id) => id !== licenseId)))}
      onToggleModule={(module, selected) => {
        const next = overview.modules
          .filter((row) => !row.first_party && (row.module === module ? selected : row.selected))
          .map((row) => row.module);
        void act(() => request('/api/tools/modules', { method: 'PUT', body: JSON.stringify({ modules: next }) }));
      }}
      onBuild={(tool) => void act(() => post(`/api/tools/${tool}/build`, { accept_licenses: accepted }))}
      onAdopt={(tool) => void act(() => post(`/api/tools/${tool}/adopt`, { path: adoptPath.trim() }))}
      onVerify={(tool) => void act(() => post(`/api/tools/${tool}/verify`))}
      onRemove={(tool) => void act(() => post(`/api/tools/${tool}/remove`))}
    />
  );
}
