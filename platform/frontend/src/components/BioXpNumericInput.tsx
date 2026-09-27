import { useEffect, useState, type InputHTMLAttributes } from 'react';

/** Keep decimal/sign editing text while exposing typed values to the draft. */
export function BioXpNumericInput({ value, onValueChange, ...props }: Omit<InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange' | 'type'> & {
    value: unknown; onValueChange: (value: number | string) => void;
}) {
    const [text, setText] = useState(String(value ?? ''));
    useEffect(() => {
        setText(current => (current === '' ? value === '' : Number(current) === value) ? current : String(value ?? ''));
    }, [value]);
    return <input {...props} type="number" value={text} onChange={event => {
        const next = event.target.value;
        setText(next);
        onValueChange(next === '' ? '' : Number(next));
    }} />;
}
