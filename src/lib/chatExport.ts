import type { InboxMessage } from './inbox';

/**
 * Parses chat export files: KakaoTalk "대화 내용 내보내기" (mobile and PC formats) and WhatsApp
 * "Export chat". Lines that don't start a new message continue the previous one.
 */
export function parseChatExport(text: string, fileName = ''): { app: string; conversation: string; messages: InboxMessage[] } {
  const lines = text.replace(/\r/g, '').split('\n');
  const title = /^(.+?)(?: \d+)? 님과 카카오톡 대화/.exec(lines[0] ?? '')?.[1];
  const conversation = title ?? fileName.replace(/\.(txt|csv)$/i, '').replace(/^KakaoTalk_(Chat_)?/i, '');
  const messages: InboxMessage[] = [];
  let app = '카카오톡';
  let day: [number, number, number] | null = null;

  const time = (y: number, mo: number, d: number, ampm: string | undefined, h: number, mi: number) => {
    if (ampm === '오후' || ampm === 'PM') h = h % 12 + 12;
    else if (ampm === '오전' || ampm === 'AM') h = h % 12;
    return new Date(y, mo - 1, d, h, mi).getTime();
  };
  const add = (t: number, sender: string, body: string) => messages.push({ time: t, app, conversation, sender: sender.trim(), text: body });

  for (const line of lines) {
    let m: RegExpExecArray | null;
    // Mobile: "2024년 10월 11일 오후 3:24, 홍길동 : 안녕" or "2024. 10. 11. 오후 3:24, 홍길동 : 안녕"
    if ((m = /^(\d{4})(?:년 |\. )(\d{1,2})(?:월 |\. )(\d{1,2})(?:일|\.)\s+(오전|오후) (\d{1,2}):(\d{2}), (.+?) : (.*)$/.exec(line))) {
      add(time(+m[1], +m[2], +m[3], m[4], +m[5], +m[6]), m[7], m[8]);
      continue;
    }
    // System lines ("홍길동님이 들어왔습니다") carry a date but no sender.
    if (/^\d{4}(년 |\. )\d{1,2}(월 |\. )\d{1,2}(일|\.)\s+(오전|오후) \d{1,2}:\d{2}(,|$)/.test(line)) continue;
    // PC: "--------------- 2024년 10월 11일 금요일 ---------------" then "[홍길동] [오후 3:24] 안녕"
    if ((m = /^-+ (\d{4})년 (\d{1,2})월 (\d{1,2})일/.exec(line))) {
      day = [+m[1], +m[2], +m[3]];
      continue;
    }
    if (day && (m = /^\[(.+?)\] \[(오전|오후) (\d{1,2}):(\d{2})\] (.*)$/.exec(line))) {
      add(time(...day, m[2], +m[3], +m[4]), m[1], m[5]);
      continue;
    }
    // WhatsApp: "10/11/24, 3:24 PM - Name: Hello" (month first, as on Philippine phones, unless the first number is over 12)
    if ((m = /^(\d{1,2})\/(\d{1,2})\/(\d{2,4}),? (\d{1,2}):(\d{2})(?:\s?([AP]M))? - (.+?): (.*)$/i.exec(line))) {
      app = 'WhatsApp';
      const [a, b] = [+m[1], +m[2]];
      const [mo, d] = a > 12 ? [b, a] : [a, b];
      const y = +m[3] < 100 ? 2000 + +m[3] : +m[3];
      add(time(y, mo, d, m[6]?.toUpperCase(), +m[4], +m[5]), m[7], m[8]);
      continue;
    }
    const last = messages[messages.length - 1];
    if (last && line.trim() && !/^저장한 날짜|님과 카카오톡 대화$/.test(line)) last.text += `\n${line}`;
  }
  return { app, conversation, messages: messages.filter((x) => x.text.trim() && !Number.isNaN(x.time)) };
}
