import Anthropic from '@anthropic-ai/sdk';

import type { AppState, StoreActions } from './store';

const MODEL = 'claude-opus-5-5';
const BETAS: Anthropic.AnthropicBeta[] = ['server-side-fallback-2026-07-01'];
const HISTORY_TURNS = 40;
const MAX_TOOL_ROUNDS = 8;

const PERSONA = `당신은 "도로시"입니다. 한 사람만을 위한 개인 비서로, 영화 속 자비스처럼 사용자의 삶을 잘 알고 먼저 챙겨 줍니다.

원칙:
- 한국어로, 따뜻하지만 간결하게 답합니다. 모바일 화면이니 핵심부터 말하고 긴 서론은 생략합니다.
- 사용자가 자신에 대해 오래 유효한 사실(가족, 건강, 취향, 일, 목표, 중요한 날짜 등)을 말하면 remember 도구로 저장합니다. 이미 기억 목록에 있는 내용은 다시 저장하지 않습니다. 사실이 바뀌면 이전 기억을 forget으로 지우고 새로 저장합니다.
- 해야 할 일이나 약속이 언급되면 add_task로 등록합니다. 시각이 정해져 있으면 due_at을 넣어 알림이 울리게 합니다. "내일 아침" 같은 표현은 아래 현재 시각 기준으로 해석하고, 애매하면 합리적으로 정한 뒤 정한 시각을 알려 줍니다.
- 완료했다고 하면 complete_task로 체크합니다.
- 최신 정보(뉴스, 날씨, 가격, 영업시간 등)가 필요하면 web_search로 확인하고, 출처가 된 사이트를 짧게 언급합니다.
- 사용자가 놓치고 있는 것(기한이 지난 일, 곧 다가오는 기념일, 기억 속 목표와 관련된 일)이 보이면 대화 흐름을 해치지 않는 선에서 한 줄로 짚어 줍니다.
- 기억과 할 일 목록은 사용자의 개인 정보입니다. 답변에 필요한 만큼만 사용합니다.`;

const TOOLS: Anthropic.Beta.BetaToolUnion[] = [
  {
    name: 'remember',
    description: '사용자에 대해 오래 기억할 사실을 저장한다. 한 번에 하나의 사실만 짧은 문장으로 저장한다.',
    strict: true,
    input_schema: {
      type: 'object',
      properties: {
        category: { type: 'string', description: '짧은 분류. 예: 가족, 건강, 취향, 일, 목표, 기념일, 기타' },
        content: { type: 'string', description: '저장할 사실. 예: "딸 이름은 서윤이고 2019년 3월 2일생"' },
      },
      required: ['category', 'content'],
      additionalProperties: false,
    },
  },
  {
    name: 'forget',
    description: '더 이상 맞지 않거나 사용자가 지우길 원하는 기억을 삭제한다.',
    strict: true,
    input_schema: {
      type: 'object',
      properties: { memory_id: { type: 'string', description: '기억 목록에 표시된 id' } },
      required: ['memory_id'],
      additionalProperties: false,
    },
  },
  {
    name: 'add_task',
    description: '할 일 또는 일정을 등록한다. due_at이 있으면 그 시각에 휴대폰 알림이 울린다.',
    strict: true,
    input_schema: {
      type: 'object',
      properties: {
        title: { type: 'string' },
        due_at: {
          type: ['string', 'null'],
          description: '현지 시간대 오프셋을 포함한 ISO 8601 시각(예: 2026-10-10T08:00:00+09:00). 기한이 없으면 null.',
        },
        notes: { type: ['string', 'null'] },
      },
      required: ['title', 'due_at', 'notes'],
      additionalProperties: false,
    },
  },
  {
    name: 'complete_task',
    description: '할 일을 완료 처리한다.',
    strict: true,
    input_schema: {
      type: 'object',
      properties: { task_id: { type: 'string', description: '할 일 목록에 표시된 id' } },
      required: ['task_id'],
      additionalProperties: false,
    },
  },
  { type: 'web_search_20260209', name: 'web_search', max_uses: 5 },
];

function formatNow(): string {
  const now = new Date();
  const offsetMin = -now.getTimezoneOffset();
  const sign = offsetMin >= 0 ? '+' : '-';
  const pad = (n: number) => String(Math.abs(n)).padStart(2, '0');
  const offset = `${sign}${pad(Math.trunc(offsetMin / 60))}:${pad(offsetMin % 60)}`;
  const local = now.toLocaleString('ko-KR', { dateStyle: 'full', timeStyle: 'short' });
  return `${local} (UTC${offset}, 시간대 ${Intl.DateTimeFormat().resolvedOptions().timeZone})`;
}

/** Everything Dorothy knows right now. Sent as a second system block so the persona block stays cacheable. */
export function buildContext(state: AppState): string {
  const { profile, memories, tasks } = state;
  const now = Date.now();
  const open = tasks.filter((t) => !t.done);
  const taskLine = (t: (typeof tasks)[number]) => {
    const due = t.dueAt ? ` (기한 ${new Date(t.dueAt).toLocaleString('ko-KR')}${new Date(t.dueAt).getTime() < now ? ', 지남' : ''})` : '';
    return `- [${t.id}] ${t.title}${due}${t.notes ? ` — ${t.notes}` : ''}`;
  };
  const recentlyDone = tasks.filter((t) => t.done).slice(-5);

  return [
    `현재 시각: ${formatNow()}`,
    `사용자 이름: ${profile.name || '(아직 모름)'}`,
    profile.about ? `사용자 자기소개:\n${profile.about}` : '',
    `기억 목록 (${memories.length}개):\n${memories.map((m) => `- [${m.id}] (${m.category}) ${m.content}`).join('\n') || '(없음)'}`,
    `진행 중인 할 일 (${open.length}개):\n${open.map(taskLine).join('\n') || '(없음)'}`,
    recentlyDone.length ? `최근 완료한 일:\n${recentlyDone.map(taskLine).join('\n')}` : '',
  ]
    .filter(Boolean)
    .join('\n\n');
}

async function runTool(name: string, input: Record<string, unknown>, actions: StoreActions): Promise<string> {
  switch (name) {
    case 'remember': {
      const m = actions.addMemory(String(input.category), String(input.content));
      return `저장됨 (id ${m.id})`;
    }
    case 'forget':
      return actions.deleteMemory(String(input.memory_id)) ? '삭제됨' : '해당 id의 기억이 없음';
    case 'add_task': {
      const dueAt = typeof input.due_at === 'string' && !Number.isNaN(Date.parse(input.due_at)) ? new Date(input.due_at).toISOString() : undefined;
      const t = await actions.addTask({
        title: String(input.title),
        dueAt,
        notes: typeof input.notes === 'string' ? input.notes : undefined,
      });
      const alarm = t.dueAt ? (t.notificationId ? ', 알림 예약됨' : ', 알림은 예약하지 못함(과거 시각이거나 알림 권한 없음)') : '';
      return `등록됨 (id ${t.id}${alarm})`;
    }
    case 'complete_task':
      return (await actions.setTaskDone(String(input.task_id), true)) ? '완료 처리됨' : '해당 id의 할 일이 없음';
    default:
      throw new Error(`알 수 없는 도구: ${name}`);
  }
}

function createClient(apiKey: string) {
  // The key lives only on this device (SecureStore). Fine for a personal app; put a backend in front before sharing it.
  return new Anthropic({ apiKey, dangerouslyAllowBrowser: true });
}

function textOf(content: Anthropic.Beta.BetaContentBlock[]): string {
  return content
    .filter((b): b is Anthropic.Beta.BetaTextBlock => b.type === 'text')
    .map((b) => b.text)
    .join('')
    .trim();
}

export function describeError(error: unknown): string {
  if (error instanceof Anthropic.AuthenticationError) return 'API 키가 올바르지 않아요. 설정에서 키를 확인해 주세요.';
  if (error instanceof Anthropic.RateLimitError) return '요청이 너무 많아요. 잠시 후 다시 시도해 주세요.';
  if (error instanceof Anthropic.APIConnectionError) return '네트워크에 연결할 수 없어요.';
  if (error instanceof Anthropic.APIError) return `오류가 발생했어요 (${error.status}): ${error.message}`;
  return error instanceof Error ? error.message : String(error);
}

/**
 * One user turn: sends the text-only history plus the new message, runs Dorothy's tools until she
 * finishes, and returns her reply. Within the turn the message list is append-only, so thinking
 * blocks stay valid; only the final text is persisted between turns.
 */
export async function chat(userText: string, state: AppState, actions: StoreActions): Promise<string> {
  const client = createClient(state.apiKey);
  const history = state.chat.slice(-HISTORY_TURNS);
  while (history.length && history[0].role !== 'user') history.shift();

  const messages: Anthropic.Beta.BetaMessageParam[] = [
    ...history.map((t) => ({ role: t.role, content: t.text })),
    { role: 'user', content: userText },
  ];
  const system: Anthropic.Beta.BetaTextBlockParam[] = [
    { type: 'text', text: PERSONA, cache_control: { type: 'ephemeral' } },
    { type: 'text', text: buildContext(state) },
  ];

  const replies: string[] = [];
  for (let round = 0; round < MAX_TOOL_ROUNDS; round++) {
    const response = await client.beta.messages.create({
      model: MODEL,
      max_tokens: 16000,
      betas: BETAS,
      fallbacks: 'default',
      output_config: { effort: 'medium' },
      system,
      tools: TOOLS,
      messages,
    });

    if (response.stop_reason === 'refusal') {
      return '죄송해요, 이 요청은 도와드리기 어려워요.';
    }
    const text = textOf(response.content);
    if (text) replies.push(text);
    messages.push({ role: 'assistant', content: response.content });

    if (response.stop_reason === 'pause_turn') continue;
    if (response.stop_reason !== 'tool_use') break;

    const toolResults: Anthropic.Beta.BetaToolResultBlockParam[] = [];
    for (const block of response.content) {
      if (block.type !== 'tool_use') continue;
      try {
        const content = await runTool(block.name, block.input as Record<string, unknown>, actions);
        toolResults.push({ type: 'tool_result', tool_use_id: block.id, content });
      } catch (e) {
        toolResults.push({ type: 'tool_result', tool_use_id: block.id, content: describeError(e), is_error: true });
      }
    }
    messages.push({ role: 'user', content: toolResults });
  }

  return replies.join('\n\n') || '(응답이 비어 있어요. 다시 말씀해 주세요.)';
}

/** A short morning briefing built from tasks and memories, with live info (weather, news) when useful. */
export async function briefing(state: AppState): Promise<string> {
  const client = createClient(state.apiKey);
  const messages: Anthropic.Beta.BetaMessageParam[] = [
    {
      role: 'user',
      content:
        '오늘의 브리핑을 해 줘. 오늘 꼭 할 일과 기한이 지난 일, 이번 주에 다가오는 일, 기억 목록에서 오늘 챙기면 좋을 것(기념일, 건강, 목표 등)을 정리해 줘. ' +
        '내 사는 곳이 기억에 있으면 오늘 날씨도 찾아 줘. 짧은 소제목과 글머리표로, 휴대폰 한 화면에 들어오게.',
    },
  ];
  const system: Anthropic.Beta.BetaTextBlockParam[] = [
    { type: 'text', text: PERSONA, cache_control: { type: 'ephemeral' } },
    { type: 'text', text: buildContext(state) },
  ];

  const parts: string[] = [];
  for (let round = 0; round < 4; round++) {
    const response = await client.beta.messages.create({
      model: MODEL,
      max_tokens: 8000,
      betas: BETAS,
      fallbacks: 'default',
      output_config: { effort: 'low' },
      system,
      tools: [{ type: 'web_search_20260209', name: 'web_search', max_uses: 3 }],
      messages,
    });
    if (response.stop_reason === 'refusal') return '브리핑을 만들지 못했어요.';
    const text = textOf(response.content);
    if (text) parts.push(text);
    if (response.stop_reason !== 'pause_turn') return parts.join('\n\n');
    messages.push({ role: 'assistant', content: response.content });
  }
  return '브리핑을 만드는 데 시간이 너무 오래 걸렸어요. 다시 시도해 주세요.';
}
