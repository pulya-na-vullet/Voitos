# Push: `type` → deep link

Формат ссылки: `voitos://app/<path>`  
Payload FCM data: `type`, `title`, `body`, `entity_type`, `entity_id`, `deep_link`.

Каждое событие также пишется в inbox `GET /api/v1/notifications`.

## Must-have (резидент)

| type | Когда | deep_link |
|------|--------|-----------|
| `collection.offered` | Инвайт на сбор (снег и т.д.) | `voitos://app/collections/{id}` |
| `collection.remind_3d` | Напоминание −3д | `voitos://app/collections/{id}` |
| `collection.remind_1d` | −1д | `voitos://app/collections/{id}` |
| `collection.remind_2h` | −2ч | `voitos://app/collections/{id}` |
| `collection.progress` | Сбор пополнился | `voitos://app/collections/{id}` |
| `collection.closed` | Сбор закрыт | `voitos://app/collections/{id}` |
| `collection.surplus` | Излишек | `voitos://app/collections/{id}` |
| `volunteer.ask` | Нужна помощь на площадке/дороге | `voitos://app/volunteer/{ask_id}` |
| `work_request.assigned` | Мастер принял заявку | `voitos://app/work-requests/{id}` |
| `work_request.no_executor` | Мастера не нашли | `voitos://app/work-requests/{id}` |
| `work_request.slots_ready` | Мастер прислал окна | `voitos://app/work-requests/{id}/slots` |
| `work_request.confirm_amount` | Сколько передали исполнителю? | `voitos://app/work-requests/{id}/confirm` |
| `work_request.service_survey` | Услуга оказана? | `voitos://app/work-requests/{id}/survey` |
| `work_request.rate` | Оцените мастера | `voitos://app/work-requests/{id}/rate` |
| `subscription.receipt_approved` | Чек принят | `voitos://app/subscription` |
| `subscription.receipt_rejected` | Чек отклонён | `voitos://app/subscription` |
| `subscription.renewal_4d` | Продление −4д | `voitos://app/subscription` |
| `subscription.renewal_2d` | −2д | `voitos://app/subscription` |
| `subscription.renewal_2h` | −2ч | `voitos://app/subscription` |
| `profile.verified` | Анкета ок | `voitos://app/home` |
| `profile.incomplete` | Дозаполнить | `voitos://app/profile` |
| `wish_ballot.started` | Голосование | `voitos://app/ballots/{id}` |
| `manager_survey.started` | Опрос менеджера | `voitos://app/manager-survey/{id}` |
| `reminder.due` | Личное напоминание | `voitos://app/reminders/{id}` |

## Исполнитель

| type | deep_link |
|------|-----------|
| `work_request.offer` | `voitos://app/executor/offers/{offer_id}` |
| `work_request.offer_expired` | `voitos://app/executor/offers` |
| `work_request.schedule_master` | `voitos://app/executor/work-requests/{id}/slots` |
| `work_request.commission_ask` | `voitos://app/executor/work-requests/{id}/commission` |
| `contractor.profile_verified` | `voitos://app/executor/profile` |
| `contractor.payout` | `voitos://app/executor/payouts/{id}` |

## Правила

1. Не слать push на ответы пользователя (списки, «помощь», ввод описания).  
2. Один `type` = один экран; title/body приходят с сервера (копирайт не дублировать в app).  
3. Тап по push открывает deep_link; если сессии нет — login, затем redirect.
