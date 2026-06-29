"""GitHub profile import — turn a user's public repos into Project entries.

Uses the injectable ``HTTPClient`` (so tests stay offline via FakeHTTPClient)
to call the public REST API. Forks are skipped; repos are ranked by stars then
recency; language + topics become the project's skills.
"""

from __future__ import annotations

from typing import Any

from jobhunt.http import HTTPClient, HTTPClientError, UrllibHTTPClient

_REPOS = "https://api.github.com/users/{user}/repos?sort=updated&per_page=100"


class GitHubError(Exception):
    """Raised when the GitHub API can't be reached or the user is unknown."""


class GitHubClient:
    def __init__(self, http: HTTPClient | None = None) -> None:
        self._http = http or UrllibHTTPClient()

    def fetch_repos(self, username: str) -> list[dict[str, Any]]:
        username = (username or "").strip().lstrip("@")
        if not username:
            raise GitHubError("github username is required")
        url = _REPOS.format(user=username)
        try:
            payload = self._http.get_json(url, headers={"Accept": "application/vnd.github+json"})
        except HTTPClientError as exc:
            raise GitHubError(str(exc)) from exc
        return payload if isinstance(payload, list) else []


def repos_to_projects(repos: list[dict], *, limit: int = 12) -> list[dict]:
    """Map GitHub repo dicts → JobHunt Project dicts (skip forks)."""
    real = [r for r in repos if not r.get("fork")]
    real.sort(
        key=lambda r: (r.get("stargazers_count", 0), r.get("updated_at", "")),
        reverse=True,
    )
    projects: list[dict] = []
    for r in real[:limit]:
        skills = []
        if r.get("language"):
            skills.append(str(r["language"]).lower())
        skills += [str(t).lower() for t in (r.get("topics") or [])]
        desc = (r.get("description") or "").strip()
        projects.append({
            "name": r.get("name", ""),
            "description": desc,
            "bullets": [desc] if desc else [],
            "link": r.get("html_url", ""),
            "skills": skills,
        })
    return projects
