// Typed client for the JobHunt FastAPI backend. Same-origin in production
// (served under /app); set NEXT_PUBLIC_API_BASE for local dev against :8000.

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE || '';

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    credentials: 'include',
  });
  if (!res.ok) throw new Error(`${method} ${path} → ${res.status}`);
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
  profile: () => req<{ profile: Profile | null }>('GET', '/api/profile'),
  parseResume: (text: string) => req<ParsedResume>('POST', '/api/onboarding/resume', { text }),
  parseResumeFile: (filename: string, content_base64: string) =>
    req<ParsedResume>('POST', '/api/profile/parse-resume-file', { filename, content_base64 }),
  importGithub: (username: string) =>
    req<{ added: number; projects: any[] }>('POST', '/api/profile/import-github', { username }),
  saveProfile: (p: any) => req<any>('POST', '/api/onboarding/profile', p),
  saveStructured: (p: any) => req<any>('PUT', '/api/profile/structured', p),
  startHunt: () => req<any>('POST', '/api/hunt/start'),
  discover: () => req<any>('POST', '/api/discover'),
  approve: (id: string, decision = 'approve') =>
    req<any>('POST', `/api/approve/${id}?decision=${decision}`),
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
  fetch(`${API_BASE}/api/pageview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ surface, ref: ref ?? null }),
  }).catch(() => {});
}

export type PricePref = 'monthly_19' | 'monthly_29' | 'lifetime_99' | 'lifetime_149';

export const joinWaitlist = (email: string, price_pref: PricePref) =>
  req<{ ok: boolean }>('POST', '/api/waitlist', { email, price_pref });

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
  return base.replace(/^http/, 'ws') + '/ws/stream';
}

// ---- types ---------------------------------------------------------------
export interface Status {
  hunt_status: string; jobs_count: number; applied_count: number;
  approvals_pending: number; ats_configured: boolean; has_profile: boolean;
  auto_apply: boolean; applied_today: number; continuous: boolean;
  inbox_connected: boolean; llm?: { provider?: string };
}
export interface Job {
  job_id: string; title: string; company: string; location: string; url: string;
  status: string; relevance_score?: number; remote?: boolean; submitted?: boolean;
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
  rows?: { left: string; right?: string; link?: string; bullets?: { text: string }[] }[];
}
export interface Profile {
  name: string; email: string; phone?: string; skills: string[];
  experiences: any[]; education: any[]; projects: any[]; links: Record<string, string>;
}
export interface ParsedResume {
  skills: string[]; experiences: any[]; education: any[]; projects: any[];
  links: Record<string, string>;
}
