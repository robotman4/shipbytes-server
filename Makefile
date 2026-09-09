.PHONY: dev test build deploy

dev:
	python -m alembic upgrade head
	python -m uvicorn shipbytes.main:create_app --factory --reload --no-access-log

test:
	docker build --target test -t shipbytes:test .
	docker run --rm --network none shipbytes:test

build:
	docker build --target production -t shipbytes:latest .

deploy:
	bash scripts/deploy.sh
