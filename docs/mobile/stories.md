# Стори бота → экраны KMP

Источник: `ai/intent.py`, `bot/pipeline.py`, schedulers.

## Резидент (приоритет v1)

| ID | Стори | Экран / flow | Push |
|----|--------|--------------|------|
| R03 | Анкета | `ProfileOnboarding` | да — verify / incomplete |
| R04 | 5 комиксов | `OnboardingComics` | нет |
| R05 | Статус подписки | `Subscription` | нет |
| R06 | Чек подписки | `Subscription` + camera | да — approve/reject |
| R07 | Grace / blocked | `Paywall` | да — pressure |
| R08 | Напоминание продления | — | да −4д/−2д/−2ч |
| R09–R12 | Сборы (снег и т.д.) | `Collections`, `CollectionDetail` | **да** invite/remind/progress |
| R13 | Чек: подписка или сбор | `ReceiptDestination` | да — результат |
| R14 | Волонтёр да/нет | `VolunteerAsk` | **да** |
| R16–R17 | Пожелания | `Wishes` | нет |
| R18 | Голосование | `WishBallot` | да — старт |
| R19 | Опрос менеджера | `ManagerSurvey` | да — старт |
| R20 | Вызов мастера | `NewWorkRequest` | — |
| R21 | Мастер назначен | `WorkRequestDetail` | **да** |
| R22 | Мастера нет | `WorkRequestDetail` | да |
| R23 | Выбор слота | `PickSlot` | да — слоты готовы |
| R24 | Сколько передали? | `ConfirmAmount` | **да** |
| R25–R26 | Опрос / оценка | `RateMaster` | да |
| R29 | Напоминания | `Reminders` | да в срок |
| R30–R32 | Задачи / память / чат | later | нет |

## Исполнитель (v1.1, та же app)

| ID | Стори | Экран | Push |
|----|--------|-------|------|
| C01 | Регистрация | `ExecutorSignup` | да — verify |
| C03 | Оффер заявки | `ExecutorOffer` | **да** |
| C04 | Слоты на дому | `PublishSlots` | да |
| C05 | Заявка выполнена | `CompleteJob` | — |
| C06 | Комиссия 10% | `PayCommission` | **да** |

## Pending kinds → экран (не чат)

`registration`, `comic_onboarding`, `work_request`, `work_request_client_confirm`,  
`work_request_schedule_client`, `work_request_rating`, `work_request_service_survey`,  
`service_invite_pick`, `volunteer_help_reply`, `wish_ballot_vote`, `manager_survey`,  
`work_request_offer_reply`, `work_request_commission`, `work_request_schedule_master`, …
