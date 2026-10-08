// Type declarations for @biomodstack/bms-plugin
declare module '@biomodstack/bms-plugin' {
    import { FC } from 'react';

    export interface BioDesignerProps {
        sequenceData?: {
            name?: string;
            circular?: boolean;
            sequence?: string;
            features?: Array<{
                id: string;
                name: string;
                type?: string;
                start: number;
                end: number;
                strand?: number;
                forward?: boolean;
            }>;
        };
        onSave?: (data: unknown) => void;
    }

    export const BioDesigner: FC<BioDesignerProps>;
}

declare module '@biomodstack/bms-plugin/dist/bms-plugin.css';
