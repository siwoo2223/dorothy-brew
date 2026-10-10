export type Task = {
  id: string;
  title: string;
  /** ISO 8601 date-time; when set, a local reminder notification is scheduled. */
  dueAt?: string;
  notes?: string;
  done: boolean;
  createdAt: string;
  /** Identifier returned by expo-notifications, used to cancel the reminder. */
  notificationId?: string;
};

export type Memory = {
  id: string;
  /** e.g. "가족", "건강", "취향", "일" */
  category: string;
  content: string;
  createdAt: string;
};

export type Profile = {
  name: string;
  /** Free-form self-introduction the user writes so Dorothy knows them from day one. */
  about: string;
};

/** Persisted chat turns are text-only: tool calls and thinking blocks stay inside a single turn. */
export type ChatTurn = {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  createdAt: string;
};
