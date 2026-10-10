import AsyncStorage from '@react-native-async-storage/async-storage';
import * as SecureStore from 'expo-secure-store';
import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { Platform } from 'react-native';

import { cancelReminder, scheduleReminder } from './notifications';
import type { ChatTurn, Memory, Profile, Task } from './types';

const KEYS = {
  tasks: 'dorothy.tasks',
  memories: 'dorothy.memories',
  profile: 'dorothy.profile',
  chat: 'dorothy.chat',
  briefingHour: 'dorothy.briefingHour',
  voiceOnOpen: 'dorothy.voiceOnOpen',
  workspaceId: 'dorothy.workspaceId',
};
const API_KEY = 'dorothy.anthropicApiKey';

export type ImportBundle = {
  profile?: { name?: string; about?: string };
  memories?: { category?: string; content: string }[];
  tasks?: { title: string; dueAt?: string; notes?: string }[];
};

export type AppState = {
  tasks: Task[];
  memories: Memory[];
  profile: Profile;
  chat: ChatTurn[];
  apiKey: string;
  /** Needed only for organization-level API keys that aren't scoped to a workspace. */
  workspaceId: string;
  /** Hour (0-23) of the daily briefing notification, or null when off. */
  briefingHour: number | null;
  /** What happens when the app is opened: nothing, listen once, or enter standby. */
  voiceOnOpen: VoiceOnOpen;
};

export type VoiceOnOpen = 'off' | 'listen' | 'standby';

const initialState: AppState = {
  tasks: [],
  memories: [],
  profile: { name: '', about: '' },
  chat: [],
  apiKey: '',
  workspaceId: '',
  briefingHour: null,
  voiceOnOpen: 'off',
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
      const [tasks, memories, profile, chat, hour, apiKey, voiceOnOpen, workspaceId] = await Promise.all([
        AsyncStorage.getItem(KEYS.tasks),
        AsyncStorage.getItem(KEYS.memories),
        AsyncStorage.getItem(KEYS.profile),
        AsyncStorage.getItem(KEYS.chat),
        AsyncStorage.getItem(KEYS.briefingHour),
        secret.get(API_KEY),
        AsyncStorage.getItem(KEYS.voiceOnOpen),
        AsyncStorage.getItem(KEYS.workspaceId),
      ]);
      ref.current = {
        tasks: tasks ? JSON.parse(tasks) : [],
        memories: memories ? JSON.parse(memories) : [],
        profile: profile ? JSON.parse(profile) : initialState.profile,
        chat: chat ? JSON.parse(chat) : [],
        briefingHour: hour ? Number(hour) : null,
        voiceOnOpen: voiceOnOpen ? JSON.parse(voiceOnOpen) : 'off',
        apiKey: apiKey ?? '',
        workspaceId: workspaceId ? JSON.parse(workspaceId) : '',
      };
      setState(ref.current);
      setLoaded(true);
    })();
  }, []);

  const update = useCallback(<K extends keyof AppState>(key: K, value: AppState[K]) => {
    ref.current = { ...ref.current, [key]: value };
    setState(ref.current);
    if (key === 'apiKey') {
      secret.set(API_KEY, value as string);
    } else if (key === 'briefingHour') {
      if (value === null) AsyncStorage.removeItem(KEYS.briefingHour);
      else AsyncStorage.setItem(KEYS.briefingHour, String(value));
    } else {
      AsyncStorage.setItem(KEYS[key as Exclude<keyof AppState, 'apiKey'>], JSON.stringify(value));
    }
  }, []);

  const actions = useMemo(() => {
    const a = {
      setProfile: (profile: Profile) => update('profile', profile),
      setApiKey: (key: string) => update('apiKey', key.trim()),
      setWorkspaceId: (id: string) => update('workspaceId', id.trim()),
      setBriefingHour: (hour: number | null) => update('briefingHour', hour),
      setVoiceOnOpen: (mode: VoiceOnOpen) => update('voiceOnOpen', mode),

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
