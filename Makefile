# Dual-Loop developer workflow.
#
# Эти цели добавлены для Dual-Loop (Phase 0+). Существующие команды
# Pythia (scripts/manual_run.sh, docker compose для production) — без изменений.

.PHONY: help dev-up dev-down dev-logs dev-ps \
        fmt lint test check \
        rust-fmt rust-lint rust-test rust-check rust-build \
        proto-lint proto-gen proto-breaking \
        clean

# -------- Help --------

help: ## Показать все доступные цели
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# -------- Dev infrastructure (Dual-Loop bus + observability) --------

dev-up: ## Поднять dev-стек (Redis, NATS, Postgres, Prometheus, Grafana, Loki, Tempo)
	docker compose -f docker-compose.dev.yml up -d
	@echo ""
	@echo "Dev stack up. Endpoints:"
	@echo "  Redis:      redis://localhost:6379"
	@echo "  NATS:       nats://localhost:4222  (monitoring: http://localhost:8222)"
	@echo "  Postgres:   postgres://pythia:pythia_dev_password@localhost:5432/pythia_dev"
	@echo "  Prometheus: http://localhost:9090"
	@echo "  Grafana:    http://localhost:3000  (admin/admin, anonymous viewer enabled)"
	@echo "  Loki:       http://localhost:3100"
	@echo "  Tempo:      http://localhost:3200  (OTLP http: 4318, grpc: 4317)"

dev-down: ## Остановить dev-стек (volumes сохраняются)
	docker compose -f docker-compose.dev.yml down

dev-nuke: ## Остановить dev-стек И удалить все volumes (deletes data)
	docker compose -f docker-compose.dev.yml down -v

dev-logs: ## Логи dev-стека в реальном времени
	docker compose -f docker-compose.dev.yml logs -f

dev-ps: ## Статус контейнеров dev-стека
	docker compose -f docker-compose.dev.yml ps

# -------- Rust services --------

rust-fmt: ## cargo fmt (применить форматирование)
	cd services && cargo fmt --all

rust-fmt-check: ## cargo fmt --check (CI gate)
	cd services && cargo fmt --all -- --check

rust-lint: ## cargo clippy с -D warnings (CI gate)
	cd services && cargo clippy --workspace --all-targets -- -D warnings

rust-test: ## cargo test --workspace
	cd services && cargo test --workspace

rust-check: ## cargo check --workspace (быстрая проверка компиляции)
	cd services && cargo check --workspace --all-targets

rust-build: ## cargo build --release
	cd services && cargo build --workspace --release

# -------- Protobuf (Dual-Loop schemas) --------

proto-lint: ## buf lint
	cd schemas && buf lint

proto-breaking: ## buf breaking --against origin/main
	cd schemas && buf breaking --against '.git#branch=main,subdir=schemas'

proto-gen: ## Сгенерировать Rust + TS клиенты из .proto
	cd schemas && buf generate

# -------- Umbrella --------

fmt: rust-fmt ## Форматирование всего кода

lint: rust-lint proto-lint ## Линт всего (Rust + proto)

test: rust-test ## Все тесты (Rust)

check: rust-fmt-check rust-lint proto-lint rust-test ## Полный CI-gate локально

# -------- Cleanup --------

clean: ## Удалить build-артефакты
	cd services && cargo clean
	@echo "Cleaned services/target/"
