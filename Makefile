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
	@echo not implemented yet
	@exit 1

chaos:
	@echo not implemented yet
	@exit 1
