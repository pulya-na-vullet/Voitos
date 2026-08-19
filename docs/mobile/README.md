# Voitos Mobile — план без второй бизнес-логики

Стек клиента: **Kotlin Multiplatform** (ветка `cursor/kmp-e31c`).  
Мозг: существующий Django (те же `services/`, `bot/`, `subscriptions/`).

| Слой | Роль |
|------|------|
| Django domain | правила, FSM заявок, сборы, подписка, онбординг-награда |
| `api/v1` | JSON для телефона |
| MAX bot | адаптер чата (как сейчас) |
| Push (FCM) | те же события, что `send_message` |
| KMP app | UI + токен + deep links |

Документы в этой папке:

1. [`openapi-v1.yaml`](openapi-v1.yaml) — контракт API  
2. [`stories.md`](stories.md) — стори бота → экраны  
3. [`push-deeplinks.md`](push-deeplinks.md) — `notification.type` → экран  

Скелет сервера: пакет `api/` в корне репо.  
Заготовка клиента: `mobile/`.
