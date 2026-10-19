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

/** Something the user owes someone: a reply to send or a request to handle, found in messages/calls. */
export type Followup = {
  id: string;
  /** Who it's with (person or chat room), e.g. "김 대표", "엄마". */
  who: string;
  /** What's owed, e.g. "견적 회신", "배송일 안내". */
  what: string;
  done: boolean;
  createdAt: string;
  /** Where it came from, e.g. "카카오톡", "부재중 전화", "문자". */
  source?: string;
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
