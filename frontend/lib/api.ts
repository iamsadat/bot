// Typed client for the JobHunt FastAPI backend. Same-origin in production
// (the static export is served at the site root; /app is a demo); set
// NEXT_PUBLIC_API_BASE for local dev against :8000.

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE || '';

// ---- access code ---------------------------------------------------------
// When the server sets JOBHUNT_ACCESS_CODE, every /api/* call needs it in an
// X-Access-Code header (the websocket takes ?code=). It is remembered in
// localStorage so it is typed once per browser. Storage can throw (private
// mode, blocked site data), so every touch is guarded and the page still
// works — it just asks again.
const ACCESS_CODE_KEY = 'jh_access_code';

export function getAccessCode(): string {
  try {
    return (typeof window !== 'undefined' && window.localStorage.getItem(ACCESS_CODE_KEY)) || '';
  } catch {
    return '';
  }
}

function storeAccessCode(code: string): void {
  try { window.localStorage.setItem(ACCESS_CODE_KEY, code); } catch { /* storage blocked */ }
}

// Set once the user cancels the prompt, so a page full of pollers does not
// re-ask every few seconds. A reload asks again.
let accessPromptDeclined = false;

// Called after a 401. Returns the code to retry with, or null to give up.
// window.prompt blocks, so concurrent 401s are handled one after another: the
// first asks, the rest see the freshly stored code and just retry with it.
function codeAfterUnauthorized(sent: string): string | null {
  const current = getAccessCode();
  if (current && current !== sent) return current;
  if (accessPromptDeclined || typeof window === 'undefined') return null;
  const entered = window.prompt(
    sent
      ? 'That access code was not accepted. Enter the JobHunt access code:'
      : 'This JobHunt is protected. Enter the access code:',
  );
  const code = (entered || '').trim();
  if (!code) {
    accessPromptDeclined = true;
    return null;
  }
  storeAccessCode(code);
  return code;
}

/** fetch() for API URLs: sends the access code; on a 401 asks for it once and retries once. */
export async function apiFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const send = (code: string) => {
    const headers = new Headers(init.headers);
    if (code) headers.set('X-Access-Code', code);
    return fetch(url, { credentials: 'include', ...init, headers });
  };
  const code = getAccessCode();
  const res = await send(code);
  if (res.status !== 401) return res;
  const retry = codeAfterUnauthorized(code);
  return retry ? send(retry) : res;
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await apiFetch(`${API_BASE}${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    // Surface the API's own explanation when it sent one. Without this the
    // caller only ever sees a status code, so "no such GitHub user" and
    // "GitHub is rate-limiting you" both render as a generic failure.
    let detail = '';
    try { detail = (await res.clone().json())?.detail || ''; } catch { /* not JSON */ }
    throw new Error(detail || `${method} ${path} → ${res.status}`);
  }
  const ct = res.headers.get('content-type') || '';
  return (ct.includes('application/json') ? res.json() : (res.text() as any)) as T;
}

export const api = {
  status: () => req<Status>('GET', '/api/status'),
  jobs: () => req<{ jobs: Job[] }>('GET', '/api/jobs'),
  approvals: (filter = 'pending') =>
    req<{ approvals: Approval[] }>('GET', `/api/approvals?state_filter=${filter}`),
  activity: (limit = 80) =>
    req<{ activity: ActivityEvent[]; grouped: Record<string, ActivityEvent[]> }>(
      'GET', `/api/activity?limit=${limit}`),
  autonomy: () => req<Autonomy>('GET', '/api/autonomy'),
  setAutonomy: (b: Partial<Autonomy>) => req<any>('POST', '/api/autonomy', b),
  document: (jobId: string) => req<{ document: Doc }>('GET', `/api/documents/${jobId}`),
  profile: () => req<{ profile: Profile | null; ats_config?: AtsConfig; pending_parse?: any }>('GET', '/api/profile'),
  parseResume: (text: string) => req<ParsedResume>('POST', '/api/onboarding/resume', { text }),
  parseResumeFile: (filename: string, content_base64: string) =>
    req<ParsedResume>('POST', '/api/profile/parse-resume-file', { filename, content_base64 }),
  importGithub: (username: string) =>
    req<{ added: number; projects: any[] }>('POST', '/api/profile/import-github', { username }),
  // First-time creation only: the POST rebuilds the profile from scratch, which
  // resets autonomy, radar and screening answers. Edits go through updateProfile.
  saveProfile: (p: any) => req<any>('POST', '/api/onboarding/profile', p),
  // Merge-update an existing profile; fields left out are kept as they are.
  updateProfile: (p: ProfileUpdate) =>
    req<{ ok: boolean; profile: Profile }>('PUT', '/api/profile', p),
  saveAts: (a: AtsConfig) => req<{ ok: boolean; ats_config: AtsConfig }>(
    'POST', '/api/onboarding/ats', a),
  setJobStatus: (jobId: string, status: string) =>
    req<any>('POST', `/api/jobs/${jobId}/status`, { status }),
  saveStructured: (p: any) => req<any>('PUT', '/api/profile/structured', p),
  startHunt: () => req<any>('POST', '/api/hunt/start'),
  discover: () => req<DiscoverResult>('POST', '/api/discover'),
  syncInbox: () => req<InboxSyncResult>('POST', '/api/inbox/sync'),
  sources: () => req<SourcesInfo>('GET', '/api/sources'),
  approve: (id: string, decision = 'approve') =>
    req<ApproveResult>('POST', `/api/approve/${id}?decision=${decision}`),
  downloadUrl: (jobId: string, fmt: string, kind = 'resume') =>
    `${API_BASE}/api/documents/${jobId}/download?format=${fmt}&kind=${kind}`,
  salary: (role: string, location = '') =>
    req<{ currency: string; p10: number; median: number; p90: number; sample: number }>(
      'GET', `/api/salary?role=${encodeURIComponent(role)}&location=${encodeURIComponent(location)}`),
  // --- growth & retention features ---
  metrics: () => req<Metrics>('GET', '/api/metrics'),
  analytics: () => req<Analytics>('GET', '/api/analytics'),
  radar: () => req<RadarData>('GET', '/api/radar'),
  radarSettings: () => req<RadarSettings>('GET', '/api/radar/settings'),
  setRadar: (b: Partial<RadarSettings>) => req<any>('POST', '/api/radar/settings', b),
  atsScore: (resume_text: string, jd_text: string) =>
    req<{ score: number; matched: string[]; missing: string[]; suggestions: string[] }>(
      'POST', '/api/tools/ats-score', { resume_text, jd_text }),
  publish: (job_id: string) =>
    req<{ ok: boolean; handle: string; url: string }>('POST', '/api/publish', { job_id }),
  interviewQuestions: (job_id: string) =>
    req<{ questions: { type: string; question: string }[] }>(
      'POST', '/api/interview/questions', { job_id }),
  interviewFeedback: (question: string, answer: string) =>
    req<InterviewFeedback>('POST', '/api/interview/feedback', { question, answer }),
  skillGaps: () => req<{ gaps: SkillGap[] }>('GET', '/api/skills/gaps'),
  contacts: (due = false) =>
    req<{ contacts: Contact[] }>('GET', `/api/contacts${due ? '?due=true' : ''}`),
  saveContact: (c: Partial<Contact>) => req<any>('POST', '/api/contacts', c),
  deleteContact: (id: string) => req<any>('DELETE', `/api/contacts/${id}`),
  nudgeContact: (id: string) => req<any>('POST', `/api/contacts/${id}/nudge`),
  // --- identity: tie a workspace to a verified email (magic link) ---
  requestMagicLink: (email: string) =>
    req<{ sent: boolean; dev_link?: string }>('POST', '/api/auth/request-link', { email }),
  authStatus: () => req<{ linked_email: string | null }>('GET', '/api/auth/status'),
};

// Fire-and-forget pageview beacon for the no-auth top-of-funnel surfaces
// (landing page, ATS tool). Never throws — a failed beacon must never break
// the page it's called from.
export function recordPageview(surface: 'landing' | 'ats_tool', ref?: string): void {
  const code = getAccessCode();
  fetch(`${API_BASE}/api/pageview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(code ? { 'X-Access-Code': code } : {}) },
    body: JSON.stringify({ surface, ref: ref ?? null }),
  }).catch(() => {});
}

export type PricePref = 'monthly_19' | 'monthly_29' | 'lifetime_99' | 'lifetime_149';

export const joinWaitlist = (email: string, price_pref: PricePref) =>
  req<{ ok: boolean }>('POST', '/api/waitlist', { email, price_pref });

// ---- admin: founder-facing validation stats -------------------------------
// Both endpoints below are gated on JOBHUNT_ADMIN_TOKEN server-side and 403
// without a matching X-Admin-Token header. The token is held in memory by the
// /admin page only — never persisted to localStorage or a cookie.

export interface WaitlistStats {
  total: number;
  by_price_pref: Record<PricePref, number>;
}
export interface SurfaceStats {
  total: number;
  by_day: Record<string, number>;
  top_refs?: { ref: string; count: number }[];
}
export type PageviewStats = Record<'landing' | 'ats_tool' | 'public_resume', SurfaceStats>;

async function adminReq<T>(path: string, token: string): Promise<T> {
  const res = await apiFetch(`${API_BASE}${path}`, {
    headers: { 'X-Admin-Token': token },
  });
  if (res.status === 403) throw new Error('forbidden');
  if (!res.ok) throw new Error(`GET ${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

export const getAdminWaitlistStats = (token: string) =>
  adminReq<WaitlistStats>('/api/waitlist/stats', token);

export const getAdminPageviewStats = (token: string) =>
  adminReq<PageviewStats>('/api/pageview/stats', token);

export const billingStatus = () =>
  req<{ plan: string; billing_configured: boolean }>('GET', '/api/billing/status');

export interface Metrics {
  discovered: number; tailored: number; applied: number; interview: number; offer: number;
  callback_rate: number; evidence_coverage: number; applied_this_week: number;
  streak: number; weekly_target: number; weekly_progress: number;
}
export interface Analytics {
  funnel: Metrics; winner: string | null;
  variants: { name: string; impressions: number; successes: number; success_rate: number }[];
}
export interface RadarSettings {
  radar_enabled: boolean; current_salary: number | null;
  current_title: string; radar_keywords: string[];
}
export interface RadarData {
  market_value: { date: string; median: number; currency: string; role: string }[];
  hits: Job[]; enabled: boolean;
}
export interface InterviewFeedback {
  scores: { structure: number; relevance: number; specificity: number };
  tips: string[]; overall: number;
}
export interface SkillGap { skill: string; count: number; resources: { title: string; url: string }[]; }
export interface Contact {
  id: string; name: string; email: string; company: string; title: string;
  last_contact: string; next_followup: string; notes: string; job_id: string;
}

export function wsUrl(): string {
  const base = API_BASE || (typeof window !== 'undefined' ? window.location.origin : '');
  // A browser WebSocket cannot set headers, so the access code rides as ?code=.
  const code = getAccessCode();
  return base.replace(/^http/, 'ws') + '/ws/stream' + (code ? `?code=${encodeURIComponent(code)}` : '');
}

// ---- types ---------------------------------------------------------------
export interface Status {
  hunt_status: string; jobs_count: number; applied_count: number;
  approvals_pending: number; ats_configured: boolean; has_profile: boolean;
  auto_apply: boolean; applied_today: number; continuous: boolean;
  inbox_connected: boolean; llm?: { provider?: string };
  hunt_error?: string | null;
  notify_channels?: string[];
  // Single-user install: no workspaces, so there is no progress to "save".
  personal?: boolean;
  // What this install has switched on (LLM polish, inbox sync, …). Absent on
  // older servers.
  capabilities?: Capability[];
}
export interface Capability { key: string; label: string; on: boolean; hint: string; }
export interface InboxSyncResult {
  ok: boolean; checked?: number; updates?: number; error?: string;
}
// What an approve actually did. `copilot` means a browser window opened with
// the application filled in, waiting for the user to press Submit there.
export interface Submission {
  submitted: boolean; manual?: boolean; copilot?: boolean; detail?: string;
  submission_id?: string;
}
export interface ApproveResult {
  ok: boolean; request?: Approval; submission?: Submission | null;
}

/** One line telling the user what an approve did; null when there is nothing to say. */
export function describeSubmission(s?: Submission | null): { text: string; warn?: boolean } | null {
  if (!s) return null;
  if (s.copilot) {
    return {
      text: 'A browser window opened with the application filled in — review it and press Submit there.'
        + (s.detail ? ` ${s.detail}` : ''),
    };
  }
  if (s.submitted) {
    return { text: s.detail || `Submitted${s.submission_id ? ` (id ${s.submission_id})` : ''}.` };
  }
  if (s.manual) {
    return { text: s.detail || 'Marked Applied — open the posting to finish on the company site.' };
  }
  return { text: `Submission failed${s.detail ? `: ${s.detail}` : '.'}`, warn: true };
}
// Only three boards are edited in the UI, but the endpoint replaces the whole
// config, so the discovery-only recruitee/workable/personio slugs have to be
// sent back as loaded or a save wipes them.
export interface AtsConfig {
  greenhouse_tokens?: string[]; lever_slugs?: string[]; ashby_slugs?: string[];
  recruitee_slugs?: string[]; workable_slugs?: string[]; personio_slugs?: string[];
}
export interface SourceStatus {
  name: string; status: 'ok' | 'degraded'; jobs: number; checked_at: number;
}
export interface SourcesInfo {
  sources: SourceStatus[]; page: number; ats_connected: boolean;
  // True when discovery is using the curated public company boards because the
  // user has connected none of their own.
  seeded_boards?: boolean; seeded_board_count?: number;
}
// Why a job scored what it did. Each component is 0..1; `skills_scored` is false
// when the description was too thin to judge coverage from (a listing snippet),
// in which case the skill figure was not counted against the job.
export interface ScoreBreakdown {
  total: number; title: number; skills: number; seniority: number; location: number;
  candidate_level_name?: string | null; posting_level_name?: string | null;
  matched_keywords?: string[]; missing_keywords?: string[]; skills_scored?: boolean;
}
export interface DiscoverResult {
  ok: boolean; added: number; tailored: number; applied: number;
  seen: number; duplicates: number;
}
export interface Job {
  job_id: string; title: string; company: string; location: string; url: string;
  status: string; relevance_score?: number; remote?: boolean; submitted?: boolean;
  awaiting_approval?: boolean;
  score_breakdown?: ScoreBreakdown;
  events?: { ts: number; stage: string; detail: string; status: string }[];
}
export interface Approval {
  request_id: string; job_id: string; company: string; title: string; state: string;
}
export interface ActivityEvent {
  agent: string; task_id: string; thought: string; phase?: string;
  considered?: string[]; rejected?: { item: string; reason: string }[];
  confidence?: number | null; decision?: string;
}
export interface Autonomy {
  auto_apply: boolean; daily_apply_cap: number; relevance_floor: number;
  ats_connected: boolean; applied_today: number; effective_cap: number; continuous: boolean;
}
export interface Doc {
  job_id: string; company: string; title: string; draft?: ResumeDraft | null;
  keyword_coverage?: number; matched_keywords?: string[]; missing_keywords?: string[];
}
export interface ResumeDraft {
  candidate_name: string; candidate_email: string; phone?: string; location?: string;
  links?: Record<string, string>; summary: string; sections: ResumeSection[];
  matched_keywords: string[]; missing_keywords: string[];
}
export interface ResumeSection {
  title: string; kind: string; body?: string;
  rows?: { left: string; right?: string; link?: string; bullets?: { text: string; evidence_id?: string }[] }[];
}
export interface Profile {
  name: string; email: string; phone?: string; skills: string[];
  experiences: any[]; education: any[]; projects: any[]; links: Record<string, string>;
  target_roles?: string[]; locations?: string[];
  // Standard answers to ATS screening questions, used to auto-fill forms.
  application_answers?: Record<string, unknown>;
}
// Fields PUT /api/profile merges; anything left out is kept as it is.
export interface ProfileUpdate {
  name?: string; email?: string; phone?: string;
  target_roles?: string[]; locations?: string[]; skills?: string[];
  links?: Record<string, string>; application_answers?: Record<string, unknown>;
  auto_apply?: boolean; daily_apply_cap?: number; relevance_floor?: number;
  remote_ok?: boolean; min_salary?: number | null; veto_companies?: string[];
}
export interface ParsedResume {
  skills: string[]; experiences: any[]; education: any[]; projects: any[];
  links: Record<string, string>;
}
