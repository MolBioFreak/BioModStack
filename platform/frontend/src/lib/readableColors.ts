function rgb(value: string): number[] | null {
    const hex = value.trim().match(/^#([0-9a-f]{6})$/i);
    if (hex) return [0, 2, 4].map((offset) => parseInt(hex[1].slice(offset, offset + 2), 16));
    const decimal = value.trim().match(/^rgba?\(\s*(\d+)\s*[, ]\s*(\d+)\s*[, ]\s*(\d+)/i);
    return decimal ? decimal.slice(1, 4).map(Number) : null;
}
function luminance(color: number[]) {
    const linear = color.map((channel) => { const value = channel / 255; return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4; });
    return .2126 * linear[0] + .7152 * linear[1] + .0722 * linear[2];
}
export function textContrast(foreground: string, background: string) {
    const fg = rgb(foreground); const bg = rgb(background);
    if (!fg || !bg) return 0;
    const a = luminance(fg); const b = luminance(bg);
    return (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
}
export function readableTextColor(foreground: string, background: string, minimum = 4.5): string {
    const source = rgb(foreground);
    if (!source || !rgb(background) || textContrast(foreground, background) >= minimum) return foreground;
    const target = textContrast('#000000', background) > textContrast('#ffffff', background) ? 0 : 255;
    const mix = (amount: number) => '#' + source.map((channel) => Math.round(channel + (target - channel) * amount).toString(16).padStart(2, '0')).join('');
    let low = 0; let high = 1;
    for (let i = 0; i < 24; i++) { const middle = (low + high) / 2; if (textContrast(mix(middle), background) >= minimum) high = middle; else low = middle; }
    return mix(high);
}
