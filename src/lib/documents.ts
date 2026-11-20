import * as Print from 'expo-print';
import * as Sharing from 'expo-sharing';
import * as FS from 'expo-file-system/legacy';

/**
 * Lets Dorothy turn an answer into a real file the owner can send: a PDF, an Excel sheet, or a Word
 * document, then opens the share sheet (카카오톡·메일·저장 …). PDF uses expo-print; Excel uses SheetJS;
 * Word uses docx — both pure-JS libraries loaded lazily so a problem in one never breaks the app.
 */

export type FileFormat = 'pdf' | 'word' | 'excel';

/** A safe, readable file name from the title. */
function safeName(title: string, ext: string): string {
  const base = (title || '문서').replace(/[\\/:*?"<>|\n\r\t]+/g, ' ').trim().slice(0, 60) || '문서';
  return `${base}.${ext}`;
}

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
/** **bold** inside a line → <strong>, after escaping. */
const inlineHtml = (s: string) => esc(s).replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');

type Block =
  | { t: 'h'; level: number; text: string }
  | { t: 'li'; text: string }
  | { t: 'p'; text: string }
  | { t: 'table'; rows: string[][] }
  | { t: 'space' };

/** A tiny Markdown subset → blocks (headings, bullets, | tables |, paragraphs). */
function parseBlocks(md: string): Block[] {
  const lines = (md || '').replace(/\r/g, '').split('\n');
  const blocks: Block[] = [];
  let i = 0;
  const isTableRow = (l: string) => /^\s*\|.*\|\s*$/.test(l);
  const isSep = (l: string) => /^\s*\|?[\s:|-]+\|?\s*$/.test(l) && l.includes('-');
  while (i < lines.length) {
    const line = lines[i];
    if (line.trim() === '') { blocks.push({ t: 'space' }); i++; continue; }
    if (isTableRow(line)) {
      const rows: string[][] = [];
      while (i < lines.length && isTableRow(lines[i])) {
        if (!isSep(lines[i])) {
          rows.push(lines[i].trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim()));
        }
        i++;
      }
      if (rows.length) blocks.push({ t: 'table', rows });
      continue;
    }
    const h = line.match(/^(#{1,3})\s+(.*)$/);
    if (h) { blocks.push({ t: 'h', level: h[1].length, text: h[2].trim() }); i++; continue; }
    const li = line.match(/^\s*[-•*]\s+(.*)$/);
    if (li) { blocks.push({ t: 'li', text: li[1].trim() }); i++; continue; }
    blocks.push({ t: 'p', text: line.trim() });
    i++;
  }
  return blocks;
}

// ───────────────────────── PDF (expo-print) ─────────────────────────

function blocksToHtml(title: string, blocks: Block[]): string {
  const parts: string[] = [];
  let openList = false;
  const closeList = () => { if (openList) { parts.push('</ul>'); openList = false; } };
  for (const b of blocks) {
    if (b.t !== 'li') closeList();
    if (b.t === 'h') parts.push(`<h${b.level}>${inlineHtml(b.text)}</h${b.level}>`);
    else if (b.t === 'li') { if (!openList) { parts.push('<ul>'); openList = true; } parts.push(`<li>${inlineHtml(b.text)}</li>`); }
    else if (b.t === 'p') parts.push(`<p>${inlineHtml(b.text)}</p>`);
    else if (b.t === 'space') parts.push('');
    else if (b.t === 'table') {
      const [head, ...body] = b.rows;
      parts.push('<table>');
      if (head) parts.push('<thead><tr>' + head.map((c) => `<th>${inlineHtml(c)}</th>`).join('') + '</tr></thead>');
      parts.push('<tbody>' + body.map((r) => '<tr>' + r.map((c) => `<td>${inlineHtml(c)}</td>`).join('') + '</tr>').join('') + '</tbody>');
      parts.push('</table>');
    }
  }
  closeList();
  return `<!DOCTYPE html><html><head><meta charset="utf-8" />
  <style>
    * { box-sizing: border-box; }
    body { font-family: -apple-system, 'Noto Sans KR', 'Malgun Gothic', sans-serif; color: #1a1a1a; padding: 32px; font-size: 14px; line-height: 1.6; }
    h1 { font-size: 22px; color: #1F4E79; margin: 0 0 4px; }
    h2 { font-size: 17px; color: #1F4E79; margin: 18px 0 6px; }
    h3 { font-size: 15px; margin: 14px 0 4px; }
    p { margin: 6px 0; }
    ul { margin: 6px 0 6px 18px; padding: 0; }
    li { margin: 3px 0; }
    table { border-collapse: collapse; width: 100%; margin: 10px 0; }
    th, td { border: 1px solid #d0d7de; padding: 7px 10px; text-align: left; font-size: 13px; }
    th { background: #eaf1f8; color: #1F4E79; }
  </style></head><body>${parts.join('\n')}</body></html>`;
}

async function makePdf(title: string, markdown: string): Promise<string> {
  const html = blocksToHtml(title, parseBlocks(markdown));
  const { uri } = await Print.printToFileAsync({ html });
  // Rename the temp file to a friendly name so the share sheet shows a sensible title.
  const dest = (FS.cacheDirectory ?? '') + encodeURIComponent(safeName(title, 'pdf'));
  try { await FS.deleteAsync(dest, { idempotent: true }); await FS.moveAsync({ from: uri, to: dest }); return dest; }
  catch { return uri; }
}

// ───────────────────────── Excel (SheetJS) ─────────────────────────

async function makeExcel(title: string, headers: string[], rows: string[][]): Promise<string> {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const XLSX = require('xlsx');
  const aoa = [headers, ...rows];
  const ws = XLSX.utils.aoa_to_sheet(aoa);
  // Reasonable column widths from the longest cell in each column.
  ws['!cols'] = headers.map((_, c) => ({ wch: Math.min(40, Math.max(10, ...aoa.map((r) => String(r[c] ?? '').length + 2))) }));
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, (title || 'Sheet1').slice(0, 28));
  const base64 = XLSX.write(wb, { type: 'base64', bookType: 'xlsx' });
  const dest = (FS.cacheDirectory ?? '') + encodeURIComponent(safeName(title, 'xlsx'));
  await FS.writeAsStringAsync(dest, base64, { encoding: FS.EncodingType.Base64 });
  return dest;
}

// ───────────────────────── Word (docx) ─────────────────────────

async function makeWord(title: string, markdown: string): Promise<string> {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const docx = require('docx');
  const { Document, Packer, Paragraph, TextRun, HeadingLevel, Table, TableRow, TableCell, WidthType, BorderStyle } = docx;

  const runs = (text: string) => {
    // Split **bold** spans into TextRuns.
    const out: unknown[] = [];
    const re = /\*\*([^*]+)\*\*/g;
    let last = 0; let m: RegExpExecArray | null;
    while ((m = re.exec(text))) {
      if (m.index > last) out.push(new TextRun(text.slice(last, m.index)));
      out.push(new TextRun({ text: m[1], bold: true }));
      last = re.lastIndex;
    }
    if (last < text.length) out.push(new TextRun(text.slice(last)));
    return out.length ? out : [new TextRun(text)];
  };

  const children: unknown[] = [];
  for (const b of parseBlocks(markdown)) {
    if (b.t === 'h') children.push(new Paragraph({ heading: b.level === 1 ? HeadingLevel.HEADING_1 : b.level === 2 ? HeadingLevel.HEADING_2 : HeadingLevel.HEADING_3, children: runs(b.text) }));
    else if (b.t === 'li') children.push(new Paragraph({ bullet: { level: 0 }, children: runs(b.text) }));
    else if (b.t === 'p') children.push(new Paragraph({ children: runs(b.text) }));
    else if (b.t === 'space') children.push(new Paragraph({ children: [new TextRun('')] }));
    else if (b.t === 'table') {
      const border = { style: BorderStyle.SINGLE, size: 6, color: 'D0D7DE' };
      const borders = { top: border, bottom: border, left: border, right: border };
      children.push(new Table({
        width: { size: 100, type: WidthType.PERCENTAGE },
        rows: b.rows.map((r, ri) => new TableRow({
          children: r.map((c) => new TableCell({ borders, children: [new Paragraph({ children: [new TextRun({ text: c, bold: ri === 0 })] })] })),
        })),
      }));
    }
  }
  if (!children.length) children.push(new Paragraph({ children: [new TextRun('')] }));

  const doc = new Document({ sections: [{ children }] });
  const base64: string = await Packer.toBase64String(doc);
  const dest = (FS.cacheDirectory ?? '') + encodeURIComponent(safeName(title, 'docx'));
  await FS.writeAsStringAsync(dest, base64, { encoding: FS.EncodingType.Base64 });
  return dest;
}

// ───────────────────────── Share ─────────────────────────

const MIME: Record<FileFormat, { mime: string; uti: string }> = {
  pdf: { mime: 'application/pdf', uti: 'com.adobe.pdf' },
  excel: { mime: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', uti: 'org.openxmlformats.spreadsheetml.sheet' },
  word: { mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', uti: 'org.openxmlformats.wordprocessingml.document' },
};

/**
 * Writes a base64 image to a persistent local file and returns its uri, so a generated image can be
 * shown in chat (via an ```image block) without storing the big base64 in the chat history itself.
 */
export async function saveBase64Image(base64: string, mime = 'image/png'): Promise<string> {
  const ext = mime.includes('jpeg') || mime.includes('jpg') ? 'jpg' : mime.includes('webp') ? 'webp' : 'png';
  const dir = (FS.documentDirectory ?? FS.cacheDirectory ?? '') + 'dorothy-images/';
  await FS.makeDirectoryAsync(dir, { intermediates: true }).catch(() => {});
  const uri = `${dir}img-${Date.now()}-${Math.floor(Math.random() * 1e6)}.${ext}`;
  await FS.writeAsStringAsync(uri, base64, { encoding: FS.EncodingType.Base64 });
  return uri;
}

/**
 * Builds the requested file and opens the system share sheet. `content` is Markdown (for pdf/word);
 * `headers`/`rows` are the table (for excel). Returns the file name shown to the user.
 */
export async function createAndShareFile(input: {
  format: FileFormat;
  title: string;
  content?: string;
  headers?: string[];
  rows?: string[][];
}): Promise<string> {
  const title = (input.title || '도로시 문서').trim();
  let uri: string;
  let ext: string;
  if (input.format === 'excel') {
    if (!input.headers?.length) throw new Error('엑셀을 만들려면 표의 머리글(headers)과 행(rows)이 필요해요.');
    uri = await makeExcel(title, input.headers, input.rows ?? []);
    ext = 'xlsx';
  } else if (input.format === 'word') {
    uri = await makeWord(title, input.content ?? '');
    ext = 'docx';
  } else {
    uri = await makePdf(title, input.content ?? '');
    ext = 'pdf';
  }
  const name = safeName(title, ext);
  if (await Sharing.isAvailableAsync()) {
    const { mime, uti } = MIME[input.format];
    await Sharing.shareAsync(uri, { mimeType: mime, UTI: uti, dialogTitle: name });
  }
  return name;
}
