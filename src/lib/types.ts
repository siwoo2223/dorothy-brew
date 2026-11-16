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

/** A recurring reminder: fires every day, or every week on `weekday`, at hour:minute. */
export type Routine = {
  id: string;
  /** What to be reminded of, e.g. "개인톡 미회신 확인". */
  title: string;
  hour: number;
  minute: number;
  repeat: 'daily' | 'weekly';
  /** expo weekday 1=Sun … 7=Sat, for weekly routines. */
  weekday?: number;
  /** expo-notifications id, used to cancel it. */
  notificationId?: string;
  createdAt: string;
};

export type Memory = {
  id: string;
  /** e.g. "가족", "건강", "취향", "일" */
  category: string;
  content: string;
  createdAt: string;
};

/** A booking/sailing Dorothy read from the forwarder's schedule photo. Dates are ISO (YYYY-MM-DD). */
export type Sailing = {
  id: string;
  /** 선사, e.g. HMM / KMTC / ONE. */
  carrier: string;
  /** 항차 또는 부킹 번호. */
  voyageNo?: string;
  /** 출발항, e.g. 부산 / 인천. */
  from?: string;
  /** 도착항, e.g. 마닐라 노스포트. */
  to?: string;
  /** 서류 마감(YYYY-MM-DD). */
  docCutoff?: string;
  /** 반입 마감(CY closing, YYYY-MM-DD). */
  cyCutoff?: string;
  /** 출항일 ETD(YYYY-MM-DD). */
  etd?: string;
  /** 도착일 ETA(YYYY-MM-DD). */
  eta?: string;
  note?: string;
  createdAt: string;
};

/** A to-do Dorothy detected from a watched KakaoTalk room, awaiting the owner's 등록/무시 decision. */
export type TodoCandidate = {
  id: string;
  /** Short task sentence (제목). The original message text is NOT stored. */
  title: string;
  /** Who requested it (상대/방). */
  who: string;
  /** ISO datetime when it's due, or absent → becomes a 회신 대기 instead of a timed task. */
  dueAt?: string;
  /** The room it came from. */
  room: string;
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
