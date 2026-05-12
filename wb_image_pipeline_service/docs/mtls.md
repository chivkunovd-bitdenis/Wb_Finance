# mTLS между монолитом и WB Image Pipeline

Целевой режим из дискавери: **mutual TLS** для вызовов `api` (wb-finance) → `wb_image_pipeline_service`.

## Практичный путь

1. Выпустить внутренний CA (или использовать существующий infra-CA).
2. Сервер (image pipeline): сертификат сервера + `verify_client` в TLS.
3. Клиент (монолит): client cert + приватный ключ в volume, read-only.
4. В Docker Compose — общая сеть, без публикации порта `9100` наружу; опционально front proxy (Caddy/nginx) с `ssl_verify_client`.

## Dev

Пока сертификаты не смонтированы, допустима изоляция только Docker network + `WIP_INTERNAL_HMAC_SECRET` на заголовке подписи (не замена mTLS для прода).
