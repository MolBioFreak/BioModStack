import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

describe('Project Manager API contract', () => {
    it('uses one shared attachment interaction from Project Manager and CM', () => {
        const projectPage = readFileSync(resolve(process.cwd(), 'src/components/project-manager/ProjectHubPage.tsx'), 'utf8');
        const cmViewer = readFileSync(resolve(process.cwd(), 'src/components/conformationalMapping/ConformationalMappingViewer.tsx'), 'utf8');
        for (const source of [projectPage, cmViewer]) {
            expect(source).toContain('ProjectAttachmentDialog');
            expect(source).not.toContain('AddExistingDialog');
            expect(source).not.toContain('AddToProjectDialog');
        }
    });
});
