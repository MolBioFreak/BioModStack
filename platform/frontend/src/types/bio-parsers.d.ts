declare module '@teselagen/bio-parsers' {
    export interface ParseOptions {
        fileName?: string;
        inclusive1BasedStart?: boolean;
        jsonType?: string;
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

    export interface ParseResult {
        parsedSequence?: ParsedSequence;
        success?: boolean;
        messages?: string[];
    }

    export function anyToJson(
        input: string | File | ArrayBuffer | Uint8Array,
        options?: ParseOptions
    ): Promise<ParseResult[] | ParseResult>;

    export function jsonToGenbank(
        json: UntypedApiValue,
        options?: UntypedApiValue
    ): string;
}
