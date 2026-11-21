import AsyncStorage from '@react-native-async-storage/async-storage';
import * as SecureStore from 'expo-secure-store';
import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { Platform } from 'react-native';

import { cancelReminder, scheduleReminder, scheduleRepeatingReminder } from './notifications';
import { setCompanyConfig } from './company';
import { setSpeechRate, setSpeechVoice } from './voice';
import type { ModelChoice } from './assistant';
import type { ServerBrainConfig } from './serverBrain';
import type { ChatTurn, Expense, Followup, Memory, PricingProfile, Profile, Routine, Sailing, SavedDoc, Task, TodoCandidate } from './types';

const KEYS = {
  tasks: 'dorothy.tasks',
  followups: 'dorothy.followups',
  memories: 'dorothy.memories',
  profile: 'dorothy.profile',
  chat: 'dorothy.chat',
  briefingHour: 'dorothy.briefingHour',
  voiceOnOpen: 'dorothy.voiceOnOpen',
  workspaceId: 'dorothy.workspaceId',
  model: 'dorothy.model',
  planHour: 'dorothy.planHour',
  speakerMatch: 'dorothy.speakerMatch',
  voiceId: 'dorothy.voiceId',
  companyUrl: 'dorothy.companyUrl',
  weeklyReportDay: 'dorothy.weeklyReportDay',
  speechRate: 'dorothy.speechRate',
  routines: 'dorothy.routines',
  standbyShortcut: 'dorothy.standbyShortcut',
  voiceHiAccuracy: 'dorothy.voiceHiAccuracy',
  sailings: 'dorothy.sailings',
  todoRooms: 'dorothy.todoRooms',
  fxRooms: 'dorothy.fxRooms',
  expenses: 'dorothy.expenses',
  todoCandidates: 'dorothy.todoCandidates',
  chatSummary: 'dorothy.chatSummary',
  chatSummaryUpto: 'dorothy.chatSummaryUpto',
  autoUpgrade: 'dorothy.autoUpgrade',
  serverBrain: 'dorothy.serverBrain',
  documents: 'dorothy.documents',
  pricing: 'dorothy.pricing',
};
const API_KEY = 'dorothy.anthropicApiKey';
const COMPANY_TOKEN = 'dorothy.companyToken';

export type ImportBundle = {
  profile?: { name?: string; about?: string };
  memories?: { category?: string; content: string }[];
  tasks?: { title: string; dueAt?: string; notes?: string }[];
};

export type AppState = {
  tasks: Task[];
  /** Replies to send / requests to handle, tracked from messages & calls. */
  followups: Followup[];
  /** Recurring reminders (daily/weekly) the owner set, e.g. "매일 저녁 8시 개인톡 미회신 확인". */
  routines: Routine[];
  memories: Memory[];
  profile: Profile;
  chat: ChatTurn[];
  apiKey: string;
  /** Needed only for organization-level API keys that aren't scoped to a workspace. */
  workspaceId: string;
  /** Which Claude model answers: cheap Haiku by default, Opus for harder questions. */
  model: ModelChoice;
  /** Hour (0-23) of the daily briefing notification, or null when off. */
  briefingHour: number | null;
  /** Hour (0-23) of the nightly "plan tomorrow" reminder, or null when off. */
  planHour: number | null;
  /** In standby, only act on the owner's voice (needs a voiceprint enrolled in Settings). */
  speakerMatch: boolean;
  /** Chosen system TTS voice id for Dorothy's spoken replies ('' = phone default). */
  voiceId: string;
  /** How fast Dorothy speaks (1.0 = normal). */
  speechRate: number;
  /** What happens when the app is opened: nothing, listen once, or enter standby. */
  voiceOnOpen: VoiceOnOpen;
  /** Read-only company-status endpoint (e.g. .../dorothy_report.php). '' = not connected. */
  companyUrl: string;
  /** Secret token for the company endpoint (kept in SecureStore, like the API key). */
  companyToken: string;
  /** Weekday (expo: 1=Sun … 7=Sat) for the weekly business report, or null when off. */
  weeklyReportDay: number | null;
  /** Keep a persistent lock-screen notification that opens straight into standby. Android only. */
  standbyShortcut: boolean;
  /** 음성 받아쓰기를 회사 Gemini로(정확하지만 조금 느림). 끄면 폰 실시간 인식으로 바로 전송(빠름). */
  voiceHiAccuracy: boolean;
  /** Booking/sailing schedules Dorothy read from the forwarder's photos (shown as calendar/list). */
  sailings: Sailing[];
  /** Room names (partial match) to watch for to-do/requests in KakaoTalk etc. Empty = off. */
  todoRooms: string[];
  /** 단톡방 평균 환율을 뽑을 방(부분 일치). 기본은 사장님이 쓰는 두 환전방. */
  fxRooms: string[];
  /** To-dos Dorothy detected from watched rooms, awaiting the owner's 등록/무시 decision. */
  todoCandidates: TodoCandidate[];
  /** 개인 지출 기록(주로 영수증 사진에서 읽어 저장). */
  expenses: Expense[];
  /** 길어진 대화에서 최근 창(window) 밖으로 밀려난 이전 대화를 접어 둔 요약. 긴 대화에서도 맥락을 잃지 않게 한다. */
  chatSummary: string;
  /** chatSummary에 이미 반영된 앞쪽 대화 턴 수(증분 요약용). */
  chatSummaryUpto: number;
  /** 기본 두뇌(haiku)일 때, 회사·숫자·사진처럼 어려운 질문은 자동으로 고급(Opus)으로 올릴지. 끄면 항상 저렴한 두뇌(비용 절약). */
  autoUpgrade: boolean;
  /** 사장님이 직접 운영하는 서버/컴퓨터의 오픈 모델(OpenAI 호환) 연결 설정. url이 비면 미설정. */
  serverBrain: ServerBrainConfig;
  /** 도로시가 만들어 보관한 견적서·인보이스 등 문서(거래처별로 다시 찾아봄). */
  documents: SavedDoc[];
  /** 즉석 견적용 표준 단가(운임·면장·픽업·배송·부가세). */
  pricing: PricingProfile;
};

/** 사장님 실제 요율 기준 기본값(설정에서 수정 가능). */
export const DEFAULT_PRICING: PricingProfile = {
  cbmOnce: 300000,
  kgOnce: 1500,
  cbmRegular: 270000,
  kgRegular: 1400,
  exportDecl: 50000,
  koreaPickup: 135000,
  phDelivery: 20000,
  vatKoreaPct: 10,
  vatPhPct: 12,
  note: '',
};

export type VoiceOnOpen = 'off' | 'listen' | 'standby';

const initialState: AppState = {
  tasks: [],
  followups: [],
  routines: [],
  memories: [],
  profile: { name: '', about: '' },
  chat: [],
  apiKey: '',
  workspaceId: '',
  model: 'haiku',
  briefingHour: null,
  planHour: null,
  speakerMatch: false,
  voiceId: '',
  speechRate: 1.05,
  voiceOnOpen: 'off',
  companyUrl: '',
  companyToken: '',
  weeklyReportDay: null,
  standbyShortcut: false,
  voiceHiAccuracy: true,
  sailings: [],
  todoRooms: [],
  fxRooms: ['손대손', '환전/호텔방'],
  expenses: [],
  todoCandidates: [],
  chatSummary: '',
  chatSummaryUpto: 0,
  autoUpgrade: true,
  serverBrain: { url: '', model: '', key: '' },
  documents: [],
  pricing: DEFAULT_PRICING,
};

export const newId = () => `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

/** 서류마감·반입마감·출항일 '하루 전 오전 9시' 로컬 알림을 예약하고 그 id들을 돌려준다(지난 날짜는 건너뜀). */
async function scheduleSailingReminders(s: Sailing): Promise<string[]> {
  const ids: string[] = [];
  const plan: { label: string; date?: string }[] = [
    { label: '서류마감', date: s.docCutoff },
    { label: '반입마감', date: s.cyCutoff },
    { label: '출항(ETD)', date: s.etd },
  ];
  for (const p of plan) {
    if (!p.date || !/^\d{4}-\d{2}-\d{2}$/.test(p.date)) continue;
    const remind = new Date(`${p.date}T09:00:00`);
    remind.setDate(remind.getDate() - 1);
    if (remind.getTime() <= Date.now()) continue;
    const route = [s.from, s.to].filter(Boolean).join('→');
    const title = `🚢 ${s.carrier}${s.voyageNo ? ' ' + s.voyageNo : ''} ${p.label} 하루 전`;
    const body = `${p.label} ${p.date}${route ? ` · ${route}` : ''}`;
    const id = await scheduleReminder(title, body, remind).catch(() => undefined);
    if (id) ids.push(id);
  }
  return ids;
}

// SecureStore is native-only; fall back to AsyncStorage so the web preview still works.
const secret = {
  get: (k: string) => (Platform.OS === 'web' ? AsyncStorage.getItem(k) : SecureStore.getItemAsync(k)),
  set: (k: string, v: string) => (Platform.OS === 'web' ? AsyncStorage.setItem(k, v) : SecureStore.setItemAsync(k, v)),
};

function useStoreValue() {
  const [state, setState] = useState<AppState>(initialState);
  const [loaded, setLoaded] = useState(false);
  // The assistant's tool loop runs across several awaits; it reads the latest state through this ref.
  const ref = useRef(state);

  useEffect(() => {
    (async () => {
      const [tasks, followups, memories, profile, chat, hour, apiKey, voiceOnOpen, workspaceId, model, planHour, speakerMatch, voiceId, companyUrl, companyToken, weeklyReportDay, speechRate, routines, standbyShortcut, voiceHiAccuracy, sailings, todoRooms, todoCandidates, fxRooms, expenses, chatSummary, chatSummaryUpto, autoUpgrade, serverBrain, documents, pricing] = await Promise.all([
        AsyncStorage.getItem(KEYS.tasks),
        AsyncStorage.getItem(KEYS.followups),
        AsyncStorage.getItem(KEYS.memories),
        AsyncStorage.getItem(KEYS.profile),
        AsyncStorage.getItem(KEYS.chat),
        AsyncStorage.getItem(KEYS.briefingHour),
        secret.get(API_KEY),
        AsyncStorage.getItem(KEYS.voiceOnOpen),
        AsyncStorage.getItem(KEYS.workspaceId),
        AsyncStorage.getItem(KEYS.model),
        AsyncStorage.getItem(KEYS.planHour),
        AsyncStorage.getItem(KEYS.speakerMatch),
        AsyncStorage.getItem(KEYS.voiceId),
        AsyncStorage.getItem(KEYS.companyUrl),
        secret.get(COMPANY_TOKEN),
        AsyncStorage.getItem(KEYS.weeklyReportDay),
        AsyncStorage.getItem(KEYS.speechRate),
        AsyncStorage.getItem(KEYS.routines),
        AsyncStorage.getItem(KEYS.standbyShortcut),
        AsyncStorage.getItem(KEYS.voiceHiAccuracy),
        AsyncStorage.getItem(KEYS.sailings),
        AsyncStorage.getItem(KEYS.todoRooms),
        AsyncStorage.getItem(KEYS.todoCandidates),
        AsyncStorage.getItem(KEYS.fxRooms),
        AsyncStorage.getItem(KEYS.expenses),
        AsyncStorage.getItem(KEYS.chatSummary),
        AsyncStorage.getItem(KEYS.chatSummaryUpto),
        AsyncStorage.getItem(KEYS.autoUpgrade),
        AsyncStorage.getItem(KEYS.serverBrain),
        AsyncStorage.getItem(KEYS.documents),
        AsyncStorage.getItem(KEYS.pricing),
      ]);
      ref.current = {
        tasks: tasks ? JSON.parse(tasks) : [],
        followups: followups ? JSON.parse(followups) : [],
        memories: memories ? JSON.parse(memories) : [],
        profile: profile ? JSON.parse(profile) : initialState.profile,
        chat: chat ? JSON.parse(chat) : [],
        briefingHour: hour ? Number(hour) : null,
        planHour: planHour ? JSON.parse(planHour) : null,
        speakerMatch: speakerMatch ? JSON.parse(speakerMatch) : false,
        voiceId: voiceId ? JSON.parse(voiceId) : '',
        voiceOnOpen: voiceOnOpen ? JSON.parse(voiceOnOpen) : 'off',
        apiKey: apiKey ?? '',
        workspaceId: workspaceId ? JSON.parse(workspaceId) : '',
        model: model ? JSON.parse(model) : 'haiku',
        companyUrl: companyUrl ? JSON.parse(companyUrl) : '',
        companyToken: companyToken ?? '',
        weeklyReportDay: weeklyReportDay ? JSON.parse(weeklyReportDay) : null,
        speechRate: speechRate ? JSON.parse(speechRate) : 1.05,
        routines: routines ? JSON.parse(routines) : [],
        standbyShortcut: standbyShortcut ? JSON.parse(standbyShortcut) : false,
        voiceHiAccuracy: voiceHiAccuracy ? JSON.parse(voiceHiAccuracy) : true,
        sailings: sailings ? JSON.parse(sailings) : [],
        todoRooms: todoRooms ? JSON.parse(todoRooms) : [],
        todoCandidates: todoCandidates ? JSON.parse(todoCandidates) : [],
        fxRooms: fxRooms ? JSON.parse(fxRooms) : initialState.fxRooms,
        expenses: expenses ? JSON.parse(expenses) : [],
        chatSummary: chatSummary ? JSON.parse(chatSummary) : '',
        chatSummaryUpto: chatSummaryUpto ? JSON.parse(chatSummaryUpto) : 0,
        autoUpgrade: autoUpgrade ? JSON.parse(autoUpgrade) : true,
        serverBrain: serverBrain ? JSON.parse(serverBrain) : initialState.serverBrain,
        documents: documents ? JSON.parse(documents) : [],
        pricing: pricing ? { ...DEFAULT_PRICING, ...JSON.parse(pricing) } : DEFAULT_PRICING,
      };
      setSpeechVoice(ref.current.voiceId);
      setSpeechRate(ref.current.speechRate);
      setCompanyConfig(ref.current.companyUrl, ref.current.companyToken);
      setState(ref.current);
      setLoaded(true);
    })();
  }, []);

  const update = useCallback(<K extends keyof AppState>(key: K, value: AppState[K]) => {
    ref.current = { ...ref.current, [key]: value };
    setState(ref.current);
    if (key === 'voiceId') setSpeechVoice(value as string);
    if (key === 'speechRate') setSpeechRate(value as number);
    if (key === 'companyUrl' || key === 'companyToken') setCompanyConfig(ref.current.companyUrl, ref.current.companyToken);
    if (key === 'apiKey') {
      secret.set(API_KEY, value as string);
    } else if (key === 'companyToken') {
      secret.set(COMPANY_TOKEN, value as string);
    } else if (key === 'briefingHour') {
      if (value === null) AsyncStorage.removeItem(KEYS.briefingHour);
      else AsyncStorage.setItem(KEYS.briefingHour, String(value));
    } else {
      AsyncStorage.setItem(KEYS[key as Exclude<keyof AppState, 'apiKey' | 'companyToken'>], JSON.stringify(value));
    }
  }, []);

  const actions = useMemo(() => {
    const a = {
      setProfile: (profile: Profile) => update('profile', profile),
      setApiKey: (key: string) => update('apiKey', key.trim()),
      setWorkspaceId: (id: string) => update('workspaceId', id.trim()),
      setModel: (model: ModelChoice) => update('model', model),
      setBriefingHour: (hour: number | null) => update('briefingHour', hour),
      setPlanHour: (hour: number | null) => update('planHour', hour),
      setSpeakerMatch: (on: boolean) => update('speakerMatch', on),
      setVoiceId: (id: string) => update('voiceId', id),
      setSpeechRate: (rate: number) => update('speechRate', rate),
      setVoiceOnOpen: (mode: VoiceOnOpen) => update('voiceOnOpen', mode),
      setCompanyUrl: (url: string) => update('companyUrl', url.trim()),
      setCompanyToken: (tkn: string) => update('companyToken', tkn.trim()),
      setWeeklyReportDay: (day: number | null) => update('weeklyReportDay', day),
      setStandbyShortcut: (on: boolean) => update('standbyShortcut', on),
      setVoiceHiAccuracy: (on: boolean) => update('voiceHiAccuracy', on),
      setAutoUpgrade: (on: boolean) => update('autoUpgrade', on),
      setServerBrain: (config: ServerBrainConfig) =>
        update('serverBrain', { url: config.url.trim(), model: config.model.trim(), key: config.key.trim() }),

      async addTask(input: { title: string; dueAt?: string; notes?: string }): Promise<Task> {
        const task: Task = { id: newId(), done: false, createdAt: new Date().toISOString(), ...input };
        if (task.dueAt) {
          task.notificationId = await scheduleReminder('⏰ ' + task.title, task.notes ?? '도로시가 알려드려요', new Date(task.dueAt));
        }
        update('tasks', [...ref.current.tasks, task]);
        return task;
      },
      async setTaskDone(id: string, done: boolean): Promise<Task | undefined> {
        const task = ref.current.tasks.find((t) => t.id === id);
        if (!task) return undefined;
        if (done) await cancelReminder(task.notificationId);
        const next = { ...task, done, notificationId: done ? undefined : task.notificationId };
        update('tasks', ref.current.tasks.map((t) => (t.id === id ? next : t)));
        return next;
      },
      async deleteTask(id: string) {
        await cancelReminder(ref.current.tasks.find((t) => t.id === id)?.notificationId);
        update('tasks', ref.current.tasks.filter((t) => t.id !== id));
      },
      /** Edits a task and, when dueAt changes (including snoozing it later or clearing it), reschedules the reminder. */
      async updateTask(id: string, patch: { title?: string; notes?: string; dueAt?: string | null }): Promise<Task | undefined> {
        const task = ref.current.tasks.find((t) => t.id === id);
        if (!task) return undefined;
        let notificationId = task.notificationId;
        let dueAt = task.dueAt;
        if (patch.dueAt !== undefined) {
          await cancelReminder(task.notificationId);
          notificationId = undefined;
          dueAt = patch.dueAt ?? undefined;
          if (dueAt && new Date(dueAt).getTime() > Date.now()) {
            notificationId = await scheduleReminder('⏰ ' + (patch.title ?? task.title), (patch.notes ?? task.notes) ?? '도로시가 알려드려요', new Date(dueAt));
          }
        }
        const next: Task = {
          ...task,
          title: patch.title ?? task.title,
          notes: patch.notes !== undefined ? patch.notes : task.notes,
          dueAt,
          notificationId,
        };
        update('tasks', ref.current.tasks.map((t) => (t.id === id ? next : t)));
        return next;
      },

      /** Creates a recurring reminder (daily, or weekly when weekday is set) and schedules it. */
      async addRoutine(input: { title: string; hour: number; minute: number; repeat: 'daily' | 'weekly'; weekday?: number }): Promise<Routine> {
        const routine: Routine = { id: newId(), createdAt: new Date().toISOString(), ...input };
        routine.notificationId = await scheduleRepeatingReminder(
          '🔁 ' + routine.title,
          '도로시가 챙겨 드려요',
          routine.hour,
          routine.minute,
          routine.repeat === 'weekly' ? routine.weekday : undefined,
        );
        update('routines', [...ref.current.routines, routine]);
        return routine;
      },
      async deleteRoutine(id: string): Promise<boolean> {
        const routine = ref.current.routines.find((r) => r.id === id);
        if (!routine) return false;
        await cancelReminder(routine.notificationId);
        update('routines', ref.current.routines.filter((r) => r.id !== id));
        return true;
      },

      addMemory(category: string, content: string): Memory {
        const memory: Memory = { id: newId(), category, content, createdAt: new Date().toISOString() };
        update('memories', [...ref.current.memories, memory]);
        return memory;
      },
      deleteMemory(id: string): boolean {
        const exists = ref.current.memories.some((m) => m.id === id);
        update('memories', ref.current.memories.filter((m) => m.id !== id));
        return exists;
      },
      /** Fixes a memory in place (wrong fact or category) instead of deleting and re-adding it. */
      updateMemory(id: string, patch: { category?: string; content?: string }): boolean {
        const m = ref.current.memories.find((x) => x.id === id);
        if (!m) return false;
        const next: Memory = { ...m, category: patch.category ?? m.category, content: patch.content ?? m.content };
        update('memories', ref.current.memories.map((x) => (x.id === id ? next : x)));
        return true;
      },

      /** Saves one sailing/booking Dorothy read from a schedule photo (shown on the 스케줄 calendar/list). */
      addSailing(input: Omit<Sailing, 'id' | 'createdAt'>): Sailing {
        // Skip an obvious duplicate (same carrier + voyage + ETD still listed).
        const dup = ref.current.sailings.find(
          (s) => s.carrier === input.carrier && (s.voyageNo ?? '') === (input.voyageNo ?? '') && (s.etd ?? '') === (input.etd ?? ''),
        );
        if (dup) return dup;
        const sailing: Sailing = { id: newId(), createdAt: new Date().toISOString(), source: 'manual', ...input };
        update('sailings', [...ref.current.sailings, sailing]);
        // 서류마감·반입마감·출항일 '하루 전 오전 9시'에 자동 알림 예약(지난 날짜는 건너뜀). 백그라운드로.
        (async () => {
          const ids = await scheduleSailingReminders(sailing);
          if (ids.length) update('sailings', ref.current.sailings.map((x) => (x.id === sailing.id ? { ...x, reminderIds: ids } : x)));
        })().catch(() => {});
        return sailing;
      },
      /**
       * 선사 사이트에서 자동 수집한 선편들을 Sailing에 공급한다(매일). carrier+voyageNo(없으면 선박명/ETD)로
       * 중복을 막아, 같은 선편이 날마다 쌓이거나 알림이 중복되지 않게 한다. ETD·마감이 바뀌면 갱신(알림 재예약),
       * 날짜가 그대로면 선박명·소요일 등만 조용히 갱신. 사진으로 직접 등록한(source='manual') 건은 덮지 않는다.
       */
      async upsertAutoSailings(incoming: Omit<Sailing, 'id' | 'createdAt'>[]): Promise<{ added: number; updated: number; unchanged: number }> {
        const keyOf = (s: { carrier?: string; voyageNo?: string; vessel?: string; etd?: string }) =>
          `${(s.carrier ?? '').trim().toLowerCase()}|${(s.voyageNo || s.vessel || s.etd || '').trim().toLowerCase()}`;
        const cur = [...ref.current.sailings];
        const byKey = new Map<string, number>();
        cur.forEach((s, i) => byKey.set(keyOf(s), i));
        let added = 0, updated = 0, unchanged = 0;
        for (const raw of incoming) {
          const inc: Sailing = { id: newId(), createdAt: new Date().toISOString(), ...raw, source: 'auto' };
          const k = keyOf(inc);
          const idx = byKey.get(k);
          if (idx == null) {
            inc.reminderIds = await scheduleSailingReminders(inc);
            cur.push(inc);
            byKey.set(k, cur.length - 1);
            added++;
          } else {
            const ex = cur[idx];
            if (ex.source === 'manual') { unchanged++; continue; } // 수동 등록은 존중(덮지 않음)
            const datesChanged = (ex.etd ?? '') !== (inc.etd ?? '') || (ex.cyCutoff ?? '') !== (inc.cyCutoff ?? '') || (ex.docCutoff ?? '') !== (inc.docCutoff ?? '');
            if (datesChanged) {
              for (const nid of ex.reminderIds ?? []) await cancelReminder(nid);
              const merged: Sailing = { ...ex, ...raw, id: ex.id, createdAt: ex.createdAt, source: 'auto' };
              merged.reminderIds = await scheduleSailingReminders(merged);
              cur[idx] = merged;
              updated++;
            } else {
              cur[idx] = { ...ex, ...raw, id: ex.id, createdAt: ex.createdAt, source: 'auto', reminderIds: ex.reminderIds };
              unchanged++;
            }
          }
        }
        update('sailings', cur);
        return { added, updated, unchanged };
      },
      async deleteSailing(id: string) {
        const s = ref.current.sailings.find((x) => x.id === id);
        for (const nid of s?.reminderIds ?? []) await cancelReminder(nid);
        update('sailings', ref.current.sailings.filter((x) => x.id !== id));
      },
      async clearSailings() {
        for (const s of ref.current.sailings) for (const nid of s.reminderIds ?? []) await cancelReminder(nid);
        update('sailings', []);
      },

      // ── 견적서·인보이스 보관함 ──
      addDocument(input: Omit<SavedDoc, 'id' | 'createdAt'>): SavedDoc {
        const doc: SavedDoc = { id: newId(), createdAt: new Date().toISOString(), ...input };
        update('documents', [doc, ...ref.current.documents].slice(0, 500));
        return doc;
      },
      deleteDocument(id: string) {
        update('documents', ref.current.documents.filter((d) => d.id !== id));
      },
      /** 최신 보관 문서 목록(도구에서 실시간으로 조회). */
      getDocuments: (): SavedDoc[] => ref.current.documents,

      // ── 즉석 견적용 표준 단가 ──
      setPricing: (p: PricingProfile) => update('pricing', p),
      getPricing: (): PricingProfile => ref.current.pricing,

      // ── 개인 지출 기록(영수증 등) ──
      addExpense(input: Omit<Expense, 'id' | 'createdAt'>): Expense {
        const exp: Expense = { id: newId(), createdAt: new Date().toISOString(), ...input, currency: input.currency || 'PHP' };
        update('expenses', [exp, ...ref.current.expenses].slice(0, 1000));
        return exp;
      },
      deleteExpense(id: string) {
        update('expenses', ref.current.expenses.filter((e) => e.id !== id));
      },

      // ── 단톡방 평균 환율: 환율을 뽑을 방 목록(부분 일치) ──
      setFxRooms(rooms: string[]) {
        update('fxRooms', Array.from(new Set(rooms.map((r) => r.trim()).filter(Boolean))));
      },

      // ── 카톡 할 일 감지: 감지할 방 목록 + 등록 전 후보 ──
      setTodoRooms(rooms: string[]) {
        const clean = Array.from(new Set(rooms.map((r) => r.trim()).filter(Boolean)));
        update('todoRooms', clean);
        // A removed room's pending candidates are dropped too (spec: 방을 끄면 그 기록도 지움).
        update('todoCandidates', ref.current.todoCandidates.filter((c) => clean.some((r) => c.room.includes(r) || r.includes(c.room))));
      },
      /** Adds detected to-dos awaiting approval, skipping ones already pending or clearly duplicated. */
      addTodoCandidates(list: Omit<TodoCandidate, 'id' | 'createdAt'>[]): number {
        const existing = ref.current.todoCandidates;
        const openTasks = ref.current.tasks.filter((t) => !t.done);
        const openFollowups = ref.current.followups.filter((f) => !f.done);
        const fresh: TodoCandidate[] = [];
        for (const c of list) {
          const key = `${c.who}|${c.title}`.toLowerCase();
          const dup =
            existing.some((e) => `${e.who}|${e.title}`.toLowerCase() === key) ||
            fresh.some((e) => `${e.who}|${e.title}`.toLowerCase() === key) ||
            openTasks.some((tsk) => tsk.title.toLowerCase().includes(c.title.toLowerCase().slice(0, 8))) ||
            openFollowups.some((f) => f.what.toLowerCase().includes(c.title.toLowerCase().slice(0, 8)));
          if (!dup && c.title.trim()) fresh.push({ id: newId(), createdAt: new Date().toISOString(), ...c });
        }
        if (fresh.length) update('todoCandidates', [...existing, ...fresh].slice(-30));
        return fresh.length;
      },
      dismissTodoCandidate(id: string) {
        update('todoCandidates', ref.current.todoCandidates.filter((c) => c.id !== id));
      },
      /** Re-reads candidates the background check may have written while the app was open. */
      async reloadTodoCandidates() {
        const raw = await AsyncStorage.getItem(KEYS.todoCandidates).catch(() => null);
        update('todoCandidates', raw ? JSON.parse(raw) : ref.current.todoCandidates);
      },
      /** Approves a candidate: a task when it has a time, else a 회신 대기 followup. Then removes it. */
      async approveTodoCandidate(id: string): Promise<void> {
        const c = ref.current.todoCandidates.find((x) => x.id === id);
        if (!c) return;
        if (c.dueAt) await a.addTask({ title: c.title, dueAt: c.dueAt, notes: c.room ? `${c.room}` : undefined });
        else a.addFollowup({ who: c.who || c.room, what: c.title, source: c.room || '카톡' });
        update('todoCandidates', ref.current.todoCandidates.filter((x) => x.id !== id));
      },

      addFollowup(input: { who: string; what: string; source?: string }): Followup {
        // Skip an obvious duplicate (same person + same thing still open).
        const dup = ref.current.followups.find((f) => !f.done && f.who === input.who && f.what === input.what);
        if (dup) return dup;
        const followup: Followup = { id: newId(), done: false, createdAt: new Date().toISOString(), ...input };
        update('followups', [...ref.current.followups, followup]);
        return followup;
      },
      completeFollowup(id: string): boolean {
        const exists = ref.current.followups.some((f) => f.id === id);
        update('followups', ref.current.followups.map((f) => (f.id === id ? { ...f, done: true } : f)));
        return exists;
      },
      deleteFollowup(id: string) {
        update('followups', ref.current.followups.filter((f) => f.id !== id));
      },

      /** Merges an exported/handwritten JSON bundle; skips memories and tasks that already exist. */
      async importData(data: ImportBundle): Promise<{ memories: number; tasks: number }> {
        if (data.profile) {
          const cur = ref.current.profile;
          update('profile', {
            name: data.profile.name?.trim() || cur.name,
            about: [cur.about, data.profile.about?.trim()].filter(Boolean).join('\n\n'),
          });
        }
        const known = new Set(ref.current.memories.map((m) => m.content.trim()));
        const newMemories = (data.memories ?? [])
          .filter((m) => m.content?.trim() && !known.has(m.content.trim()))
          .map((m) => ({ id: newId(), category: m.category?.trim() || '기타', content: m.content.trim(), createdAt: new Date().toISOString() }));
        update('memories', [...ref.current.memories, ...newMemories]);

        const taskKey = (t: { title: string; dueAt?: string }) => `${t.title.trim()}|${t.dueAt ? new Date(t.dueAt).getTime() : ''}`;
        const knownTasks = new Set(ref.current.tasks.map(taskKey));
        let added = 0;
        for (const t of data.tasks ?? []) {
          if (!t.title?.trim() || knownTasks.has(taskKey(t))) continue;
          const dueAt = t.dueAt && !Number.isNaN(Date.parse(t.dueAt)) ? new Date(t.dueAt).toISOString() : undefined;
          await a.addTask({ title: t.title.trim(), dueAt, notes: t.notes });
          added++;
        }
        return { memories: newMemories.length, tasks: added };
      },

      /** 드라이브 백업(backup.ts의 Backup)으로 복구한다. 목록은 id로 합쳐 중복 없이 추가, 설정·프로필·방은 백업 값으로. */
      async applyBackup(b: {
        profile?: Profile;
        memories?: Memory[];
        tasks?: Task[];
        followups?: Followup[];
        routines?: Routine[];
        sailings?: Sailing[];
        expenses?: Expense[];
        todoRooms?: string[];
        fxRooms?: string[];
        settings?: Partial<Pick<AppState, 'model' | 'briefingHour' | 'planHour' | 'weeklyReportDay' | 'speechRate' | 'voiceId' | 'voiceOnOpen' | 'companyUrl'>>;
      }): Promise<{ memories: number; tasks: number; followups: number }> {
        const mergeById = <T extends { id: string }>(cur: T[], incoming?: T[]) => {
          const have = new Set(cur.map((x) => x.id));
          const add = (incoming ?? []).filter((x) => x && x.id && !have.has(x.id));
          return { merged: [...cur, ...add], added: add.length };
        };
        if (b.profile && (b.profile.name?.trim() || b.profile.about?.trim())) update('profile', { name: b.profile.name ?? '', about: b.profile.about ?? '' });
        const m = mergeById(ref.current.memories, b.memories); update('memories', m.merged);
        const t = mergeById(ref.current.tasks, b.tasks); update('tasks', t.merged);
        const f = mergeById(ref.current.followups, b.followups); update('followups', f.merged);
        const r = mergeById(ref.current.routines, b.routines); update('routines', r.merged);
        const s = mergeById(ref.current.sailings, b.sailings); update('sailings', s.merged);
        const ex = mergeById(ref.current.expenses, b.expenses); update('expenses', ex.merged);
        if (Array.isArray(b.todoRooms)) update('todoRooms', b.todoRooms);
        if (Array.isArray(b.fxRooms)) update('fxRooms', b.fxRooms);
        const st = b.settings ?? {};
        if (st.model) update('model', st.model);
        if (st.briefingHour !== undefined) update('briefingHour', st.briefingHour);
        if (st.planHour !== undefined) update('planHour', st.planHour);
        if (st.weeklyReportDay !== undefined) update('weeklyReportDay', st.weeklyReportDay);
        if (typeof st.speechRate === 'number') update('speechRate', st.speechRate);
        if (typeof st.voiceId === 'string') update('voiceId', st.voiceId);
        if (st.voiceOnOpen) update('voiceOnOpen', st.voiceOnOpen);
        if (typeof st.companyUrl === 'string') update('companyUrl', st.companyUrl);
        return { memories: m.added, tasks: t.added, followups: f.added };
      },

      appendChat(turn: Omit<ChatTurn, 'id' | 'createdAt'>) {
        update('chat', [...ref.current.chat, { ...turn, id: newId(), createdAt: new Date().toISOString() }]);
      },
      /** Stores the rolling conversation summary and how many leading turns it already covers. */
      setChatSummary: (summary: string, upto: number) => {
        update('chatSummary', summary);
        update('chatSummaryUpto', upto);
      },
      clearChat: () => {
        update('chat', []);
        update('chatSummary', '');
        update('chatSummaryUpto', 0);
      },
    };
    return a;
  }, [update]);

  return { state, loaded, actions, getState: () => ref.current };
}

export type Store = ReturnType<typeof useStoreValue>;
export type StoreActions = Store['actions'];

const StoreContext = createContext<Store | null>(null);

export function StoreProvider({ children }: { children: ReactNode }) {
  const store = useStoreValue();
  return <StoreContext.Provider value={store}>{children}</StoreContext.Provider>;
}

export function useStore(): Store {
  const store = useContext(StoreContext);
  if (!store) throw new Error('useStore must be used inside <StoreProvider>');
  return store;
}
