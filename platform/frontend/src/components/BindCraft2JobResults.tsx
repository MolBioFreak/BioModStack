import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { BindCraft2NativeResults, type BindCraft2NativePage, type BindCraft2Stage } from './BindCraft2NativeResults';

/** The native publication is separate from the generic selectable Design route. */
export function BindCraft2JobResults({ jobId }: { jobId: string }) {
  const [query, setQuery] = useState<{ arm: string | null; stage: BindCraft2Stage; offset: number; limit: number }>({
    arm: null, stage: 'trajectory', offset: 0, limit: 25,
  });
  const { data, isLoading, isError } = useQuery<BindCraft2NativePage>({
    queryKey: ['bindcraft2-native-results', jobId, query],
    queryFn: async () => {
      const params = new URLSearchParams({ stage: query.stage, offset: String(query.offset), limit: String(query.limit) });
      if (query.arm !== null) params.set('arm', query.arm);
      const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/bindcraft2-results?${params}`);
      if (!response.ok) throw new Error('Verified native results unavailable');
      return response.json();
    },
  });
  if (isLoading) return <p>Loading BindCraft2 native records...</p>;
  if (isError || !data) return <p role="status">BindCraft2 native records are not available for this job.</p>;
  return <BindCraft2NativeResults page={data} onPage={setQuery} />;
}
