# Ozon в WB Finance Pro — сценарий и технический контракт

Дата проверки: 11 октября 2026. Задача: new-feature, UI + backend.
Владелец разрешил после анализа реализацию параллельными агентами Luna xhigh; текущий WB не ломать.
Ветка: codex/ozon-finance-20261011. База: 4fe3455ba8fd49f8af9d73fda642b1db6135997b.

## Сценарий
Владелец магазина выбирает сверху WB, Ozon или «Все маркетплейсы». WB открывает прежний интерфейс и использует прежние расчёты. Ozon показывает финансовые показатели выбранного периода, товары и расходы без товарной привязки. Общий режим складывает денежные показатели WB и Ozon, пересчитывает доли от общей выручки, сохраняет раздельную идентичность товаров. Просмотр чужого магазина возможен только через существующие права доступа; менять подключение и себестоимость может только владелец.

Человек видит выручку, комиссию, логистику, хранение, штрафы, рекламу, прочие начисления, себестоимость, налог и прибыль. Неизвестное значение показывается прочерком с причиной, а не нулём. Отсутствие подключения, незагруженный день, ошибка API и настоящий день без операций — разные состояния. Суммы разных валют нельзя складывать.

Общие расходы не разбрасываются по товарам без подтверждённого правила. Они видны отдельно и входят в результат магазина. Исходные суммы Ozon сохраняются без изменения знака. В интерфейсе расходы приводятся к принятому в WB виду: комиссия, логистика, штрафы, хранение, реклама и прочие расходы равны минус исходной сумме удержания; сторно уменьшает статью расхода. Выручка, компенсации и итог начислений сохраняют исходный знак. Сверка выполняется по исходным знакам, до преобразования для интерфейса. Например, комиссия WB 25 и удержание Ozon −10 дают расход Ozon 10 и общий расход 35. Итог каждой операции сверяется с деталями; расхождение явно видно и не может исчезнуть при взаимном погашении расхождений разных операций.

## Цепочка для разработки
Выбор marketplace + активный owner + даты → GET /dashboard/marketplace/overview → WB stored views (read only) + Ozon isolated ledger → daily/product aggregates and quality → dedicated finance view. WB default old routes remain intact.
Настройки Ozon → PUT /dashboard/marketplace/connection → encrypted credential. GET connection returns configured/client_id/status only. POST /dashboard/marketplace/sync {date_from,date_to} queues bounded job; GET status includes progress/errors. No credentials in broker payload/logs/response. No secrets managed during this task.

## Разделение ответственности
- app/services/ozon_client.py: HTTP read-only client; accrual/types, accrual/by-day, product/info/list; public contract tests.
- app/services/ozon_finance.py: pure Decimal normalization/reconciliation, exact-type mapping, unknown bucket, no SQL.
- app/models/ozon.py + additive migration: isolated connection, accrual source snapshots, sync-day coverage, product costs. No rewrites of WB tables.
- app/services/marketplace_finance_service.py: persistence, atomic replacement of completed days, overview. Missing/failed page never publishes partial day. Same accrual ID deduped per owner; changed days replace atomically. Unknown category preserved; no heuristic guesses presented as confirmed.
- app/routers/marketplace_finance.py; celery_app/ozon_tasks.py; router/task registration only changes to existing backend.
- frontend dedicated marketplace view, connection/settings component, Topbar/Layout selector. WB screens untouched; new state scope owner+marketplace+dates, stale response guard and reset on owner changes.

## API UI contract (new, WB APIs unchanged)
GET /dashboard/marketplace/overview?marketplace=ozon|all&date_from=YYYY-MM-DD&date_to=YYYY-MM-DD
Response: {marketplace,currency:"RUB",daily:[],products:[],warnings:[],coverage:{complete,missing_dates:[]},connection:{configured,status,error?}}.
Daily: {date,revenue,commission,logistics,penalties,storage,ads_spend,other_expenses,compensation,cogs,tax,operation_expenses,margin,marketplace_net,unallocated_amount,reconciliation_difference}; amounts number|null. Any uncertain COGS => cogs and margin null; missing coverage => warnings and no invented zero-profit days.
Products: {marketplace,sku:string,offer_id,name,cost_price:number|null,daily:[same daily shape]}. Identity marketplace+sku, not offer_id alone. Ozon costs PUT /dashboard/marketplace/products/{sku}/cost {cost_price}. Cost zero is explicit; unset is null.
GET /dashboard/marketplace/connection → {configured,client_id,status,error?}.
PUT same {client_id,api_key}; owner only; never echo api_key; validate encryption available, existing crypto reused.
POST /dashboard/marketplace/sync {date_from,date_to}; max 31 days per manual request, reject reverse range; owner only. response {status}. Status polled through GET connection / overview. No provider calls on GET overview.

## Official external contract, live browser verified
Source https://docs.ozon.ru/api/seller/ (live browser DOM, 2026-10-11).
POST https://api-seller.ozon.ru/v1/finance/accrual/by-day; headers Client-Id and Api-Key. Body {date:"YYYY-MM-DD",last_id:""}; next pages preserve date and use returned last_id. Cursor TTL 15 min. Earliest date 2022-01-01. Empty cursor termination needs real-account reconciliation; repeated cursor fails explicitly, empty accruals with nonempty cursor continues. Max pages bounded. 200 JSON only; 401/403 clear access error; 429 obey Retry-After/backoff bounded and expose failure; 5xx bounded retries. No deprecated v3 fallback.
Return {accruals:[{accrual_id,date,unit_number,accrued_category,total_amount:{amount:string,currency},posting?,item_fees?,non_item_fee?,container_fees?}],last_id}.
Categories POSTING|ITEM|NON_ITEM|CONTAINER_FEES|UNSPECIFIED.
POSTING posting.products[{sku,commission:{sale_amount,commission,sale_commission,bonus,coinvestment,sale_price,seller_price},delivery:{services:[{type_id,accrued}],total_accrued}}]; money objects {amount,currency}. `commission` final commission; `sale_commission` tariff commission. Do not add both. Delivery total and delivery services are alternate levels; do not add both. `sale_price` buyer price; `seller_price` per-unit price; `sale_amount` realized amount. Do not infer quantity by monetary division.
ITEM item_fees.fees[{sku,fees:[{type_id,accrued}]}]. NON_ITEM non_item_fee {type_id,accrued}. CONTAINER_FEES container_fees.fees[{type_id,accrued}]. total_amount used once as authoritative operation total, components used for allocation/reconciliation.
POST /v1/finance/accrual/types: dictionary {accrual_types:[{id,name,description}]}; cache dictionary, do not refetch per day. Numeric IDs cannot be invented.
POST /v3/product/info/list body {sku:["..."]}; <=1000 identifiers combined. response items[{id,sku,offer_id,name,...}]. No price/stock mutation methods.
POST /v1/finance/accrual/postings {posting_numbers:[1..200 strings]} returns posting_accruals[{posting_number,accruals:[{accrual_date,accrued,quantity,seller_price,sku,type_id}]}]. Supplements quantity but lacks accrual_id: matching must be proven before automatic COGS attribution.
POST /v2/finance/realization {month,year}: monthly delivered and returned quantities by item.sku/offer_id; excludes cancellations/unbought; available by next month day5; some Kazakhstan contracts unsupported. Use for reconciliation, not additive ledger entries.
POST /v1/finance/realization/by-day {day,month,year}: Premium Plus/Pro, last32 calendar days only; cannot be mandatory for all sellers.
POST /v1/finance/cash-flow-statement/list: half-month balance report; cannot replace daily per-SKU accruals.
Deprecated /v3/finance/transaction/list and totals: official docs scheduled cutoff 2026-09-08, explicitly warn cabinet mismatch. Excluded from new production path.

## UX/UI contract
Entry: existing Topbar select, default WB. Existing dates/store controls remain. Ozon/all financial screen has connection status, explicit refresh action, loading/error/empty/partial banners, financial table and per-product drilldown. Connection form in Settings; API key password field, clear after save, no persistence in browser storage. Dirty edits cancel/reset; save disabled while pending; field validation and recoverable errors. Shared-store viewer gets read-only view. Unsupported WB-only tools stay WB-only and labeled rather than showing WB data as Ozon. No automatic live sync on opening page.

## Acceptance / failure cases
WB regression totals unchanged; colliding WB nm_id and Ozon sku remain two products; cache cannot leak between owner/marketplace/date; failed sync preserves prior complete snapshot; repeated page/id no double accounting; same-operation service details never added to total twice; unknown fees visible; no quantity means unknown COGS/profit; coverage loss visible in all view; rounding Decimal to cents at output; currency mismatch blocked; owner/viewer tests; HTTP payload and cursor tests; costs repeated edits recompute; 429 finite user-visible failure.

## Полнота общего режима и собственные расходы
Общий режим требует покрытия обеих площадок за выбранные даты. Неподключённый Ozon не превращает сумму WB в полный общий итог. Исторические сохранённые данные WB можно читать независимо от наличия текущего API-ключа WB. Существующие собственные расходы относятся к прежнему учёту WB: в общем режиме они учитываются один раз; для отдельного Ozon их назначение не доказано, поэтому значение неизвестно. Изменение себестоимости пересчитывает сохранённые исходные данные без нового запроса в Ozon, но не заменяет отсутствующее подтверждённое количество товара.

## Proof boundary
Public documentation is verified. No Ozon seller credentials or live finance sample supplied yet. Accurate classification, source signs, quantity matching and full reconciliation require one real account and closed-period report. Implementation must expose this incompleteness; mock tests do not prove live Ozon parity. Production is not deployed by this task without passing verification and explicit publication decision.
