// Type declarations for @biomodstack/ove
declare module '@biomodstack/ove' {
    import { FC } from 'react';

    export interface EditorProps {
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
                color?: string;
                [key: string]: any;
            }>;
            [key: string]: any;
        };
        onSave?: (data: any) => void;
        editorName?: string;
        [key: string]: any;
    }

    export const Editor: FC<EditorProps>;
}

declare module '@biomodstack/ove/src/style.css';
