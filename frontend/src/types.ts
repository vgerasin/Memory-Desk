export interface PersonDto { id: string; name: string; role: string; initials: string; color: string; organization?: string; aliases?: string[]; notes?: string }
export interface ProjectRef { id: string | null; title: string; color: string }
export interface TypeRef { id: string; title: string }

export type MeetingStatus = 'needs_review' | 'completed'

export interface MeetingItem {
  id: string; title: string; date: string; time: string
  duration: string; durationSec: number | null
  project: ProjectRef | null; type: TypeRef; people: PersonDto[]
  status: MeetingStatus; excerpt: string; important: boolean; archived: boolean
}

export interface TaskDto {
  id: string; text: string; owner: string; due: string | null; dueText: string
  t: string; tSec: number | null; status: 'draft' | 'open' | 'done' | 'rejected'
  draft: boolean; done: boolean; meetingId: string; meetingTitle: string; meetingDate: string
  ageDays?: number; stuck?: boolean; stuckReasons?: string[]
}
export interface DecisionDto {
  id: string; text: string; owner: string; t: string; tSec: number | null
  status: 'draft' | 'confirmed' | 'rejected'; draft: boolean; replacedBy?: string | null
  meetingId: string; meetingTitle: string; meetingDate: string
}
export interface Fact { text: string; t: string; tSec: number | null }
export interface Segment { i: number; t: string; sec: number; speaker: string; text: string; overlap?: boolean }
export interface SpeakerInfo {
  label: string; count: number; generic: boolean; personId: string | null
  /** есть голосовой отпечаток; matched — человек узнан по голосу автоматически */
  voice: { matched: boolean; score: number | null } | null
}
export interface DiarizationStatus { state: 'idle' | 'running' | 'done' | 'error'; mode?: 'text' | 'voice'; done?: number; total?: number; message?: string }
export interface VoiceState {
  enabled: boolean; available: boolean; reason: string; dependencies: boolean; dependenciesReason: string
  modelReady: boolean; profiles: number
  download: { state: 'idle' | 'running' | 'done' | 'error'; phase: '' | 'install' | 'model'; done: number; total: number; message: string }
}
export interface BackupItem { name: string; size: number; createdAt: string }

export interface MeetingDetail extends MeetingItem {
  summary: string[]; topics: string[]; decisions: DecisionDto[]; tasks: TaskDto[]
  risks: Fact[]; questions: Fact[]; nextSteps: Fact[]; transcript: Segment[]
  speakers: SpeakerInfo[]; diarization: DiarizationStatus | null
  obsidianUrl: string | null; notePath: string; hasAudio: boolean; audioUrl: string | null
  template?: { id: string; name: string; suggestedId: string }; extraSections?: { title: string; items: Fact[] }[]
  views?: ViewDto[]; bookmarks?: BookmarkDto[]; attachments?: AttachmentDto[]
}

export interface MeetingsResponse { items: MeetingItem[]; total: number; counts: { total: number; needsReview: number; noProject: number } }

export interface HeroQuestion { text: string; t: string; tSec: number | null; meetingId: string; meetingTitle: string; meetingDate: string }
export interface HeroColumns { decisions: DecisionDto[]; tasks: TaskDto[]; questions: HeroQuestion[] }
export interface HeroPerson { name: string; initials: string; color: string }
export interface Hero {
  kind: 'next_meeting' | 'last_record'
  title: string; people: (PersonDto | HeroPerson)[]; project: { id: string | null; title: string; color?: string } | null
  columns: HeroColumns
  start?: string; end?: string; ongoing?: boolean
  meetingId?: string; date?: string; time?: string
}
export interface TodayPayload {
  hero: Hero | null; newRecords: MeetingItem[]; tasks: TaskDto[]; drafts: number
  decisions: DecisionDto[]; noProject: number; reviewCount: number
}

export interface ProjectOverview {
  id: string; title: string; color: string; goal: string; status?: ProjectStatus
  counts: { records: number; openTasks: number; questions: number }
  people: PersonDto[]
  lastChange: { text: string; kind: 'decision' | 'summary'; meetingId: string; date: string } | null
}
export interface PersonOverview extends PersonDto {
  meetings: number; projects: string[]; obligations: number; lastAgreement: TaskDto | null
  voice: { samples: number; seconds: number } | null
}

export interface Meta {
  projects: { id: string; title: string; color: string }[]
  types: { id: string; title: string }[]
  people: PersonDto[]
  calendarConfigured: boolean
  calendarWrite: boolean
}

export interface JobError { title: string; stage: string; action: string; detail: string }
export interface Job {
  id: string; file: string; duration: number | null
  state: 'queued' | 'running' | 'failed'
  stage: string; stage_index: number; stage_label: string; percent: number
  started_at: string | null; eta: number | null; last_message: string; runner: string
  error: JobError | null; templateId?: string
}
export interface AppState {
  queue: Job[]; running: Job | null; inboxCount: number
  dataVersion: number; jobsVersion: number; indexState: string
}

export interface SystemRow { key: string; name: string; value: string; state: 'ok' | 'warn' | 'bad' }
export interface SystemStatus { rows: SystemRow[]; overall: 'ok' | 'warn' | 'bad'; indexState: string; indexError: string }

export interface Settings {
  recordings_dir: string; obsidian_dir: string; archive_dir: string
  retention_days: number; auto_process: boolean; diarize: boolean; voice_enabled: boolean; calendar_source: string
  apple_calendar: boolean; reminders_enabled: boolean; reminders_list: string; reminders_scope: 'mine' | 'all'; my_name: string
  notify: boolean; morning_brief: boolean; morning_hour: number; backup_auto: boolean; backup_interval_hours: number; backup_keep: number
  backup_on_change: boolean; note_writeback: boolean; mirror_enabled: boolean
  default_template: string
  exists?: Record<string, boolean>
}

export interface ChatSource {
  meetingId: string; title: string; date: string; time: string; section: string
  t: string; tSec: number | null; excerpt: string
}
export interface ChatDigest {
  title: string; records: number; shown: number; model: boolean
  stats: { records: number; decisions: number; decisionsDraft: number; tasksDone: number; tasksOpen: number; tasksOverdue: number; risks: number }
}
export interface ChatMaterial { kind: MaterialKind; result: MaterialResult }
export interface ChatReply { answer: string; sources: ChatSource[]; mode: string; confirmed: boolean; digest?: ChatDigest; material?: ChatMaterial; suggestions?: string[] }
export interface ChatMessage {
  id: number; role: 'user' | 'bot'; text: string; sources?: ChatSource[]; digest?: ChatDigest; material?: ChatMaterial; pending?: boolean; error?: boolean
  suggestions?: string[]
  saved?: { savedAs: string; obsidianUrl: string; already: boolean }; saving?: boolean; saveError?: string
}
export type ChatScope =
  | { kind: 'all' }
  | { kind: 'record'; id: string; label: string }
  | { kind: 'project'; id: string; title: string; label: string }
  | { kind: 'person'; id: string; label: string }
  | { kind: 'days'; days: number; label: string }

export interface AppleStatus {
  available: boolean; reason: string; events: string; reminders: string
  calendarEnabled: boolean; remindersEnabled: boolean
  lastSync: { at: number | null; summary: Record<string, number | string> | null; error: string }
}
export interface CalendarSuggestion {
  linked: boolean; title?: string
  event?: { id: string; title: string; start: string; end: string; calendar: string }
  people?: string[]; project?: { id: string; title: string } | null; titleDiffers?: boolean
}

export interface StorageCategory {
  key: string; title: string; bytes: number; count: number; hint: string; clean: string; days: boolean; danger: boolean
}
export interface StorageUsage {
  categories: StorageCategory[]; info: { key: string; title: string; bytes: number; count: number | null }[]; total: number
}

export interface PrepTarget { projectId?: string; meetingId?: string; title?: string }

export type ArtifactKind = 'memo' | 'brief' | 'followup'
export type MaterialKind = 'protocol' | 'map' | 'deck' | 'digest' | ArtifactKind
/** Материал по запросу: протокол записи, карта или презентация по записи, проекту или человеку */
export interface MaterialTarget { kind: MaterialKind; meetingId?: string; projectId?: string; personId?: string; title: string; result?: MaterialResult }
export interface MapNode { text: string; children: MapNode[] }
export interface MaterialResult {
  title: string; mode?: 'model' | 'structured'; savedAs?: string; obsidianUrl?: string
  markdown?: string; fileName?: string
  tree?: MapNode; mermaid?: string
  name?: string; url?: string; slides?: number; records?: number
  /** служебная записка, бриф, письмо: записи-источники, число пунктов и не вошедшие (без места в записи) */
  refs?: { id: string; title: string; label: string }[]; count?: number; located?: number
  skipped?: { kind: string; text: string; meetingId: string; meetingTitle: string; reason: string }[]
}
export interface PrepItem {
  text: string; meetingId: string; meetingTitle: string; date: string; tSec: number | null; view: string | null
  owner?: string; due?: string | null; dueText?: string; draft?: boolean; overdue?: boolean
}
export interface PrepSection { key: 'changes' | 'decisions' | 'tasks' | 'questions' | 'risks' | 'bookmarks'; title: string; empty: string; items: PrepItem[] }
export interface PrepHeadline { text: string; mode: 'model' | 'structured' }
export interface PrepBrief { title: string; projectId: string | null; meetingId: string | null; records: number; sections: PrepSection[]; headline: PrepHeadline }

export interface AnalyticsData {
  days: number; today: string
  totals: { records: number; minutes: number; openTasks: number; overdue: number; doneTasks: number; decisions: number }
  weeks: { start: string; meetings: number; minutes: number }[]
  byOwner: { owner: string; open: number; overdue: number; done: number }[]
  gantt: { id: string; text: string; owner: string; draft: boolean; project: string; start: string; due: string; overdue: boolean; meetingId: string; tSec: number | null }[]
  ganttTotal: number
  decisions: { id: string; text: string; draft: boolean; project: string; date: string; meetingId: string; tSec: number | null }[]
  graph: {
    people: { name: string; meetings: number; initials: string; color: string }[]
    projects: { name: string; meetings: number; color: string }[]
    links: { person: string; project: string; meetings: number }[]
  }
}

export interface ReindexStatus {
  state: 'idle' | 'running' | 'done' | 'error'; done: number; total: number; records: number; message: string
  startedAt: number; finishedAt: number; seconds: number
}

export interface DecisionNode {
  id: string; text: string; owner: string; status: 'draft' | 'confirmed'; draft: boolean
  date: string; meetingId: string; meetingTitle: string; tSec: number | null
  projectId: string; project: string; replacedBy: string | null; replaces: DecisionNode[]
}
export interface DecisionGroup { id: string; title: string; color: string; total: number; active: number; replaced: number; current: DecisionNode[] }
export interface DecisionTreeData { today: string; days: number; projects: DecisionGroup[] }

export interface CalendarEvent {
  id: string; title: string; start: string; end: string; allDay: boolean; calendar: string; location: string; people: string[]
  records: { id: string; title: string; linked: boolean }[]
}
export interface CalendarDay { date: string; events: CalendarEvent[]; records: { id: string; title: string; time: string }[] }
export interface CalendarRange {
  from: string; to: string; source: 'apple' | 'ics' | 'none'; error: string; configured: boolean
  days: CalendarDay[]; totals: { events: number; withRecord: number; recordsWithoutEvent: number }
}

export interface MorningTask { id: string; text: string; owner: string; due: string | null; dueText: string; meetingId: string; meetingTitle: string; tSec: number | null; ageDays: number }
export interface MorningMeeting {
  title: string; start: string; end: string; people: string[]; project: { id: string; title: string } | null; headline: string; hasRecord: boolean
}
export interface MorningPayload {
  date: string; empty: boolean; message: string
  counts: { overdue: number; mine: number; promised: number; meetings: number; stuck: number }
  overdue: MorningTask[]; mine: MorningTask[]; promised: MorningTask[]; stuck: MorningTask[]; meetings: MorningMeeting[]
}

export interface SeriesItem { id: string; text: string; owner: string; status: string; deadline?: string }
export interface SeriesInfo {
  id: string; title: string; count: number; position: number
  meetings: { id: string; title: string; date: string; time: string; current: boolean }[]
  previous: { id: string; title: string; date: string; time: string } | null
  decisionsNew: SeriesItem[]; tasksNew: SeriesItem[]; tasksClosed: SeriesItem[]; tasksOpen: SeriesItem[]
  questionsNew: { text: string; sec: number | null }[]; questionsResolved: { text: string }[]
  questionsRepeated: { text: string; sec: number | null; count: number; since: string }[]
}

export interface ConflictPair { key: string; similarity: number; project: string; old: DecisionDto; new: DecisionDto }
export interface ConflictVerdict { relation: 'replaces' | 'contradicts' | 'consistent' | 'unrelated'; reason: string }

export interface TemplateSection { key: string; title: string; instruction: string; free: boolean }
export interface TemplateDto {
  id: string; name: string; description: string; builtin: boolean; overridesBundled: boolean; recordTypes: string[]; focus: string; signature: string
  sections: TemplateSection[]
}
export interface TemplatesResponse { items: TemplateDto[]; problems: string[]; default: string; dir: string; message?: string }
export interface RebuildState {
  state: 'idle' | 'running' | 'done' | 'error'; templateId?: string; requestedId?: string; fallback?: string
  keptTasks?: number; keptDecisions?: number; message?: string
}

export type BookmarkColor = 'yellow' | 'red' | 'green' | 'blue' | 'purple'
export interface BookmarkDto { sec: number; t: string; text: string; color: BookmarkColor; context: string }
export interface ViewDto {
  id: string; title: string; kind: 'preset' | 'custom'; instruction: string; date: string; model: string
  items: Fact[]; edited: boolean
}
export interface ViewPreset { id: string; title: string; instruction: string; limit: number }
export interface ViewPresets { presets: ViewPreset[]; max: number }
export interface ViewJob {
  state: 'idle' | 'running' | 'done' | 'error'; viewId?: string; title?: string; label?: string
  percent?: number; part?: number; total?: number; elapsed?: number; message?: string
}

export type ProjectStatus = 'active' | 'paused' | 'done'
export interface MeetingBrief { id: string; title: string; date: string; time: string; project: ProjectRef | null }
export interface FactLine { text: string; sec: number | null; meetingId: string; meetingTitle: string; date: string }

export interface PersonDossier {
  person: PersonDto & { voice: { samples: number; seconds: number } | null }
  stats: {
    meetings: number; meetingsLast30: number; firstContact: string | null; lastContact: string | null; daysSince: number | null
    lastMeeting: MeetingBrief | null; talk: { seconds: number; share: number } | null
  }
  tasks: { open: number; overdue: number; stuck: number; done: number; drafts: number; items: TaskDto[] }
  projects: (ProjectRef & { meetings: number; share: number })[]
  decisions: { total: number; items: DecisionDto[] }
  questions: FactLine[]
  companions: (PersonDto & { meetings: number })[]
  timeline: MeetingBrief[]
}

export interface ProjectDossier {
  project: { id: string; title: string; color: string; status: ProjectStatus; statusLabel: string; goal: string; description: string }
  health: { level: 'ok' | 'warn' | 'bad'; reasons: string[]; overdue: number; stuck: number; drafts: number; openTasks: number; openQuestions: number; daysSinceLast: number | null }
  counts: { records: number; first: string | null; last: string | null }
  people: (PersonDto & { meetings: number; openTasks: number })[]
  tasksByPerson: { owner: string; count: number; items: TaskDto[] }[]
  decisions: { active: DecisionDto[]; activeTotal: number; replaced: DecisionDto[]; replacedTotal: number }
  risks: FactLine[]; questions: FactLine[]
  weeks: { start: string; meetings: number }[]
  series: { id: string; title: string; count: number; first: string; last: string; latestId: string; repeatedQuestions: number }[]
  recent: MeetingBrief[]
}

export interface AttachmentDto {
  id: string; name: string; kind: 'image' | 'text'; text: string; engine: string; draft: boolean
  path: string; exists: boolean; size: number; url: string | null; already?: boolean; warning?: string
}
