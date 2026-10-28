# Smart Prompt Director

Project skeleton and infrastructure for Smart Prompt Director.

## Windows without make

| Makefile Target | PowerShell Command |
| --- | --- |
| `make up` | `docker compose up --build -d` |
| `make down` | `docker compose down` |
| `make logs` | `docker compose logs -f` |
| `make test` | `docker compose run --rm api python -m pytest` |
| `make eval` | `Write-Host "not implemented yet"; exit 1` |
| `make chaos` | `Write-Host "not implemented yet"; exit 1` |
