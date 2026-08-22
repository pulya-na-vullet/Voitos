from django.urls import path

from api import auth_views, views

app_name = "api"

urlpatterns = [
    path("health", views.health, name="health"),
    path("auth/config", auth_views.auth_config, name="auth_config"),
    path("auth/phone/start", auth_views.phone_start, name="auth_phone_start"),
    path("auth/phone/verify", auth_views.phone_verify, name="auth_phone_verify"),
    path("auth/phone/login-request", auth_views.phone_login_request, name="auth_phone_login_request"),
    path("auth/phone/login-verify", auth_views.phone_login_verify, name="auth_phone_login_verify"),
    path("auth/max/start", auth_views.max_start, name="auth_max_start"),
    path("auth/max/verify", auth_views.max_verify, name="auth_max_verify"),
    path("auth/pin/set", auth_views.pin_set, name="auth_pin_set"),
    path("auth/pin/login", auth_views.pin_login, name="auth_pin_login"),
    path("auth/pin/reset-request", auth_views.pin_reset_request, name="auth_pin_reset_request"),
    path("auth/pin/reset-confirm", auth_views.pin_reset_confirm, name="auth_pin_reset_confirm"),
    path("auth/pin/change-request", auth_views.pin_change_request, name="auth_pin_change_request"),
    path("auth/pin/change-confirm", auth_views.pin_change_confirm, name="auth_pin_change_confirm"),
    path("auth/logout", auth_views.logout, name="auth_logout"),
    path("devices", auth_views.register_device, name="devices"),
    path("me", views.me, name="me"),
    path("me/avatar", views.me_avatar, name="me_avatar"),
    path("me/access", views.me_access, name="me_access"),
    path("me/subscription", views.me_subscription, name="me_subscription"),
    path("me/receipts", views.me_receipts, name="me_receipts"),
    path("me/feedback", views.me_feedback, name="me_feedback"),
    path("me/wishes", views.me_wishes, name="me_wishes"),
    path("groups", views.groups_list, name="groups"),
    path("groups/<int:group_id>/messages", views.group_messages, name="group_messages"),
    path("groups/<int:group_id>/read", views.group_mark_read, name="group_mark_read"),
    path("executor-roles", views.executor_roles, name="executor_roles"),
    path("work-requests", views.work_requests_list, name="work_requests"),
    path("work-requests/<int:pk>", views.work_request_detail, name="work_request_detail"),
    path(
        "work-requests/<int:pk>/confirm-amount",
        views.work_request_confirm_amount,
        name="work_request_confirm",
    ),
    path(
        "work-requests/<int:pk>/confirm-slot",
        views.work_request_confirm_slot,
        name="work_request_confirm_slot",
    ),
    path(
        "work-requests/<int:pk>/photos",
        views.work_request_add_photo,
        name="work_request_photos",
    ),
    path(
        "work-requests/<int:pk>/submit",
        views.work_request_submit,
        name="work_request_submit",
    ),
    path(
        "work-requests/<int:pk>/cancel",
        views.work_request_cancel,
        name="work_request_cancel",
    ),
    path(
        "work-requests/<int:pk>/cancel/",
        views.work_request_cancel,
        name="work_request_cancel_slash",
    ),
    path("me/executor", views.me_executor, name="me_executor"),
    path("executor/register", views.executor_register, name="executor_register"),
    path("executor/offers", views.executor_offers, name="executor_offers"),
    path(
        "executor/offers/<int:pk>/respond",
        views.executor_offer_respond,
        name="executor_offer_respond",
    ),
    path("collections", views.collections_list, name="collections"),
    path("collections/<int:pk>", views.collection_detail, name="collection_detail"),
    path(
        "collections/<int:pk>/receipt",
        views.collection_receipt,
        name="collection_receipt",
    ),
    path("onboarding", views.onboarding, name="onboarding"),
    path(
        "onboarding/steps/<slug:code>/complete",
        views.onboarding_complete_step,
        name="onboarding_step",
    ),
    path("notifications", views.notifications_list, name="notifications"),
    path(
        "notifications/<int:pk>/read",
        views.notification_read,
        name="notification_read",
    ),
]
