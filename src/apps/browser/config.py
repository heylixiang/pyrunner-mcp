from core.config import Settings as BaseSettings


class Settings(BaseSettings):
    BROWSER_HOST: str = "localhost"
    BROWSER_PORT: int = 9222
    BROWSER_TIMEOUT: int = 30000
    BROWSER_WS_URL: str | None = None
    BROWSER_HOST_HEADER: str | None = None

    @property
    def browser_cdp_url(self) -> str:
        if self.BROWSER_WS_URL:
            return self.BROWSER_WS_URL
        return f"http://{self.BROWSER_HOST}:{self.BROWSER_PORT}"


    @property
    def browser_cdp_headers(self) -> dict[str, str] | None:
        if self.BROWSER_HOST_HEADER:
            return {"Host": self.BROWSER_HOST_HEADER}
        return None


settings = Settings()
