from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    secret_key: str
    admin_email: str
    admin_initial_password_hash: str
    cookie_secure: bool = True

    # Assistente (fase 5): Claude Code CLI dentro do contentor
    claude_bin: str = "claude"
    claude_code_oauth_token: str = ""
    anthropic_api_key: str = ""

    data_dir: str = "/data"
    public_url: str = "http://localhost:8010"  # endereço público (o conector MCP e o OAuth usam-no); PUBLIC_URL no .env
    author_name: str = "Autor"  # o nome do utilizador na Sala de argumentistas (AUTHOR_NAME no .env)
    session_max_age_s: int = 14 * 24 * 3600

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.data_dir}/db/director.sqlite3"

    @property
    def media_dir(self) -> str:
        return f"{self.data_dir}/media"


settings = Settings()
