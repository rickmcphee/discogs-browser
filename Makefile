BACKEND_DIR := backend
FRONTEND_DIR := frontend
PID_FILE     := /tmp/discogs-uvicorn.pid

.PHONY: dev backend frontend restart-backend stop-backend

dev:
	@$(MAKE) -j2 backend frontend

backend:
	cd $(BACKEND_DIR) && uvicorn main:app --reload --reload-dir . --port 8000

frontend:
	cd $(FRONTEND_DIR) && npm run dev

restart-backend:
	@if [ -f $(PID_FILE) ]; then \
		kill $$(cat $(PID_FILE)) 2>/dev/null || true; \
		rm -f $(PID_FILE); \
		sleep 0.5; \
	fi
	@pkill -f "uvicorn main:app" 2>/dev/null || true
	@sleep 0.5
	@cd $(BACKEND_DIR) && uvicorn main:app --reload --reload-dir . --port 8000 & echo $$! > $(PID_FILE)
	@echo "Backend restarted (pid=$$(cat $(PID_FILE)))"

stop-backend:
	@pkill -f "uvicorn main:app" 2>/dev/null || true
	@rm -f $(PID_FILE)
