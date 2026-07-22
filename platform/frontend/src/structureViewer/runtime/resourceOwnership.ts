export type ResourceDisposer = () => void | Promise<void>;

interface OwnedResource {
    readonly id: string;
    readonly generation: number;
    readonly dispose: ResourceDisposer;
    disposed: boolean;
}

export class ViewerResourceOwner {
    private generation = 0;
    private disposed = false;
    private readonly resources = new Map<string, OwnedResource>();

    beginGeneration(): number {
        if (this.disposed) throw new Error('Viewer resource owner is disposed');
        this.generation += 1;
        return this.generation;
    }

    own(id: string, dispose: ResourceDisposer, generation = this.generation): () => Promise<void> {
        if (this.disposed) throw new Error('Viewer resource owner is disposed');
        const existing = this.resources.get(id);
        if (existing) void this.disposeResource(existing);
        const resource: OwnedResource = { id, generation, dispose, disposed: false };
        this.resources.set(id, resource);
        return async () => this.disposeResource(resource);
    }

    isCurrent(generation: number): boolean { return !this.disposed && generation === this.generation; }

    snapshot(): readonly { id: string; generation: number; disposed: boolean }[] {
        return [...this.resources.values()].map(({ id, generation, disposed }) => ({ id, generation, disposed }));
    }

    disposeSync(id: string): void {
        const resource = this.resources.get(id);
        if (!resource || resource.disposed) return;
        resource.disposed = true;
        if (this.resources.get(resource.id) === resource) this.resources.delete(resource.id);
        const result = resource.dispose();
        if (result instanceof Promise) throw new Error(`Resource ${id} requires asynchronous disposal`);
    }

    async disposeGeneration(generation: number): Promise<void> {
        const resources = [...this.resources.values()].filter((resource) => resource.generation <= generation);
        await Promise.allSettled(resources.map((resource) => this.disposeResource(resource)));
    }

    async dispose(): Promise<void> {
        if (this.disposed) return;
        this.disposed = true;
        this.generation += 1;
        await Promise.allSettled([...this.resources.values()].map((resource) => this.disposeResource(resource)));
        this.resources.clear();
    }

    private async disposeResource(resource: OwnedResource): Promise<void> {
        if (resource.disposed) return;
        resource.disposed = true;
        if (this.resources.get(resource.id) === resource) this.resources.delete(resource.id);
        await resource.dispose();
    }
}
