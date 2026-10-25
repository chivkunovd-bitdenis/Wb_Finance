# Ozon в WB Finance Pro — сценарий и технический контракт

Дата проверки: 11 октября 2026. Задача: new-feature, UI + backend.
Владелец разрешил после анализа реализацию параллельными агентами Luna xhigh; текущий WB не ломать.
Ветка: codex/ozon-finance-20261011. База: 4fe3455ba8fd49f8af9d73fda642b1db6135997b.

## Сценарий
Владелец магазина выбирает сверху WB, Ozon или «Все маркетплейсы». WB открывает прежний интерфейс и использует прежние расчёты. Ozon показывает финансовые показатели выбранного периода, товары и расходы без товарной привязки. Общий режим складывает денежные показатели WB и Ozon, пересчитывает доли от общей выручки, сохраняет раздельную идентичность товаров. Просмотр чужого магазина возможен только через существующие права доступа; менять подключение и себестоимость может только владелец.

Человек видит выручку, комиссию, эквайринг, логистику, хранение, штрафы, рекламу, прочие начисления, количество продаж, себестоимость, расчётный налог и прибыль. Неизвестное значение показывается прочерком с причиной, а не нулём. Отсутствие подключения, незагруженный день, ошибка API и настоящий день без операций — разные состояния. Суммы разных валют нельзя складывать.

Общие расходы не разбрасываются по товарам без подтверждённого правила. Они видны отдельно и входят в результат магазина. Исходные суммы Ozon сохраняются без изменения знака. В интерфейсе расходы приводятся к принятому в WB виду: комиссия, логистика, штрафы, хранение, реклама и прочие расходы равны минус исходной сумме удержания; сторно уменьшает статью расхода. Выручка, компенсации, итог начислений и сумма без товарной привязки (unallocated_amount) сохраняют исходный знак. Сверка выполняется по исходным знакам, до преобразования для интерфейса. Например, комиссия WB 25 и удержание Ozon −10 дают расход Ozon 10 и общий расход 35. Итог каждой операции сверяется с деталями; расхождение явно видно и не может исчезнуть при взаимном погашении расхождений разных операций.

## Цепочка для разработки
Выбор marketplace + активный owner + даты → GET /dashboard/marketplace/overview → WB stored views (read only) + Ozon isolated ledger → daily/product aggregates and quality → dedicated finance view. WB default old routes remain intact.
Настройки Ozon → PUT /dashboard/marketplace/connection → encrypted credential. GET connection returns configured/client_id/status only. POST /dashboard/marketplace/sync {date_from,date_to} queues bounded job; GET status includes progress/errors. No credentials in broker payload/logs/response. Existing credential of one explicitly authorized WMS seller used for read-only verification in the WMS process; no credential creation, rotation or WMS settings mutation.

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
Поле `reconciliation_difference` — сумма абсолютных расхождений отдельных начислений, а не знаковый баланс ошибок: +1 и −1 дают 2. Поэтому ошибки не гасят друг друга ни в дне, ни при суммировании периода. Направление каждого расхождения сохраняется в предупреждении; при неполных деталях итог неизвестен. Все предупреждения доступны через раскрытие списка.
Daily: {date,revenue,commission,acquiring,logistics,penalties,storage,ads_spend,other_expenses,compensation,cogs,tax,operation_expenses,margin,marketplace_net,unallocated_amount,reconciliation_difference}; amounts number|null. Quantity metrics: sold_quantity, returned_quantity, net_quantity; unknown quantity remains null. Quality booleans distinguish classified economic content, SKU allocation and eligibility for profit calculation. Any uncertain COGS => cogs and margin null; missing coverage => warnings and no invented zero-profit days.
Products: {marketplace,sku:string,offer_id,name,cost_price:number|null,daily:[same daily shape]}. Identity marketplace+sku, not offer_id alone. Ozon costs PUT /dashboard/marketplace/products/{sku}/cost {cost_price}. Cost zero is explicit; unset is null.
GET /dashboard/marketplace/connection → {configured,client_id,status,error?}.
PUT same {client_id,api_key}; owner only; never echo api_key; validate encryption available, existing crypto reused.
POST /dashboard/marketplace/sync {date_from,date_to}; max 31 days per manual request, reject reverse range; owner only. response {status}. Status polled through GET connection / overview. No provider calls on GET overview.

## Official external contract, live browser verified
Source https://docs.ozon.ru/api/seller/ (live browser DOM, 2026-10-11).
POST https://api-seller.ozon.ru/v1/finance/accrual/by-day; headers Client-Id and Api-Key. Body {date:"YYYY-MM-DD",last_id:""}; next pages preserve date and use returned last_id. Cursor TTL 15 min. Earliest date 2022-01-01. Observed real account returns empty cursor for complete single-page days; real multipage response remains unobserved; repeated cursor fails explicitly, empty accruals with nonempty cursor continues. Max pages bounded. 200 JSON only; 401/403 clear access error; 429 obey Retry-After/backoff bounded and expose failure; 5xx bounded retries. No deprecated v3 fallback.
Return {accruals:[{accrual_id,date,unit_number,accrued_category,total_amount:{amount:string,currency},posting?,item_fees?,non_item_fee?,container_fees?}],last_id}.
Categories POSTING|ITEM|NON_ITEM|CONTAINER_FEES|UNSPECIFIED.
POSTING posting.products[{sku,commission:{sale_amount,commission,sale_commission,bonus,coinvestment,sale_price,seller_price},delivery:{services:[{type_id,accrued}],total_accrued}}]; money objects {amount,currency}. `commission` final commission; `sale_commission` tariff commission. Do not add both. Delivery total and delivery services are alternate levels; do not add both. `sale_price` buyer price; `seller_price` per-unit price; `sale_amount` realized amount. The published schema omits quantity, but four observed real sales contain posting.products[].quantity. Use that explicit integer only for a confirmed sale; missing or ambiguous quantity remains unknown. Do not infer quantity by monetary division or count service quantities as sales.
ITEM item_fees.fees[{sku,fees:[{type_id,accrued}]}]. NON_ITEM non_item_fee {type_id,accrued}. CONTAINER_FEES container_fees.fees[{type_id,accrued}]. total_amount used once as authoritative operation total, components used for allocation/reconciliation.
POST /v1/finance/accrual/types: dictionary {accrual_types:[{id,name,description}]}; cache dictionary, do not refetch per day. Numeric IDs cannot be invented.
POST /v3/product/info/list body {sku:["..."]}; <=1000 identifiers combined. response items[{id,sku,offer_id,name,...}]. No price/stock mutation methods.
POST /v1/finance/accrual/postings {posting_numbers:[1..200 strings]} returns posting_accruals[{posting_number,accruals:[{accrual_date,accrued,quantity,seller_price,sku,type_id}]}]. Supplementary verification only, not additive; lacks accrual_id. Three real sale quantities matched daily quantities. Unknown return semantics still cannot drive automatic COGS reversal.
POST /v2/finance/realization {month,year}: monthly delivered and returned quantities by item.sku/offer_id; excludes cancellations/unbought; available by next month day5; some Kazakhstan contracts unsupported. Use for reconciliation, not additive ledger entries.
POST /v1/finance/realization/by-day {day,month,year}: Premium Plus/Pro, last32 calendar days only; cannot be mandatory for all sellers.
POST /v1/finance/cash-flow-statement/list: half-month balance report; cannot replace daily per-SKU accruals.
Deprecated /v3/finance/transaction/list and totals: official docs scheduled cutoff 2026-09-08, explicitly warn cabinet mismatch. Excluded from new production path.

## UX/UI contract
Release audit amendment (2026-10-12): existing WB figures remain unchanged, but WB and combined margins are explicitly preliminary because the legacy import lacks full financial operation detail. Ozon COGS uses the currently entered SKU cost for all saved dates; expose this restatement basis and label profit as a calculation, not verified historical net profit. Tax is a management estimate using the user's WB rate, not a tax return. Recognized return-related charges, including nested delivery services, preserve all money and reconciliation but block quantity/COGS/profit claims until return accounting is verified. Unknown return direction must never be inferred from a logistics fee. Regression scenarios cover sales with later return fees, nested subtotal/service shapes, and unchanged legacy WB values.

Entry: existing Topbar select, default WB. Existing dates/store controls remain. Ozon/all financial screen has connection status, explicit refresh action, loading/error/empty/partial banners, financial table and per-product drilldown. Connection form in Settings; API key password field, clear after save, no persistence in browser storage. Dirty edits cancel/reset; save disabled while pending; field validation and recoverable errors. Shared-store viewer gets read-only view. Unsupported WB-only tools stay WB-only and labeled rather than showing WB data as Ozon. No automatic live sync on opening page.

## Acceptance / failure cases
WB regression totals unchanged; colliding WB nm_id and Ozon sku remain two products; cache cannot leak between owner/marketplace/date; failed sync preserves prior complete snapshot; repeated page/id no double accounting; same-operation service details never added to total twice; unknown fees visible; no quantity means unknown COGS/profit; coverage loss visible in all view; rounding Decimal to cents at output; currency mismatch blocked; owner/viewer tests; HTTP payload and cursor tests; costs repeated edits recompute; 429 finite user-visible failure.

## Полнота общего режима и собственные расходы
Общий режим требует покрытия обеих площадок за выбранные даты. Неподключённый Ozon не превращает сумму WB в полный общий итог. Исторические сохранённые данные WB можно читать независимо от наличия текущего API-ключа WB. Существующие собственные расходы остаются в прежнем учёте WB и в общем режиме учитываются один раз. Расходы Ozon ведутся отдельно по датам; общие расходы не распределяются на товары без основания. Изменение себестоимости пересчитывает сохранённые исходные данные без нового запроса в Ozon.

Владелец прямо выбрал общую с WB ставку налога и отдельную себестоимость Ozon. GET /dashboard/marketplace/settings возвращает {tax_rate:number|null, tax_rate_source:"wb", operation_expenses_confirmed:boolean}; tax_rate читается из User.tax_rate владельца выбранного магазина. PUT этого маршрута принимает только {operation_expenses_confirmed:boolean} и не меняет ставку WB. Ставка — доля от 0 до 1, в UI проценты, она меняется через существующие общие настройки WB. При её изменении следующий GET Ozon пересчитывает налог без повторной загрузки провайдера. operation_expenses_confirmed=false по умолчанию: до подтверждения полноты внешних расходов окончательная прибыль неизвестна. PUT /dashboard/marketplace/expenses/{date} {amount:number} задаёт неотрицательную сумму внешних расходов дня. При подтверждённой полноте отсутствие записи означает нулевые расходы; иначе неизвестно. Доступ на запись только владельцу.

Расчётный налог следует принципу существующей WB-витрины: max(реализованная выручка дня, 0) × общая ставка владельца из WB, согласно его прямому выбору. Это настройка управленческого расчёта, а не определение режима налогообложения по API. Общая прибыль = итог начислений Ozon − подтверждённая себестоимость − расчётный налог − внешние расходы. Дополнительно нужны нулевая разница сверки и однозначная экономическая классификация начислений: депозит или взаимозачёт нельзя превратить в подтверждённый расход прибыли. Товарный показатель подписывается «Прибыль до общих расходов» и использует только однозначно отнесённые к SKU суммы; NON_ITEM и внешние расходы магазина не распределяются на артикулы. Неизвестные составляющие делают соответствующую прибыль неизвестной.

## Proof boundary
Public documentation and one explicitly authorized real seller account were checked. September daily accrual components and sale quantities reconcile with the monthly realization report; a further October sale and SKU-to-offer-id catalog lookup were verified. Live returns, multipage days, storage, advertising, penalties and compensation have not appeared in this sample. Exact ID+name classification uses the saved public dictionary; unknown or changed types remain visible and prevent unsupported profit claims. Tests do not extend this real-account proof to unseen events. Main and production remain unchanged until an explicit release decision.


## Исправление запуска после первого выката

Участники: владелец сервиса и выпуск приложения. Владелец запускает обновление; действующий сайт должен продолжить работу, если новая сборка не умеет подключиться к базе. Система собирает приложение, проверяет чтение базы новой сборкой до остановки старой версии, затем выполняет обычное переключение. Адрес базы и секреты остаются прежними. Обычный адрес PostgreSQL явно использует установленный драйвер psycopg2 во всех путях запуска. Явно указанный другой драйвер сохраняется.

Проверка успеха: сборка с SQLAlchemy 2.1 и обычным адресом PostgreSQL подключается установленным psycopg2; API, проверка версии схемы и Alembic используют один выбор драйвера. Проверка отказа: ошибка импорта или подключения при предварительной проверке завершает выпуск до остановки работающих сервисов.

Для разработки: DATABASE_URL → общая нормализация схемы → app.db / docker_entrypoint_api / alembic/env → create_engine; compose build → одноразовый read-only startup probe → stop/up. Интеграционная проверка CI использует обычный postgresql://, чтобы не скрывать производственную конфигурацию.


## Ввод себестоимости и видимый пересчёт

Владелец выбирает Ozon, открывает «Себестоимость», вводит цену товара и сохраняет строку. Он должен увидеть сохранённую цену и новые суммы себестоимости/прибыли в той же таблице без перезагрузки страницы. Цена применяется ко всей сохранённой истории данного товара; остальные товары и владельцы не меняются. Незаполненные цены других строк остаются в редакторе. При очистке цены себестоимость и зависимая прибыль снова неизвестны. Если собственные расходы не подтверждены, итоговая прибыль магазина остаётся неизвестной даже при известной товарной себестоимости.

Обработка: сохранение цены → пересчёт сохранённых начислений без повторного запроса к Ozon → перечитывание отчёта → обновление видимых сумм. Ошибка сохранения не должна показывать успех; ошибка перечитывания должна позволить повторить чтение и сообщить, что цена уже сохранена. Исчезновение причины неизвестности должно убрать соответствующее устаревшее предупреждение.

Для разработки: /costs + marketplace=ozon → PUT /dashboard/marketplace/products/{sku}/cost → set_product_cost/recompute_saved_ozon_costs → GET overview → ProductsTable. Проверки: браузерный stateful-сценарий изменения значений и сохранения соседнего черновика; PostgreSQL + настоящий нормализатор для изменения/очистки цены; WB и прочие владельцы неизменны.

Цена и пересчитанные суммы сохраняются одной транзакцией: при ошибке остаются прежними и цена, и отчёт. Во время ожидающей/работающей синхронизации ввод цены получает понятный отказ без изменения данных; общая блокировка владельца исключает перезапись отчёта конкурирующей загрузкой. Проверки дополнены ошибкой нормализатора и конфликтом синхронизации.

- Если во время сохранения изменились владелец, площадка, период или вкладка, запоздавший ответ не изменяет новый экран и не блокирует его загрузку.

## Решения владельца от 2026-10-25

Эти решения заменяют соответствующие правила выше для дашборда, артикулов, себестоимости и сводного экрана. Остальные правила (знаки, сверка, дедупликация, налог по ставке WB) не меняются.

1. Неизвестная деталь не обнуляет день. Выручка, комиссия, логистика и прочие начисления дня равны сумме известных начислений. Отсутствие одной детали продажи (например, блока комиссии у одного товара) отмечает только эту деталь. Поле остаётся неизвестным, только если ни одно начисление дня его не дало. Себестоимость, маржа и ROI дня считаются по товарам, у которых себестоимость известна; доля выручки с известной себестоимостью показывается в подписи. Прежнее правило «любая неопределённая себестоимость → себестоимость и маржа дня null» для этих экранов больше не применяется.

2. Возвраты возвращаются на склад и продаются повторно. Себестоимость товара за день равна себестоимости единицы, умноженной на (продано − возвращено). Возвращённые единицы берутся из количества операции возврата (posting.products[].quantity, где seller_price отрицательна; подтверждённый пример: seller_price −4900, комиссия возвращена +2548, итог −2352, количество 1, схема FBO). День или товар с возвратом не считается неизвестным. Если сбор или услуга возврата есть, а количества возврата в операциях нет, возвращённые единицы неизвестны, и себестоимость и прибыль этого товара остаются неизвестными.

3. Маржа не ждёт подтверждения расходов. Маржа = выручка − начисления Ozon − себестоимость − налог − операционные расходы, внесённые за день. День без записи расходов считается нулём, как в WB. Галочка «полнота операционных расходов подтверждена» больше не влияет на маржу и убрана из настроек; в подписи карточки указываются внесённые расходы.

4. Хранение и реклама: если в выгрузке Ozon за день нет таких начислений, показывается ноль, как в WB. Наличие начислений проверяется по видам начислений, а не предполагается.
