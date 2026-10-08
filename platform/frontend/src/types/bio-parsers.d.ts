declare module '@teselagen/bio-parsers' {
    export interface ParseOptions {
        fileName?: string;
        parseOptions?: {
            inclusive1BasedStart?: boolean;
            jsonType?: string;
        };
    }

    export interface ParsedSequence {
        name: string;
        circular: boolean;
        sequence: string;
        features: Array<{
            id?: string;
            name?: string;
            type?: string;
            start: number;
            end: number;
            strand?: number;
            color?: string;
            [key: string]: UntypedApiValue;
        }>;
        [key: string]: UntypedApiValue;
    }

    export function anyToJson(
        input: string | File,
        options?: ParseOptions
    ): Promise<Array<{ parsedSequence: ParsedSequence }> | { parsedSequence: ParsedSequence }>;

    export function jsonToGenbank(
        json: UntypedApiValue,
        options?: UntypedApiValue
    ): string;
}
