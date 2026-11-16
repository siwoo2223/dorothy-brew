import AsyncStorage from '@react-native-async-storage/async-storage';
import * as SecureStore from 'expo-secure-store';
import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { Platform } from 'react-native';

import { cancelReminder, scheduleReminder, scheduleRepeatingReminder } from './notifications';
import { setCompanyConfig } from './company';
import { setSpeechRate, setSpeechVoice } from './voice';
import type { ModelChoice } from './assistant';
import type { ChatTurn, Followup, Memory, Profile, Routine, Sailing, Task, TodoCandidate } from './types';

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
  sailings: 'dorothy.sailings',
  todoRooms: 'dorothy.todoRooms',
  todoCandidates: 'dorothy.todoCandidates',
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
  /** Booking/sailing schedules Dorothy read from the forwarder's photos (shown as calendar/list). */
  sailings: Sailing[];
  /** Room names (partial match) to watch for to-do/requests in KakaoTalk etc. Empty = off. */
  todoRooms: string[];
  /** To-dos Dorothy detected from watched rooms, awaiting the owner's 등록/무시 decision. */
  todoCandidates: TodoCandidate[];
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
  sailings: [],
  todoRooms: [],
  todoCandidates: [],
};

export const newId = () => `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

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
      const [tasks, followups, memories, profile, chat, hour, apiKey, voiceOnOpen, workspaceId, model, planHour, speakerMatch, voiceId, companyUrl, companyToken, weeklyReportDay, speechRate, routines, standbyShortcut, sailings, todoRooms, todoCandidates] = await Promise.all([
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
        AsyncStorage.getItem(KEYS.sailings),
        AsyncStorage.getItem(KEYS.todoRooms),
        AsyncStorage.getItem(KEYS.todoCandidates),
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
        sailings: sailings ? JSON.parse(sailings) : [],
        todoRooms: todoRooms ? JSON.parse(todoRooms) : [],
        todoCandidates: todoCandidates ? JSON.parse(todoCandidates) : [],
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
        const sailing: Sailing = { id: newId(), createdAt: new Date().toISOString(), ...input };
        update('sailings', [...ref.current.sailings, sailing]);
        return sailing;
      },
      deleteSailing(id: string) {
        update('sailings', ref.current.sailings.filter((s) => s.id !== id));
      },
      clearSailings() {
        update('sailings', []);
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

      appendChat(turn: Omit<ChatTurn, 'id' | 'createdAt'>) {
        update('chat', [...ref.current.chat, { ...turn, id: newId(), createdAt: new Date().toISOString() }]);
      },
      clearChat: () => update('chat', []),
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
