// The image row format of a palette sprite and the palette file. Every tool of the pack and the render page import it,
// so it uses no DOM and no Node API.

/**
 * The characters of the palette indices in an image row: the character at index i is palette color i. `.` is a
 * transparent pixel. A palette has at most 64 colors.
 */
export const IMAGE_PIXELS = '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ#@';

export const IMAGE_TRANSPARENT = '.';

/**
 * The colors of a palette file, as `#rrggbb`, in index order. The text is one of:
 * - a JSON object `{ "colors": [...], "extended": [...] }`: the colors, then the extended colors;
 * - a JSON list of colors;
 * - a GIMP palette (`GIMP Palette` on the first line, then `r g b name` rows);
 * - one hex color per line (`.hex`, as Lospec exports it), with or without `#`.
 */
export function parsePaletteText(text: string): string[] {
  const trimmed = text.trim();
  let colors: string[];
  if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
    const data = JSON.parse(trimmed) as string[] | { colors: string[]; extended?: string[] };
    colors = Array.isArray(data) ? data : [...data.colors, ...(data.extended ?? [])];
  } else if (trimmed.startsWith('GIMP Palette')) {
    colors = trimmed
      .split(/\r?\n/)
      .map((line) => /^\s*(\d+)\s+(\d+)\s+(\d+)/.exec(line))
      .filter((match): match is RegExpExecArray => match !== null)
      .map((match) => `#${[match[1], match[2], match[3]].map((value) => Number(value).toString(16).padStart(2, '0')).join('')}`);
  } else {
    colors = trimmed
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => line !== '' && !line.startsWith(';'))
      .map((line) => (line.startsWith('#') ? line : `#${line}`));
  }
  const bad = colors.find((color) => !/^#[0-9a-fA-F]{6}$/.test(color));
  if (bad !== undefined) throw new Error(`the palette color ${bad} is not #rrggbb`);
  if (colors.length === 0) throw new Error('the palette has no color');
  if (colors.length > IMAGE_PIXELS.length) throw new Error(`the palette has ${colors.length} colors; the image rows hold at most ${IMAGE_PIXELS.length}`);
  return colors.map((color) => color.toLowerCase());
}
