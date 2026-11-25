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
  /** 선박명(e.g. "SAWASDEE SUNRISE"). 자동 수집분에서 채워진다. */
  vessel?: string;
  /** 서비스 레인(e.g. "JPH"). */
  service?: string;
  /** 소요일(부산→마닐라). 직항 3일 vs 환적 13일처럼 핵심 비교값. */
  transitDays?: number;
  /** 환적(T/S)이면 true, 직항(Direct)이면 false. */
  transship?: boolean;
  note?: string;
  /** 어디서 왔는지: 'auto'=선사 사이트 자동 수집, 'manual'=사진/직접 등록. */
  source?: 'auto' | 'manual';
  /** Local-notification ids for the auto reminders (day before cutoffs/ETD), so they can be cancelled. */
  reminderIds?: string[];
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

/** A personal expense Dorothy recorded, usually read from a receipt/payment photo. */
export type Expense = {
  id: string;
  /** 금액(숫자). */
  amount: number;
  /** 통화: 'PHP' | 'KRW' (기본 PHP). */
  currency: string;
  /** 분류, e.g. 식비·교통·통신·공과금·장보기·기타. */
  category?: string;
  /** 어디서/무엇에 썼는지 한 줄. */
  memo?: string;
  /** 가게/상대. */
  vendor?: string;
  /** 쓴 날짜(YYYY-MM-DD). 없으면 등록일로. */
  spentAt?: string;
  createdAt: string;
};

export type Profile = {
  name: string;
  /** Free-form self-introduction the user writes so Dorothy knows them from day one. */
  about: string;
};

/**
 * 사장님의 '말투·성격' 프로필. 도로시가 사장님의 실제 메시지·대화를 보고 스스로 뽑아낸,
 * "사장님처럼 쓰고 말하는 법" 설명서다. 대신 메시지를 쓸 때 이 말투로 쓰고, mirror가 켜져
 * 있으면 도로시가 사장님에게 답할 때도 이 말투·성격을 흉내 낸다. 모두 이 폰에만 저장된다.
 */
export type StyleProfile = {
  /** 학습한 말투·성격 설명(한국어, 편집 가능). 비어 있으면 아직 학습 전. */
  guide: string;
  /** 켜면 도로시가 사장님에게 답할 때도 이 말투·성격을 따라 한다(대신 쓰는 메시지는 항상 따라 함). */
  mirror: boolean;
  /** 마지막으로 학습한 시각(ISO). 비면 아직 학습 안 함. */
  learnedAt: string;
};

/** Persisted chat turns are text-only: tool calls and thinking blocks stay inside a single turn. */
export type ChatTurn = {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  createdAt: string;
  /** 카톡식 답글: 이 메시지가 어떤 말에 대한 답장인지(인용 표시용). */
  replyTo?: { role: 'user' | 'assistant'; text: string };
};

/** 도로시가 만들어 보관하는 견적서·인보이스 등 문서. 거래처별로 다시 찾아볼 수 있다. */
export type SavedDoc = {
  id: string;
  createdAt: string;
  /** 문서 종류: 견적서/인보이스/보고서/기타. */
  kind: 'quote' | 'invoice' | 'report' | 'other';
  /** 파일 이름/제목. */
  title: string;
  /** 거래처·고객 이름(있으면). */
  customer?: string;
  /** 파일 형식. */
  format: 'pdf' | 'word' | 'excel';
  /** pdf·word 본문(마크다운). 다시 파일로 만들거나 내용을 보여줄 때 쓴다. */
  content?: string;
  /** excel 표 머리글·행. */
  headers?: string[];
  rows?: string[][];
};

/** 사장님의 표준 운임·부가세 단가. 저장해두면 "세부 20CBM 화장품 견적" 한마디에 즉석 견적을 낸다. */
export type PricingProfile = {
  cbmOnce: number;      // 일회성 CBM당 운임(원)
  kgOnce: number;       // 일회성 kg당 운임(원)
  cbmRegular: number;   // 정기 CBM당 운임(원)
  kgRegular: number;    // 정기 kg당 운임(원)
  exportDecl: number;   // 면장(수출신고) 비용(원)
  koreaPickup: number;  // 한국 픽업비(원)
  phDelivery: number;   // 현지(필리핀) 배송비 기본값(페소)
  vatKoreaPct: number;  // 한국 부가세율(%) 기본 10
  vatPhPct: number;     // 필리핀 부가세율(%) 기본 12
  note: string;         // 메모(유의사항 등)
};
