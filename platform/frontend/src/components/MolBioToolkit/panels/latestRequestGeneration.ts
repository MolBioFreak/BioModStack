export class LatestRequestGeneration {
    private generation = 0;
    private scope: string | null = null;

    begin(): number {
        this.generation += 1;
        return this.generation;
    }

    invalidate(): void {
        this.generation += 1;
    }

    reconcileScope(scope: string): number {
        if (scope !== this.scope) {
            this.scope = scope;
            this.generation += 1;
        }
        return this.generation;
    }

    current(): number {
        return this.generation;
    }

    isCurrent(generation: number): boolean {
        return generation === this.generation;
    }
}