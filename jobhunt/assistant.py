"""In-app assistant: chat with JobHunt and have it make changes for you.

There is no native tool calling here — every ``LLMClient`` is plain text in,
plain text out — so the model is asked to answer with ONE JSON object::

    {"reply": "...", "actions": [{"name": "...", "args": {...}}]}

and this module validates and runs each action against the same state the
dashboard endpoints mutate. Nothing the model says is trusted: unknown actions
and bad arguments come back as ``{"ok": false, "detail": ...}``, and anything
that could submit an application, hit the network for a long time, or touch
many jobs at once is returned as *pending* and only runs when the client sends
it back in ``confirm``.

The conversation is stateless on the server: the client sends its history on
every turn and we keep only the tail of it.
"""

from __future__ import annotations

import json
import re
from typing import Any

from jobhunt.llm import factory as llm_factory
from jobhunt.llm.anthropic_client import _EMAIL_RE, _PHONE_RE

MAX_MESSAGES = 20
MAX_CHARS = 8000
TOP_JOBS = 15
BULK_LIMIT = 5  # a change touching more jobs than this needs confirmation

VALID_STATUSES = ("Saved", "Applied", "Assessment", "Interview", "Offer", "Closed")

# Screening-answer keys the submitters and autofill look up (see onboarding UI).
KNOWN_ANSWER_KEYS = (
    "work_authorization", "requires_sponsorship", "years_experience",
    "notice_period", "current_ctc", "expected_ctc", "willing_to_relocate",
    "how_did_you_hear", "linkedin", "github", "website",
)

NO_LLM_REPLY = (
    "The assistant needs an AI model, and none is set up on this machine. "
    "To use your Claude subscription: install Claude Code "
    "(`npm install -g @anthropic-ai/claude-code`), run `claude` once to sign in, "
    "then restart JobHunt (set JOBHUNT_LLM=claude-code to force it). "
    "A GEMINI_API_KEY or ANTHROPIC_API_KEY works too."
)

SAFE_ACTIONS = (
    "update_profile", "set_screening_answers", "add_skills", "remove_skills",
    "set_job_status", "add_job_note", "reject_job", "set_autonomy_limits",
    "set_radar", "explain_match",
)
GATED_ACTIONS = ("approve_job", "fetch_jobs", "toggle_auto_apply", "edit_experience")
JOB_ACTIONS = ("set_job_status", "add_job_note", "reject_job")

ACTION_CATALOG = """\
Applied immediately:
- update_profile {name?, email?, phone?, target_roles?: [str], locations?: [str],
    skills?: [str] (replaces the whole list), links?: {key: url} (merged),
    min_salary?: int|null, remote_ok?: bool, veto_companies?: [str]}
- set_screening_answers {answers: {key: value}} — merged into existing answers;
    known keys: """ + ", ".join(KNOWN_ANSWER_KEYS) + """
- add_skills {skills: [str]}
- remove_skills {skills: [str]}
- set_job_status {job_id | job_ids: [str] | company, status: one of """ + "/".join(VALID_STATUSES) + """}
- add_job_note {job_id | company, note: str, next_action?: str}
- reject_job {job_id | company} — reject the tailored résumé waiting for approval
- set_autonomy_limits {daily_apply_cap?: int >= 0, relevance_floor?: 0..1}
- set_radar {radar_enabled?: bool, current_salary?: int|null, current_title?: str,
    radar_keywords?: [str]}
- explain_match {job_id | company} — no change; explains a job's match score
Need the owner's confirmation (propose them; the app asks before running):
- approve_job {job_id | company} — may submit a REAL application
- fetch_jobs {} — run a discovery sweep now (slow)
- toggle_auto_apply {enabled: bool}
- edit_experience {index: int, fields: {title?, company?, location?, start?, end?,
    bullets?: [str]}} — index into the experience list shown above
Any change touching more than """ + str(BULK_LIMIT) + """ jobs also needs confirmation."""


class ActionError(ValueError):
    """An action the model proposed that cannot be run as given."""


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def handle_chat(state, body: Any, *, registry=None) -> dict:
    """Answer one chat turn. Never raises on bad input or model output."""
    body = body if isinstance(body, dict) else {}
    messages = _clean_messages(body.get("messages"))
    user_text = "\n".join(m["content"] for m in messages if m["role"] == "user")
    confirm = body.get("confirm") or []

    if isinstance(confirm, list) and confirm:
        applied = _run_actions(state, confirm, user_text, registry=registry,
                               confirmed=True)["applied"]
        ok = [a for a in applied if a["ok"]]
        reply = (f"Done — {len(ok)} of {len(applied)} confirmed change(s) applied."
                 if applied else "Nothing to confirm.")
        return {"reply": reply, "applied": applied, "pending": [], "llm": False}

    if not messages or messages[-1]["role"] != "user":
        return {"reply": "Ask me something — e.g. “Add Airflow to my skills”.",
                "applied": [], "pending": [], "llm": False}

    client = llm_factory.build_llm_client_from_env()
    if client is None:
        return {"reply": NO_LLM_REPLY, "applied": [], "pending": [], "llm": False}

    try:
        raw = client.complete(build_system_prompt(state), _transcript(messages),
                              max_tokens=1500)
    except Exception as exc:  # LLMError, timeouts, anything — never a 500
        return {"reply": f"I couldn't reach the AI model just now ({exc}). Try again in a moment.",
                "applied": [], "pending": [], "llm": False}

    reply, actions = parse_model_output(raw)
    result = _run_actions(state, actions, user_text, registry=registry, confirmed=False)
    return {"reply": reply, **result, "llm": True}


# --------------------------------------------------------------------------- #
# Conversation + prompt
# --------------------------------------------------------------------------- #

def _clean_messages(raw: Any) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out = []
    for m in raw:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
            continue
        content = str(m.get("content") or "").strip()
        if content:
            out.append({"role": m["role"], "content": content})
    out = out[-MAX_MESSAGES:]
    # Drop the oldest until the history fits; then clip what is left.
    while len(out) > 1 and sum(len(m["content"]) for m in out) > MAX_CHARS:
        out.pop(0)
    if out and len(out[0]["content"]) > MAX_CHARS:
        out[0] = {**out[0], "content": out[0]["content"][-MAX_CHARS:]}
    return out


def _transcript(messages: list[dict]) -> str:
    lines = [f"{'OWNER' if m['role'] == 'user' else 'ASSISTANT'}: {m['content']}"
             for m in messages]
    return ("Conversation so far (answer the last OWNER message):\n\n"
            + "\n\n".join(lines)
            + "\n\nRespond with the JSON object only.")


def build_system_prompt(state) -> str:
    return (
        "You are the assistant inside JobHunt, a personal job-hunting copilot used by "
        "one owner. You can answer questions about their search and change things "
        "for them by emitting actions.\n\n"
        "Reply with ONLY one JSON object, no prose around it and no code fences:\n"
        '{"reply": "<short, friendly answer>", "actions": [{"name": "<action>", "args": {}}]}\n'
        "Use an empty actions list when nothing should change.\n\n"
        "Rules:\n"
        "- Never invent facts. update_profile, set_screening_answers and edit_experience may "
        "only use values the owner stated in this conversation. No made-up numbers, "
        "metrics, dates, employers or skills. If something is missing, ask.\n"
        "- Refer to jobs by the job_id shown below. If the owner names a company, use "
        "that job's job_id (or the company arg when it is not listed).\n"
        "- For confirmation-gated actions, propose them and say the owner will be asked "
        "to confirm. Do not claim they already happened.\n"
        "- To explain a match, use explain_match and summarise the breakdown in reply.\n"
        "- For privacy you see the owner's email address and phone number as <email> and "
        "<phone>. Those are their real values: pass the token as-is "
        "(e.g. args {\"email\": \"<email>\"}) and it is filled in when saved. Never ask them to repeat it.\n"
        "- Keep reply under 120 words.\n\n"
        f"ACTIONS:\n{ACTION_CATALOG}\n\n"
        f"CURRENT STATE:\n{describe_state(state)}\n"
    )


def describe_state(state) -> str:
    p = state.user_profile
    lines: list[str] = []
    if p is None:
        lines.append("No profile yet — the owner has not finished onboarding. "
                     "Profile actions will fail until they do.")
    else:
        lines += [
            f"Name: {p.name} | email: {p.email} | phone: {p.phone or '-'}",
            f"Current title: {p.current_title or '-'}",
            f"Target roles: {', '.join(p.target_roles) or '-'}",
            f"Locations: {', '.join(p.locations) or '-'} | remote ok: {p.remote_ok} "
            f"| min salary: {p.min_salary or '-'}",
            f"Skills: {', '.join(p.skills[:40]) or '-'}",
            f"Vetoed companies: {', '.join(p.veto_companies) or '-'}",
            f"Links: {', '.join(sorted(p.links)) or '-'}",
            "Screening answers set: "
            + (", ".join(f"{k}={str(v)[:40]}" for k, v in p.application_answers.items())
               or "none"),
            f"Autonomy: auto_apply={p.auto_apply}, daily_apply_cap={p.daily_apply_cap}, "
            f"relevance_floor={p.relevance_floor}",
            f"Radar: enabled={p.radar_enabled}, current_salary={p.current_salary}, "
            f"keywords={', '.join(p.radar_keywords) or '-'}",
            "Experience:",
        ]
        for i, e in enumerate(p.experiences):
            lines.append(
                f"  [{i}] {e.get('title', '')} @ {e.get('company', '')} "
                f"({e.get('start', '')}–{e.get('end', '')}), "
                f"{len(e.get('bullets') or [])} bullet(s)")
        if not p.experiences:
            lines.append("  (none)")

    counts: dict[str, int] = {}
    for j in state.jobs:
        counts[j.get("status", "Saved")] = counts.get(j.get("status", "Saved"), 0) + 1
    lines.append(f"Jobs: {len(state.jobs)} total — "
                 + (", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "none"))
    awaiting = {r.job_id for r in state.approval_queue.pending()}
    lines.append(f"Awaiting approval: {len(awaiting)}")
    top = sorted(state.jobs, key=lambda j: float(j.get("relevance_score") or 0), reverse=True)
    lines.append(f"Top {min(TOP_JOBS, len(top))} jobs (job_id | title | company | status | match):")
    for j in top[:TOP_JOBS]:
        flag = " | awaiting approval" if j.get("job_id") in awaiting else ""
        lines.append(
            f"  {j.get('job_id')} | {j.get('title')} | {j.get('company')} | "
            f"{j.get('status', 'Saved')} | {float(j.get('relevance_score') or 0):.0%}{flag}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Model output
# --------------------------------------------------------------------------- #

_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\s*\n?(.*?)\n?```\s*$", re.DOTALL)


def parse_model_output(raw: Any) -> tuple[str, list]:
    """``(reply, actions)``; non-JSON output becomes the reply with no actions."""
    text = str(raw or "").strip()
    m = _FENCE_RE.match(text)
    body = m.group(1).strip() if m else text
    data = None
    for candidate in (body, body[body.find("{"):body.rfind("}") + 1]):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
            break
        except (ValueError, TypeError):
            continue
    if not isinstance(data, dict):
        return text, []
    reply = data.get("reply")
    actions = data.get("actions")
    return (str(reply) if reply is not None else "",
            actions if isinstance(actions, list) else [])


# --------------------------------------------------------------------------- #
# Running actions
# --------------------------------------------------------------------------- #

def _run_actions(state, actions: list, user_text: str, *, registry, confirmed: bool) -> dict:
    applied: list[dict] = []
    pending: list[dict] = []
    ready: list[tuple[str, dict, dict]] = []  # (name, args, original action)

    for action in actions[:20]:
        name = action.get("name") if isinstance(action, dict) else None
        label = name if isinstance(name, str) else "?"
        try:
            if not isinstance(action, dict):
                raise ActionError("action must be an object")
            if name not in SAFE_ACTIONS and name not in GATED_ACTIONS:
                raise ActionError(f"unknown action {name!r}")
            args = _validate(state, name, action.get("args"), user_text)
        except ActionError as exc:
            applied.append({"action": label, "ok": False, "detail": str(exc)})
            continue
        ready.append((name, args, {"name": name, "args": action.get("args") or {}}))

    # A response touching many jobs at once is a bulk change.
    touched = {jid for name, args, _ in ready if name in JOB_ACTIONS for jid in args["job_ids"]}
    bulk = len(touched) > BULK_LIMIT

    changed = False
    for name, args, original in ready:
        if not confirmed and (name in GATED_ACTIONS or (bulk and name in JOB_ACTIONS)):
            pending.append({**original, "summary": _summary(state, name, args)})
            continue
        try:
            detail = _execute(state, name, args, registry)
            ok = True
        except ActionError as exc:
            detail, ok = str(exc), False
        except Exception as exc:  # a bug in a code path must not 500 the chat
            detail, ok = f"failed: {exc}", False
        applied.append({"action": name, "ok": ok, "detail": detail})
        if ok and name != "explain_match":
            changed = True
            state.bus.publish("assistant", "chat", f"Assistant: {detail}")

    if changed:
        state.persist()
    return {"applied": applied, "pending": pending}


# ---- validation ----------------------------------------------------------- #

def _need_profile(state):
    if state.user_profile is None:
        raise ActionError("no profile yet — finish onboarding first")
    return state.user_profile


def _args(raw: Any, allowed: set[str]) -> dict:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ActionError("args must be an object")
    extra = set(raw) - allowed
    if extra:
        raise ActionError(f"unexpected arg(s): {', '.join(sorted(extra))}")
    return raw


def _str_list(v: Any, key: str) -> list[str]:
    if isinstance(v, str):
        v = v.split(",")
    if not isinstance(v, list) or not all(isinstance(s, (str, int, float)) for s in v):
        raise ActionError(f"{key} must be a list of strings")
    return [str(s).strip() for s in v if str(s).strip()]


def _int_or_none(v: Any, key: str) -> int | None:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        raise ActionError(f"{key} must be a number")
    try:
        return int(float(v))
    except (TypeError, ValueError):
        raise ActionError(f"{key} must be a number") from None


def _bool(v: Any, key: str) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.strip().lower() in ("true", "yes", "on", "false", "no", "off"):
        return v.strip().lower() in ("true", "yes", "on")
    raise ActionError(f"{key} must be true or false")


def _rehydrate(value: str, user_text: str, key: str) -> str:
    """Put back an email/phone the PII redaction hid from the model.

    The model only ever sees ``<email>``/``<phone>``; the owner typed the real
    value, so take it from their own messages — never from anywhere else.
    """
    for token, rx in (("<email>", _EMAIL_RE), ("<phone>", _PHONE_RE)):
        if token in value:
            found = rx.findall(user_text)
            if not found:
                raise ActionError(f"{key}: you didn't give a value for {token[1:-1]}")
            last = found[-1] if isinstance(found[-1], str) else "".join(found[-1])
            value = value.replace(token, last)
    return value


_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in _NUM_RE.findall(text)}


def _job_ids(state, raw: dict) -> list[str]:
    ids: list[str] = []
    if "job_ids" in raw:
        ids = _str_list(raw["job_ids"], "job_ids")
    elif raw.get("job_id"):
        ids = [str(raw["job_id"]).strip()]
    elif raw.get("company"):
        company = str(raw["company"]).strip().lower()
        hits = [j for j in state.jobs if company in str(j.get("company", "")).lower()]
        if not hits:
            raise ActionError(f"no job at a company matching {raw['company']!r}")
        if len(hits) > 1:
            names = "; ".join(f"{j['job_id']} ({j.get('title')})" for j in hits[:6])
            raise ActionError(f"{len(hits)} jobs match {raw['company']!r} — say which: {names}")
        ids = [hits[0]["job_id"]]
    if not ids:
        raise ActionError("job_id is required")
    known = {j["job_id"] for j in state.jobs}
    missing = [i for i in ids if i not in known]
    if missing:
        raise ActionError(f"unknown job_id(s): {', '.join(missing)}")
    return list(dict.fromkeys(ids))


def _validate(state, name: str, raw: Any, user_text: str) -> dict:
    if name == "update_profile":
        _need_profile(state)
        a = _args(raw, {"name", "email", "phone", "target_roles", "locations", "skills",
                        "links", "min_salary", "remote_ok", "veto_companies"})
        if not a:
            raise ActionError("nothing to update")
        out: dict[str, Any] = {}
        if "name" in a:
            out["name"] = _rehydrate(str(a["name"]).strip(), user_text, "name")
            if not out["name"]:
                raise ActionError("name cannot be empty")
        if "email" in a:
            out["email"] = _rehydrate(str(a["email"]).strip(), user_text, "email").lower()
            if "@" not in out["email"]:
                raise ActionError("invalid email")
        if "phone" in a:
            out["phone"] = _rehydrate(str(a["phone"]).strip(), user_text, "phone")
        for key in ("target_roles", "locations", "skills", "veto_companies"):
            if key in a:
                out[key] = _str_list(a[key], key)
        if "skills" in out:
            out["skills"] = [s.lower() for s in out["skills"]]
        if "links" in a:
            if not isinstance(a["links"], dict):
                raise ActionError("links must be an object")
            out["links"] = {str(k): _rehydrate(str(v).strip(), user_text, "links")
                            for k, v in a["links"].items() if str(v).strip()}
        if "min_salary" in a:
            out["min_salary"] = _int_or_none(a["min_salary"], "min_salary")
        if "remote_ok" in a:
            out["remote_ok"] = _bool(a["remote_ok"], "remote_ok")
        return out

    if name == "set_screening_answers":
        _need_profile(state)
        a = _args(raw, {"answers"})
        answers = a.get("answers")
        if not isinstance(answers, dict) or not answers:
            raise ActionError("answers must be a non-empty object")
        out = {}
        for k, v in answers.items():
            key = str(k).strip().lower()
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,40}", key):
                raise ActionError(f"bad answer key {k!r}")
            if not isinstance(v, (str, int, float, bool)):
                raise ActionError(f"answer for {key} must be text, a number or yes/no")
            if isinstance(v, str):
                v = _rehydrate(v.strip(), user_text, key)
                if not v:
                    continue
            out[key] = v
        if not out:
            raise ActionError("no answers given")
        return {"answers": out}

    if name in ("add_skills", "remove_skills"):
        _need_profile(state)
        a = _args(raw, {"skills"})
        skills = [s.lower() for s in _str_list(a.get("skills", []), "skills")]
        if not skills:
            raise ActionError("skills must not be empty")
        return {"skills": skills}

    if name == "set_job_status":
        a = _args(raw, {"job_id", "job_ids", "company", "status"})
        status = str(a.get("status", "")).strip().capitalize()
        if status not in VALID_STATUSES:
            raise ActionError(f"status must be one of {', '.join(VALID_STATUSES)}")
        return {"job_ids": _job_ids(state, a), "status": status}

    if name == "add_job_note":
        a = _args(raw, {"job_id", "company", "note", "next_action"})
        note = str(a.get("note") or "").strip()
        next_action = str(a.get("next_action") or "").strip()
        if not note and not next_action:
            raise ActionError("note is required")
        return {"job_ids": _job_ids(state, a), "note": note, "next_action": next_action}

    if name in ("reject_job", "approve_job", "explain_match"):
        a = _args(raw, {"job_id", "company"})
        ids = _job_ids(state, a)
        if len(ids) != 1:
            raise ActionError("give exactly one job")
        return {"job_ids": ids}

    if name == "set_autonomy_limits":
        _need_profile(state)
        a = _args(raw, {"daily_apply_cap", "relevance_floor"})
        if not a:
            raise ActionError("give daily_apply_cap and/or relevance_floor")
        out = {}
        if "daily_apply_cap" in a:
            cap = _int_or_none(a["daily_apply_cap"], "daily_apply_cap")
            out["daily_apply_cap"] = max(0, cap or 0)
        if "relevance_floor" in a:
            try:
                floor = float(a["relevance_floor"] or 0)
            except (TypeError, ValueError):
                raise ActionError("relevance_floor must be a number") from None
            if floor > 1:
                floor /= 100  # "70" meant 70%
            out["relevance_floor"] = max(0.0, min(1.0, floor))
        return out

    if name == "set_radar":
        _need_profile(state)
        a = _args(raw, {"radar_enabled", "current_salary", "current_title", "radar_keywords"})
        if not a:
            raise ActionError("nothing to update")
        out = {}
        if "radar_enabled" in a:
            out["radar_enabled"] = _bool(a["radar_enabled"], "radar_enabled")
        if "current_salary" in a:
            out["current_salary"] = _int_or_none(a["current_salary"], "current_salary")
        if "current_title" in a:
            out["current_title"] = str(a["current_title"]).strip()
        if "radar_keywords" in a:
            out["radar_keywords"] = _str_list(a["radar_keywords"], "radar_keywords")
        return out

    if name == "fetch_jobs":
        _need_profile(state)
        _args(raw, set())
        return {}

    if name == "toggle_auto_apply":
        _need_profile(state)
        a = _args(raw, {"enabled"})
        if "enabled" not in a:
            raise ActionError("enabled is required")
        return {"enabled": _bool(a["enabled"], "enabled")}

    if name == "edit_experience":
        p = _need_profile(state)
        a = _args(raw, {"index", "fields"})
        idx = a.get("index")
        if isinstance(idx, bool) or not isinstance(idx, int) or not 0 <= idx < len(p.experiences):
            raise ActionError(f"index must be 0..{len(p.experiences) - 1}"
                              if p.experiences else "there is no experience entry to edit")
        fields = a.get("fields")
        allowed = {"title", "company", "location", "start", "end", "bullets"}
        if not isinstance(fields, dict) or not fields:
            raise ActionError("fields must be a non-empty object")
        if set(fields) - allowed:
            raise ActionError(f"unexpected field(s): {', '.join(sorted(set(fields) - allowed))}")
        out_fields: dict[str, Any] = {}
        for k, v in fields.items():
            out_fields[k] = _str_list(v, "bullets") if k == "bullets" else str(v).strip()
        # Code-level guard against invented metrics: every number written into
        # the résumé must be one the owner typed in this conversation.
        stated = _numbers(user_text)
        written = " ".join(out_fields["bullets"]) if "bullets" in out_fields else ""
        written += " " + " ".join(v for k, v in out_fields.items() if k != "bullets")
        invented = sorted(_numbers(written) - stated)
        if invented:
            raise ActionError("refused: these numbers weren't stated by you: "
                              + ", ".join(invented))
        return {"index": idx, "fields": out_fields}

    raise ActionError(f"unknown action {name!r}")  # pragma: no cover


# ---- execution ------------------------------------------------------------ #

def _job(state, job_id: str) -> dict:
    for j in state.jobs:
        if j["job_id"] == job_id:
            return j
    raise ActionError(f"unknown job_id {job_id}")


def _label(job: dict) -> str:
    return f"{job.get('company', '?')} — {job.get('title', '?')}"


def _summary(state, name: str, args: dict) -> str:
    jobs = [_job(state, i) for i in args.get("job_ids", [])]
    if name == "approve_job":
        return f"Approve the résumé for {_label(jobs[0])} (may submit a real application)"
    if name == "fetch_jobs":
        return "Run a discovery sweep now (takes a while)"
    if name == "toggle_auto_apply":
        return f"Turn auto-apply {'on' if args['enabled'] else 'off'}"
    if name == "edit_experience":
        return (f"Edit experience #{args['index']}: "
                + ", ".join(f"{k}" for k in args["fields"]))
    if name == "set_job_status":
        return f"Move {len(jobs)} jobs to {args['status']}"
    if name == "add_job_note":
        return f"Add a note to {len(jobs)} jobs"
    if name == "reject_job":
        return f"Reject {_label(jobs[0])}"
    return name


def _pending_request(state, job_id: str):
    from jobhunt.approval import ApprovalState
    reqs = [r for r in state.approval_queue.by_job(job_id) if r.state == ApprovalState.PENDING]
    if not reqs:
        raise ActionError("nothing is waiting for approval on that job")
    return reqs[0]


def _execute(state, name: str, args: dict, registry) -> str:
    # Lazy: server imports this module, and these helpers live in server.
    from jobhunt.dashboard import server as srv

    if name == "update_profile":
        p = _need_profile(state)
        for key, value in args.items():
            if key == "links":
                p.links = {**p.links, **value}
            else:
                setattr(p, key, value)
        return "Updated profile: " + ", ".join(args)

    if name == "set_screening_answers":
        p = _need_profile(state)
        p.application_answers = {**p.application_answers, **args["answers"]}
        return "Saved screening answers: " + ", ".join(
            f"{k}={v}" for k, v in args["answers"].items())

    if name == "add_skills":
        p = _need_profile(state)
        new = [s for s in args["skills"] if s not in p.skills]
        p.skills = list(p.skills) + new
        return f"Added skills: {', '.join(new)}" if new else "Those skills were already listed"

    if name == "remove_skills":
        p = _need_profile(state)
        drop = set(args["skills"])
        gone = [s for s in p.skills if s.lower() in drop]
        p.skills = [s for s in p.skills if s.lower() not in drop]
        return f"Removed skills: {', '.join(gone)}" if gone else "None of those skills were listed"

    if name == "set_job_status":
        moved = []
        for job_id in args["job_ids"]:
            # Mirrors POST /api/jobs/{id}/status.
            j = _job(state, job_id)
            old, new = j.get("status", "Saved"), args["status"]
            j["status"] = new
            state.bus.publish("tracking", job_id, f"{j['company']} → {j['title']}: {old} → {new}")
            srv._add_event(state, job_id, new, f"Moved {old} → {new}")
            if new in ("Interview", "Offer"):
                srv._record_outcome(state, job_id)
            moved.append(j.get("company", job_id))
        return f"Moved {', '.join(moved)} to {args['status']}"

    if name == "add_job_note":
        labels = []
        for job_id in args["job_ids"]:
            j = _job(state, job_id)
            if args["note"]:
                j["notes"] = (str(j.get("notes") or "").rstrip() + "\n" + args["note"]).strip()
            if args["next_action"]:
                j["next_action"] = args["next_action"]
            labels.append(j.get("company", job_id))
        return f"Added a note to {', '.join(labels)}"

    if name == "reject_job":
        from jobhunt.approval import ApprovalState, InvalidTransition
        job = _job(state, args["job_ids"][0])
        req = _pending_request(state, job["job_id"])
        try:  # mirrors the reject branch of POST /api/approve/{id}
            state.approval_queue.transition(req.request_id, ApprovalState.REJECTED,
                                            reviewer="assistant")
        except InvalidTransition as exc:
            raise ActionError(str(exc)) from None
        srv._add_event(state, job["job_id"], "Rejected",
                       "Resume rejected — won't be used", status="failed")
        state.bus.publish("approval", job["job_id"], f"{req.company} → reject by assistant")
        return f"Rejected {_label(job)}"

    if name == "approve_job":
        from jobhunt.approval import ApprovalState, InvalidTransition
        job = _job(state, args["job_ids"][0])
        req = _pending_request(state, job["job_id"])
        try:  # mirrors the approve branch of POST /api/approve/{id}
            req = state.approval_queue.transition(req.request_id, ApprovalState.APPROVED,
                                                  reviewer="you")
        except InvalidTransition as exc:
            raise ActionError(str(exc)) from None
        srv._add_event(state, job["job_id"], "Approved", "Resume approved by you")
        sub = srv._auto_apply(state, registry, req, job, state.documents.get(job["job_id"]))
        state.bus.publish("approval", job["job_id"], f"{req.company} → approve by you")
        how = (" and submitted" if sub and sub.get("submitted")
               else " — marked Applied, finish on the company site" if sub and sub.get("manual")
               else f" ({sub.get('detail')})" if sub and sub.get("detail") else "")
        return f"Approved {_label(job)}{how}"

    if name == "set_autonomy_limits":
        p = _need_profile(state)
        for key, value in args.items():
            setattr(p, key, value)
        return (f"Autonomy limits: cap {p.daily_apply_cap or 'default'}, "
                f"floor {p.relevance_floor:.0%}")

    if name == "set_radar":
        p = _need_profile(state)
        for key, value in args.items():
            setattr(p, key, value)
        return "Updated Career Radar: " + ", ".join(args)

    if name == "explain_match":
        return explain_job(_job(state, args["job_ids"][0]))

    if name == "fetch_jobs":
        res = srv._discover_once(state, registry)
        return (f"Fetched jobs: {res.get('added', 0)} new, "
                f"{res.get('tailored', 0)} tailored, {res.get('applied', 0)} applied")

    if name == "toggle_auto_apply":
        p = _need_profile(state)
        p.auto_apply = args["enabled"]
        return f"Auto-apply {'on' if p.auto_apply else 'off'}"

    if name == "edit_experience":
        p = _need_profile(state)
        entry = dict(p.experiences[args["index"]])
        entry.update(args["fields"])
        p.experiences = [entry if i == args["index"] else e for i, e in enumerate(p.experiences)]
        return f"Updated experience #{args['index']} ({', '.join(args['fields'])})"

    raise ActionError(f"unknown action {name!r}")  # pragma: no cover


def explain_job(job: dict) -> str:
    """One-paragraph summary of a job's score breakdown."""
    b = job.get("score_breakdown") or {}
    total = float(b.get("total", job.get("relevance_score") or 0) or 0)
    if not b:
        return f"{_label(job)}: {total:.0%} match (no breakdown recorded for this job)."
    parts = [f"{k} {float(b.get(k) or 0):.0%}" for k in ("title", "skills", "seniority", "location")
             if k in b]
    out = f"{_label(job)}: {total:.0%} match — " + ", ".join(parts)
    if b.get("candidate_level_name") or b.get("posting_level_name"):
        out += (f"; level you {b.get('candidate_level_name') or '?'} vs role "
                f"{b.get('posting_level_name') or '?'}")
    if b.get("matched_keywords"):
        out += "; matched: " + ", ".join(b["matched_keywords"][:8])
    if b.get("missing_keywords"):
        out += "; missing: " + ", ".join(b["missing_keywords"][:8])
    if b.get("skills_scored") is False:
        out += " (description too thin to judge skills, so they weren't held against it)"
    return out + "."
