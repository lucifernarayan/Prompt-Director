.PHONY: up down logs test eval chaos

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f

test:
	docker compose run --rm api python -m pytest

eval:
	docker compose run -e DATABASE_URL=postgresql://postgres:postgres@postgres:5432/prompt_director_test -v .:/app --rm api python -m evals.run

chaos:
	@echo not implemented yet
	@exit 1
