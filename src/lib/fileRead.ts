import * as FS from 'expo-file-system/legacy';

/**
 * Reads a file the owner attached in chat so Dorothy can use it:
 * - PDF → kept as base64 and sent to Claude as a document block (Claude reads it natively).
 * - Excel(.xlsx/.xls) → turned into CSV-like text with SheetJS.
 * - Word(.docx) → text pulled from the document XML (jszip).
 * - txt/csv/json/md/… → read as plain text.
 * Both libraries (xlsx, jszip) are loaded lazily so a problem in one can't break the app.
 */

export type Attached =
  | { kind: 'pdf'; name: string; base64: string }
  | { kind: 'text'; name: string; text: string };

const MAX_TEXT = 200000; // 너무 긴 파일은 잘라 토큰·비용을 아낀다
const MAX_PDF_B64 = 14 * 1024 * 1024; // 약 10MB PDF

export async function readAttachment(uri: string, name: string, mime?: string): Promise<Attached> {
  const lower = (name || '').toLowerCase();
  const isPdf = mime === 'application/pdf' || lower.endsWith('.pdf');

  if (isPdf) {
    const base64 = await FS.readAsStringAsync(uri, { encoding: FS.EncodingType.Base64 });
    if (base64.length > MAX_PDF_B64) throw new Error('PDF가 너무 커요(약 10MB까지). 필요한 페이지만 잘라서 올려 주세요.');
    return { kind: 'pdf', name, base64 };
  }

  if (/\.(xlsx|xls|xlsm|xlsb)$/.test(lower)) {
    const base64 = await FS.readAsStringAsync(uri, { encoding: FS.EncodingType.Base64 });
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const XLSX = require('xlsx');
    const wb = XLSX.read(base64, { type: 'base64' });
    const parts: string[] = [];
    for (const sn of wb.SheetNames as string[]) {
      const csv = XLSX.utils.sheet_to_csv(wb.Sheets[sn]);
      if (csv.trim()) parts.push(`# 시트: ${sn}\n${csv}`);
    }
    return { kind: 'text', name, text: parts.join('\n\n').slice(0, MAX_TEXT) || '(빈 엑셀 파일)' };
  }

  if (/\.docx$/.test(lower)) {
    const base64 = await FS.readAsStringAsync(uri, { encoding: FS.EncodingType.Base64 });
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const JSZip = require('jszip');
    const zip = await JSZip.loadAsync(base64, { base64: true });
    const xml: string = (await zip.file('word/document.xml')?.async('string')) ?? '';
    const text = xml
      .replace(/<\/w:p>/g, '\n')
      .replace(/<w:tab[^>]*\/>/g, '\t')
      .replace(/<[^>]+>/g, '')
      .replace(/&amp;/g, '&')
      .replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>')
      .replace(/&quot;/g, '"')
      .replace(/\n{3,}/g, '\n\n')
      .trim();
    return { kind: 'text', name, text: text.slice(0, MAX_TEXT) || '(워드에서 읽을 글자가 없어요)' };
  }

  // 그 밖(txt, csv, json, md, 코드 등)은 평문으로 읽는다.
  const text = await FS.readAsStringAsync(uri, { encoding: FS.EncodingType.UTF8 }).catch(() => '');
  return { kind: 'text', name, text: (text || '').slice(0, MAX_TEXT) || '(파일을 글자로 읽지 못했어요. PDF면 그대로 올려 주세요.)' };
}
