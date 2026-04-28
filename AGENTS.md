# Cursor Autopilot: task orchestration

Цель: по формулировке задачи определить тип, затем вызвать sub-agents в детерминированном порядке и отработать нужные skills.

Важно:
- Глобальный стандарт разработки задаётся в `~/.cursor/rules`, `~/.cursor/skills`, `~/.cursor/agents`.
- Project-level правила ниже только дополняют глобальный стандарт спецификой этого репозитория.
- Для сценарных `bugfix`/`new-feature` обязателен global skill `scenario-contract`: до кода нужен `Scenario contract` + `Flow trace`, перед “готово” нужен `Scenario proof`.

Важное замечание про “код не писать”:
- Если текущая ветка по skill запрещает код, sub-agent на этой стадии не должен создавать реализацию (только спецификация/план/контракт).

## Типы задач
- `project-bootstrap`
- `new-feature` (требует backend-изменений)
- `bugfix` (регрессия/сломанный flow с backend-изменениями)
- `refactor-safe` (локальный безопасный рефакторинг без изменения поведения)
- `design-new-screen` (UX/UI спецификация нового экрана, КОД НЕ ПИШЕМ)
- `design-system-apply` + `responsive-pass` (реализация UI по дизайну + адаптив)
- `release-prep` (готовность к merge/release значимых изменений)
- Композит: “новая фича требует новый экран/вкладку” (UI + возможно backend)

## Универсальная логика оркестрации
1. Определи тип задачи (если композит — отдельный флаг “UI+backend”).
2. Выбери chain sub-agents по типу (ниже).
3. Для каждого этапа передай список skills, которые должны быть “отработаны по шагам” этим этапом.
4. В конце запускай verifier (и/или release-guard при merge/release).

## Ветки (тип → chain sub-agents → skills)

### 1) `project-bootstrap`
Sub-agents:
1. `/solution-architect`
2. `/analyst`
3. `/planner`
4. `/verifier`
Skills:
1. `project-bootstrap`

### 2) `new-feature` (backend-изменения)
Sub-agents:
1. `/analyst`
2. `/planner`
3. `/builder`
4. `/test-engineer`
5. `/reviewer`
6. `/release-guard` (если это merge/release)
7. `/verifier`
Skills:
1. `new-feature`
2. `scenario-contract`
3. `write-tests`
4. `self-review`
5. `release-prep` (если это merge/release)

### 3) `bugfix` (backend-регрессия)
Sub-agents:
1. `/analyst`
2. `/planner`
3. `/builder`
4. `/test-engineer`
5. `/reviewer`
6. `/release-guard` (если merge/release)
7. `/verifier`
Skills:
1. `bugfix`
2. `scenario-contract`
3. `write-tests`
4. `self-review`
5. `release-prep` (если это merge/release)

### 4) `refactor-safe` (без изменения поведения)
Sub-agents:
1. `/analyst`
2. `/planner`
3. `/refactorer`
4. `/reviewer`
5. `/verifier`
Skills:
1. `refactor-safe`
2. `self-review`

### 5) `design-new-screen` (КОД НЕ ПИШЕМ)
Sub-agents:
1. `/analyst`
2. `/planner`
3. `/reviewer`
4. `/verifier`
Skills:
1. `design-new-screen`

### 6) `design-system-apply` + `responsive-pass` (реализация UI)
Sub-agents:
1. `/analyst`
2. `/planner`
3. `/frontend-ui-engineer`
4. `/reviewer`
5. `/verifier`
Skills:
1. `design-system-apply`
2. `responsive-pass`
3. `self-review`

### 7) `release-prep` (готовность к merge/release)
Sub-agents:
1. `/reviewer`
2. `/release-guard`
3. `/verifier`
Skills:
1. `release-prep`
2. `self-review`

### 8) Композит: “новая фича требует новый экран/вкладку”
Условие:
- если backend API/сервисы/данные действительно меняются — добавь backend ветку (builder/test-engineer) до release-guard
- если backend не меняется — достаточно UI ветки + тестов/верификации

Базовый композит (минимум UI-качества, как вы просили):
Sub-agents:
1. `/orchestrator`
2. `/analyst`
3. `/planner`
4. `/frontend-ui-engineer`
5. `/reviewer`
6. `/test-engineer`
7. `/release-guard` (если merge/release)
8. `/verifier`
Skills:
1. `design-new-screen`
2. `design-system-apply`
3. `responsive-pass`
4. `new-feature` (только если backend-часть действительно меняется)
5. `scenario-contract` (если есть пользовательский flow/API/фон/БД/state/очереди/UI)
6. `write-tests` (если есть backend или новые тест-кейсы)
7. `self-review`
8. `release-prep` (если merge/release)

