import httpx

from app.core.config import get_settings

settings = get_settings()

GITHUB_API_BASE = "https://api.github.com"
GITHUB_OAUTH_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_OAUTH_TOKEN_URL = "https://github.com/login/oauth/access_token"
OAUTH_SCOPES = "repo read:user user:email"


def build_authorize_url(state: str, redirect_uri: str) -> str:
    params = httpx.QueryParams(
        {
            "client_id": settings.github_client_id,
            "redirect_uri": redirect_uri,
            "scope": OAUTH_SCOPES,
            "state": state,
        }
    )
    return f"{GITHUB_OAUTH_AUTHORIZE_URL}?{params}"


async def exchange_code_for_token(code: str, redirect_uri: str) -> str:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            GITHUB_OAUTH_TOKEN_URL,
            data={
                "client_id": settings.github_client_id,
                "client_secret": settings.github_client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        data = response.json()

    if "access_token" not in data:
        raise ValueError(f"GitHub OAuth exchange failed: {data}")

    return data["access_token"]


class GitHubClient:
    def __init__(self, access_token: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=GITHUB_API_BASE,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=15.0,
        )

    async def get_authenticated_user(self) -> dict:
        response = await self._client.get("/user")
        response.raise_for_status()
        return response.json()

    async def get_primary_email(self) -> str | None:
        response = await self._client.get("/user/emails")
        response.raise_for_status()
        for entry in response.json():
            if entry.get("primary") and entry.get("verified"):
                return entry["email"]
        return None

    async def list_repositories(self) -> list[dict]:
        response = await self._client.get(
            "/user/repos",
            params={"per_page": 100, "sort": "updated", "affiliation": "owner,collaborator"},
        )
        response.raise_for_status()
        return response.json()

    async def get_repository_by_id(self, github_id: int) -> dict:
        response = await self._client.get(f"/repositories/{github_id}")
        response.raise_for_status()
        return response.json()

    async def aclose(self) -> None:
        await self._client.aclose()
